"""Identificação e extração das seções do artigo.

Classificação de cabeçalhos Markdown (prefixo exato + fuzzy), extração dos
blocos, fallbacks posicionais (abstract/conclusão) e o reforço opcional via
IA local (Ollama).
"""

import json
import os
import re
import urllib.error
import urllib.request
from functools import lru_cache

from config import (OLLAMA_MODELO_PADRAO, OLLAMA_TIMEOUT_S, OLLAMA_URL_PADRAO,
                    _get_setting)


_NUM_PREFIX_RE = re.compile(
    r'^[\d]+(?:\.[\d]+)*\.?\s*\|\s*'                      # "1 | ", "2.3 | "
    r'|^[\d]+(?:\.[\d]+)*\.?\s+'                           # "1.", "2.1.", "1 "
    r'|^[A-Z]\.\s+'                                        # "A. "
    r'|^(?:II|III|IV|VI{0,3}|IX|XI{0,2}|XII)\.?\s+'      # "II.", "III.", "IV." …
)

_FUZZY_LIMIAR    = 0.85


# Palavras fortes: inequívocas, verificadas por "começa com" (degrau 1)
_SECAO_STRONG_PREFIXES = {
    'abstract':    ['abstract', 'resumo', 'summary'],
    'introducao':  ['introduction', 'introdução', 'background'],
    'metodos':     ['methods', 'methodology', 'materials', 'métodos', 'materiais'],
    'resultados':  ['results', 'resultados', 'findings'],
    'discussao':   ['discussion', 'discussão'],
    'conclusao':   ['conclusion', 'conclusions', 'conclusão', 'conclusões'],
    'referencias': ['references', 'bibliography', 'referências', 'referencia'],
    'suplementar': ['supporting', 'supplementary', 'supplemental', 'appendix', 'appendices'],
}

# Todas as palavras-chave como strings simples, usadas no fuzzy (degrau 2)
_SECAO_ALL_KEYWORDS = {
    'abstract':    ['abstract', 'resumo', 'summary'],
    'introducao':  ['introduction', 'introdução', 'background', 'overview', 'context'],
    'metodos':     ['methods', 'materials and methods', 'methodology', 'métodos',
                    'materiais e métodos', 'experimental procedures', 'study design',
                    'patients and methods'],
    'resultados':  ['results', 'resultados', 'findings', 'outcomes'],
    'discussao':   ['discussion', 'discussão', 'interpretation', 'analysis'],
    'conclusao':   ['conclusions', 'conclusion', 'concluding remarks', 'conclusões', 'conclusão',
                    'summary and conclusions', 'final remarks', 'perspectives', 'closing remarks'],
    'referencias': ['references', 'bibliography', 'literature cited', 'works cited',
                    'referências', 'referencia'],
    'suplementar': ['supporting information', 'supplementary material', 'supplementary data',
                    'supplementary methods', 'supplemental', 'appendix', 'appendices',
                    'material suplementar'],
}

# Cabeçalhos compostos que mapeiam para múltiplas seções simultaneamente
_SECAO_COMPOSTOS = [
    ('results and discussion',           frozenset({'resultados', 'discussao'})),
    ('results, discussion and conclusions', frozenset({'resultados', 'discussao', 'conclusao'})),
    ('discussion and conclusions',       frozenset({'discussao', 'conclusao'})),
    ('discussion and conclusion',        frozenset({'discussao', 'conclusao'})),
    ('conclusions and future work',      frozenset({'conclusao'})),
    ('conclusion and future work',       frozenset({'conclusao'})),
    ('conclusions and perspectives',     frozenset({'conclusao'})),
    ('conclusion and perspectives',      frozenset({'conclusao'})),
    ('background and objectives',        frozenset({'introducao'})),
    ('background and aims',              frozenset({'introducao'})),
    ('aims and objectives',              frozenset({'introducao'})),
    ('aims and methods',                 frozenset({'introducao', 'metodos'})),
    ('materials and methods',            frozenset({'metodos'})),
    ('patients and methods',             frozenset({'metodos'})),
    ('subjects and methods',             frozenset({'metodos'})),
]


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _cabecalho_nivel(linha):
    """Retorna (nivel, texto_limpo) se linha é cabeçalho Markdown, ou None."""
    m = re.match(r'^(#{1,6})\s+(.+)', linha)
    if not m:
        return None
    nivel = len(m.group(1))
    texto = m.group(2).strip()
    # 1. Remove formatação Markdown: **, *, __, _, `
    texto = re.sub(r'[*_`]+', '', texto)
    # 2. Remove prefixo numérico (inclusive variante com pipe: "1 |", "2.3 |")
    texto = _NUM_PREFIX_RE.sub('', texto)
    # 3. Normaliza espaços
    texto = re.sub(r'\s+', ' ', texto).strip()
    return nivel, texto


@lru_cache(maxsize=512)
def _classificar_cabecalho(texto):
    """Classifica cabeçalho. Retorna str, frozenset (composto) ou None."""
    import difflib
    texto_l = texto.lower().strip()
    if not texto_l:
        return None

    # Degrau 0: cabeçalhos compostos (exato + fuzzy leve)
    for padrao, secoes in _SECAO_COMPOSTOS:
        if texto_l == padrao:
            return secoes
        if difflib.SequenceMatcher(None, texto_l, padrao).ratio() >= _FUZZY_LIMIAR:
            return secoes

    # Degrau 1: prefixo exato de palavra forte (inequívoca)
    for key, prefixes in _SECAO_STRONG_PREFIXES.items():
        for prefix in prefixes:
            if texto_l.startswith(prefix):
                return key

    # Degrau 2: fuzzy contra todas as palavras-chave
    for key, keywords in _SECAO_ALL_KEYWORDS.items():
        for kw in keywords:
            ratio = difflib.SequenceMatcher(None, texto_l, kw).ratio()
            if ratio >= _FUZZY_LIMIAR:
                return key

    return None



def _md_remover_secoes(md, secoes_a_remover):
    """Remove blocos de seções do Markdown (usado no fallback sem duplicação)."""
    if not secoes_a_remover:
        return md
    linhas = md.splitlines()
    cabecalhos = []
    for idx_l, linha in enumerate(linhas):
        r = _cabecalho_nivel(linha)
        if r:
            nivel, texto = r
            cabecalhos.append((idx_l, nivel, _classificar_cabecalho(texto)))
    excluir = set()
    for i, (idx_l, nivel, chave) in enumerate(cabecalhos):
        remover = (isinstance(chave, frozenset) and bool(chave & secoes_a_remover)
                   or isinstance(chave, str) and chave in secoes_a_remover)
        if remover:
            end = len(linhas)
            for j in range(i + 1, len(cabecalhos)):
                if cabecalhos[j][1] <= nivel:
                    end = cabecalhos[j][0]
                    break
            excluir.update(range(idx_l, end))
    return '\n'.join(l for i, l in enumerate(linhas) if i not in excluir).strip()


def _e_review(caminho, md):
    """Detecta se o artigo é uma review por metadados, nome do arquivo ou heurística."""
    # 1. Metadados internos do PDF
    try:
        import fitz
        doc = fitz.open(caminho)
        meta = doc.metadata or {}
        doc.close()
        campos = (meta.get('type', '') + ' ' + meta.get('subject', '')).lower()
        if 'review' in campos:
            return True
    except Exception:
        pass
    # 2. Nome do arquivo
    if 'review' in os.path.basename(caminho).lower():
        return True
    # 3. Heurística: ausência de Methods E Results — sinal forte de review
    tem_metodos = tem_resultados = False
    for linha in md.splitlines():
        r = _cabecalho_nivel(linha)
        if r:
            chave = _classificar_cabecalho(r[1])
            if chave == 'metodos':
                tem_metodos = True
            elif chave == 'resultados':
                tem_resultados = True
    return not tem_metodos and not tem_resultados


def _abstract_por_posicao(md):
    """Captura texto antes do primeiro cabeçalho de seção conhecida como abstract.

    Cobre papers onde o abstract aparece como parágrafo corrido sem cabeçalho.
    Ignora cabeçalhos de nível 1 (título do artigo).
    """
    linhas = md.splitlines()
    pre = []
    for linha in linhas:
        r = _cabecalho_nivel(linha)
        if r:
            nivel, texto = r
            if _classificar_cabecalho(texto) is not None:
                break        # primeira seção conhecida — para aqui
            if nivel == 1:
                continue     # título do artigo — pula sem incluir
        pre.append(linha)

    texto = re.sub(r'\n{3,}', '\n\n', '\n'.join(pre)).strip()
    if len(texto) >= 100:
        return texto[:3000]
    return ''


# Frases que sinalizam o início de um parágrafo de conclusão sem cabeçalho
_CONCLUSAO_FRASE_RE = re.compile(
    r'^\s*(?:In\s+conclusion[,\s]|In\s+summary[,\s]|To\s+conclude[,\s]'
    r'|To\s+summarize[,\s]|Taken\s+together[,\s]|Collectively[,\s]'
    r'|Overall[,\s]|In\s+closing[,\s]'
    r'|Em\s+conclus[aã]o[,\s]|Em\s+resumo[,\s]|Concluindo[,\s])',
    re.IGNORECASE
)


def _conclusao_por_frase(md):
    """Extrai conclusão por frase-sinal no último terço do artigo.

    Cobre papers sem cabeçalho ## Conclusions onde a conclusão aparece como
    "In conclusion, ..." embutido no final da Discussion.
    """
    linhas = md.splitlines()
    n = len(linhas)
    inicio = max(0, int(n * 0.60))   # busca só no último 40%

    resultado = []
    dentro = False

    for i in range(inicio, n):
        linha = linhas[i]
        l = linha.strip()

        if _cabecalho_nivel(linha):
            if dentro:
                break         # novo cabeçalho encerra o bloco
            resultado = []
            dentro = False
            continue

        if not dentro and _CONCLUSAO_FRASE_RE.match(l):
            dentro = True
            resultado = [l]
        elif dentro:
            if not l and resultado:
                # Linha em branco: ver se o próximo parágrafo continua
                # (acumulamos uma linha vazia e seguimos — interrompemos
                #  só na próxima linha em branco ou no cabeçalho)
                resultado.append('')
            elif l:
                resultado.append(l)
            else:
                break   # segunda linha em branco consecutiva = fim

    # Limpar linhas em branco finais
    while resultado and not resultado[-1]:
        resultado.pop()

    if resultado:
        texto = '\n'.join(resultado).strip()
        if len(texto) >= 100:
            return texto[:2500]
    return ''


def _extrair_secoes(md, secoes_cfg):
    """Extrai seções do Markdown com suporte a cabeçalhos compostos.

    Retorna (encontradas, nao_encontradas, partes) onde partes é lista de strings
    '[SECAO]\\nconteúdo'. Compostos são extraídos uma única vez, sem duplicação.
    """
    linhas = md.splitlines()
    cabecalhos = []  # (idx_linha, nivel, chave_classificada)
    for idx_l, linha in enumerate(linhas):
        r = _cabecalho_nivel(linha)
        if r:
            nivel, texto = r
            cabecalhos.append((idx_l, nivel, _classificar_cabecalho(texto)))

    def _bloco(cab_idx):
        start = cabecalhos[cab_idx][0]
        nivel = cabecalhos[cab_idx][1]
        end   = len(linhas)
        for j in range(cab_idx + 1, len(cabecalhos)):
            if cabecalhos[j][1] <= nivel:
                end = cabecalhos[j][0]
                break
        return '\n'.join(linhas[start + 1:end]).strip()

    secoes_pedidas = set(secoes_cfg)
    encontradas    = []
    nao_encontradas = []
    partes         = []
    ja_usados      = set()  # índices de cabeçalhos já extraídos

    for sec in secoes_cfg:
        if sec in encontradas:
            continue  # coberto por composite anterior

        for cab_idx, (_, nivel, chave) in enumerate(cabecalhos):
            if cab_idx in ja_usados:
                continue
            if isinstance(chave, frozenset):
                if sec not in chave:
                    continue
                cobradas = chave & secoes_pedidas
            elif chave == sec:
                cobradas = {sec}
            else:
                continue

            conteudo = _bloco(cab_idx)
            if conteudo:
                # Mudança 5: limpar marcadores de imagem residuais no conteúdo
                conteudo = re.sub(r'^==>.*?<==[ \t]*$', '', conteudo, flags=re.MULTILINE)
                conteudo = re.sub(r'\n{3,}', '\n\n', conteudo).strip()
            if conteudo:
                label = ('+'.join(s.upper() for s in sorted(cobradas))
                         if len(cobradas) > 1 else sec.upper())
                partes.append(f'[{label}]\n{conteudo}')
                ja_usados.add(cab_idx)
                for s in cobradas:
                    if s not in encontradas:
                        encontradas.append(s)
                break
        else:
            # Cabeçalho não encontrado — fallbacks posicionais
            if sec == 'abstract' and sec not in encontradas:
                conteudo = _abstract_por_posicao(md)
                if conteudo:
                    partes.append(f'[ABSTRACT]\n{conteudo}')
                    encontradas.append('abstract')
                    continue
            if sec == 'conclusao' and sec not in encontradas:
                conteudo = _conclusao_por_frase(md)
                if conteudo:
                    partes.append(f'[CONCLUSAO]\n{conteudo}')
                    encontradas.append('conclusao')
                    continue
            if sec not in encontradas:
                nao_encontradas.append(sec)

    return encontradas, nao_encontradas, partes


def _extrair_secoes_ia(md_texto, secoes):
    """Pergunta a um modelo local (Ollama) onde cada seção não encontrada começa.

    Retorna {secao: trecho_inicial_literal, ...} com o que o modelo respondeu,
    ou {} se o Ollama estiver indisponível, der erro ou estourar o timeout —
    nesses casos o chamador deve seguir para o fallback antigo normalmente.
    """
    if not secoes:
        return {}

    url    = _get_setting('ollama_url', OLLAMA_URL_PADRAO).rstrip('/')
    modelo = _get_setting('ollama_modelo', OLLAMA_MODELO_PADRAO)
    nomes  = ', '.join(secoes)

    prompt = (
        'Você recebe abaixo o texto (Markdown) de um artigo científico. '
        f'Localize onde começa o CORPO de cada uma destas seções: {nomes}.\n'
        'Para cada seção que encontrar, copie EXATAMENTE (sem parafrasear, '
        'sem resumir) a primeira frase do conteúdo da seção — não o título/'
        'cabeçalho, o texto logo depois dele. Se não encontrar uma seção, '
        'simplesmente não a inclua na resposta.\n'
        'Responda apenas com um objeto JSON no formato '
        '{"nome_da_secao": "trecho inicial literal"}.\n\n'
        f'TEXTO:\n{md_texto[:15000]}'
    )
    payload = {
        'model': modelo,
        'prompt': prompt,
        'format': 'json',
        'stream': False,
        'options': {'temperature': 0, 'num_ctx': 8192},
    }

    try:
        req = urllib.request.Request(
            f'{url}/api/generate',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
            corpo = json.loads(resp.read().decode('utf-8'))
        marcadores = json.loads(corpo.get('response', '{}'))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return {}

    if not isinstance(marcadores, dict):
        return {}
    return {k: v for k, v in marcadores.items()
            if k in secoes and isinstance(v, str) and v.strip()}


def _fallback_secoes_ia(md, secoes_faltando):
    """Tenta recuperar seções que o regex/fuzzy não encontraram, via IA local.

    Retorna (partes, encontradas) no mesmo formato de _extrair_secoes.
    Localiza no texto original o trecho literal apontado pela IA; seções
    cujo trecho não é encontrado (IA "inventou" ou parafraseou) são ignoradas
    e continuam no fallback antigo.
    """
    marcadores = _extrair_secoes_ia(md, secoes_faltando)
    if not marcadores:
        return [], []

    # Posições de cabeçalhos Markdown reais, para não deixar o conteúdo de
    # uma seção vazar para dentro do título da seção seguinte.
    pos_cabecalhos = [m.start() for m in re.finditer(r'^#{1,6}\s+.+$', md, re.MULTILINE)]

    posicoes = []
    for secao, trecho in marcadores.items():
        trecho = trecho.strip()
        idx = md.find(trecho)
        if idx < 0:
            idx = md.lower().find(trecho.lower())
        if idx >= 0:
            posicoes.append((idx, secao))
    posicoes.sort()

    partes, encontradas = [], []
    for i, (idx, secao) in enumerate(posicoes):
        fim = posicoes[i + 1][0] if i + 1 < len(posicoes) else len(md)
        prox_cabecalho = next((p for p in pos_cabecalhos if p > idx), len(md))
        fim = min(fim, prox_cabecalho)
        conteudo = md[idx:fim].strip()
        if conteudo:
            partes.append(f'[{secao.upper()} — via IA local]\n{conteudo}')
            encontradas.append(secao)
    return partes, encontradas
