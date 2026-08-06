"""Identificação e extração das seções do artigo.

Classificação de cabeçalhos Markdown (prefixo exato + fuzzy), extração dos
blocos, fallbacks posicionais (abstract/conclusão) e o reforço opcional via
IA local (Ollama).
"""

import json
import os
import re
from functools import lru_cache

import ollama_bridge
from config import (OLLAMA_KEEP_ALIVE, OLLAMA_MODELO_PADRAO, OLLAMA_TIMEOUT_S,
                    OLLAMA_URL_PADRAO, _get_setting)
from log import logger


_NUM_PREFIX_RE = re.compile(
    r'^[\d]+(?:\.[\d]+)*\.?\s*\|\s*'                      # "1 | ", "2.3 | "
    r'|^[\d]+(?:\.[\d]+)*\.?\s+'                           # "1.", "2.1.", "1 "
    r'|^[A-Z]\.\s+'                                        # "A. "
    r'|^(?:II|III|IV|VI{0,3}|IX|XI{0,2}|XII)\.?\s+'      # "II.", "III.", "IV." …
)

_FUZZY_LIMIAR    = 0.85

# Cabeçalhos que encerram o corpo do artigo. Não são seções extraíveis, mas
# precisam interromper a seção anterior — senão, num PDF de hierarquia plana,
# a discussão acabaria engolindo agradecimentos, financiamento e afins.
_FIM_CORPO_RE = re.compile(
    r'^(acknowledg|author contribution|author information|authors?$|'
    r'funding|financial support|grant|conflict of interest|'
    r'competing interest|declaration of competing|declaration of interest|'
    r'data availability|availability of data|credit authorship|'
    r'associated content|abbreviations|notes$|disclosure|'
    r'ethics|consent|orcid|corresponding author|publisher|open access|'
    r'additional information|supporting information)', re.I)


def _chaves_secao(chave):
    """Normaliza o retorno de _classificar_cabecalho: str, frozenset ou None."""
    if isinstance(chave, frozenset):
        return set(chave)
    return {chave} if chave else set()


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
                    'patients and methods', 'experimental section'],
    'resultados':  ['results', 'resultados', 'findings', 'outcomes'],
    # 'analysis' saiu: cabeçalho "Analysis" costuma ser subseção de métodos
    # ("Statistical Analysis"), não a discussão do artigo.
    'discussao':   ['discussion', 'discussão', 'interpretation'],
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
    # Precisa vir como composto: 'summary' é prefixo forte de abstract e o
    # degrau 1 casaria antes, mandando a conclusão para o abstract.
    ('summary and conclusions',          frozenset({'conclusao'})),
    ('summary and conclusion',           frozenset({'conclusao'})),
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
    # 1b. Colchetes que o conversor insere em torno de trechos e bullets
    #     decorativos de revista ("■[METHODS][AND][MATERIALS]") escondiam o
    #     cabeçalho do classificador. Vira espaço para não colar as palavras.
    texto = re.sub(r'[\[\]]+', ' ', texto)
    texto = re.sub(r'^[^\w]+', '', texto)
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
    return _review_com_motivo(caminho, md)[0]


def _review_com_motivo(caminho, md):
    """(é_review, motivo). O motivo diz qual regra decidiu — vai para o log."""
    # 1. Metadados internos do PDF
    try:
        import fitz
        doc = fitz.open(caminho)
        meta = doc.metadata or {}
        doc.close()
        campos = (meta.get('type', '') + ' ' + meta.get('subject', '')).lower()
        if 'review' in campos:
            return True, 'metadados do PDF contem "review"'
    except Exception:
        pass
    # 2. Nome do arquivo
    if 'review' in os.path.basename(caminho).lower():
        return True, 'nome do arquivo contem "review"'
    # 3. Heurística: ausência de Methods E Results — sinal forte de review
    tem_metodos = tem_resultados = False
    for linha in md.splitlines():
        r = _cabecalho_nivel(linha)
        if not r:
            continue
        # Cabeçalho composto ("Materials and Methods", "Results and Discussion")
        # volta como frozenset, não string — comparar direto com str dava sempre
        # falso e mandava o artigo inteiro para o caminho de review.
        chaves = _chaves_secao(_classificar_cabecalho(r[1]))
        if 'metodos' in chaves:
            tem_metodos = True
        if 'resultados' in chaves:
            tem_resultados = True
    if not tem_metodos and not tem_resultados:
        return True, 'heuristica: nenhum cabecalho de metodos nem de resultados'
    return False, 'tem metodos=%s resultados=%s' % (tem_metodos, tem_resultados)


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


def diagnostico_estrutura(md, max_amostra=12):
    """Resumo da estrutura de cabeçalhos do artigo, para o log de diagnóstico.

    O que dá para descobrir depois, lendo o log: se a conversão produziu
    cabeçalhos (n=0 significa PDF que virou texto corrido), se a hierarquia
    saiu plana (o conversor costuma achatar tudo num nível só, o que já
    causou seções inteiras serem perdidas), o que foi reconhecido e quais
    cabeçalhos ficaram sem classificação — a lista de onde saem as lacunas
    de vocabulário.
    """
    niveis, classificados, nao_classificados = [], {}, []
    for linha in md.splitlines():
        r = _cabecalho_nivel(linha)
        if not r:
            continue
        nivel, texto = r
        niveis.append(nivel)
        chaves = _chaves_secao(_classificar_cabecalho(texto))
        if chaves:
            for k in chaves:
                classificados[k] = classificados.get(k, 0) + 1
        elif texto and not _FIM_CORPO_RE.match(texto):
            nao_classificados.append(texto[:60])
    return {
        'cabecalhos': len(niveis),
        'plana': len(set(niveis)) <= 1 and len(niveis) > 2,
        'niveis': sorted(set(niveis)),
        'classificados': classificados,
        'nao_classificados': nao_classificados[:max_amostra],
        'total_nao_classificados': len(nao_classificados),
    }


def _indexar_cabecalhos(md):
    """(linhas, cabecalhos) com cabecalhos = [(idx_linha, nivel, chave, texto)].

    Índice compartilhado pelo caminho do regex e pelo da IA local, para que os
    dois delimitem as seções exatamente da mesma forma.
    """
    linhas = md.splitlines()
    cabecalhos = []
    for idx_l, linha in enumerate(linhas):
        r = _cabecalho_nivel(linha)
        if r:
            nivel, texto = r
            cabecalhos.append((idx_l, nivel, _classificar_cabecalho(texto), texto))
    return linhas, cabecalhos


def _bloco_de(linhas, cabecalhos, cab_idx):
    """Conteúdo da seção aberta pelo cabeçalho cabecalhos[cab_idx]."""
    start = cabecalhos[cab_idx][0]
    nivel = cabecalhos[cab_idx][1]
    end   = len(linhas)
    for j in range(cab_idx + 1, len(cabecalhos)):
        if cabecalhos[j][1] <= nivel:
            end = cabecalhos[j][0]
            break
    conteudo = '\n'.join(linhas[start + 1:end]).strip()
    if conteudo:
        return conteudo

    # Bloco vazio: o conversor achatou a hierarquia (na prática a maioria
    # dos PDFs sai com todos os cabeçalhos no mesmo nível), então a seção
    # terminou no próprio subtítulo dela. Reabre até o próximo cabeçalho
    # que seja outra seção conhecida ou o fim do corpo do artigo.
    proprias = _chaves_secao(cabecalhos[cab_idx][2])
    end = len(linhas)
    for j in range(cab_idx + 1, len(cabecalhos)):
        idx_j, _, chave_j, texto_j = cabecalhos[j]
        if _chaves_secao(chave_j) - proprias or _FIM_CORPO_RE.match(texto_j):
            end = idx_j
            break
    return '\n'.join(linhas[start + 1:end]).strip()


def _extrair_secoes(md, secoes_cfg):
    """Extrai seções do Markdown com suporte a cabeçalhos compostos.

    Retorna (encontradas, nao_encontradas, partes) onde partes é lista de strings
    '[SECAO]\\nconteúdo'. Compostos são extraídos uma única vez, sem duplicação.
    """
    linhas, cabecalhos = _indexar_cabecalhos(md)

    secoes_pedidas = set(secoes_cfg)
    encontradas    = []
    nao_encontradas = []
    partes         = []
    ja_usados      = set()  # índices de cabeçalhos já extraídos

    for sec in secoes_cfg:
        if sec in encontradas:
            continue  # coberto por composite anterior

        for cab_idx, (_, nivel, chave, _texto) in enumerate(cabecalhos):
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

            conteudo = _bloco_de(linhas, cabecalhos, cab_idx)
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


# Abaixo de 3 cabeçalhos não há lista útil para mandar — cai no texto corrido.
_MIN_CABECALHOS_IA = 3
# Teto de segurança: artigos com centenas de subtítulos não devem inflar o prompt.
_MAX_CABECALHOS_IA = 120


def _config_ollama():
    """(url, modelo, timeout) conforme as configurações do usuário."""
    return (
        _get_setting('ollama_url', OLLAMA_URL_PADRAO).rstrip('/'),
        _get_setting('ollama_modelo', OLLAMA_MODELO_PADRAO),
        _get_setting('ollama_timeout_s', OLLAMA_TIMEOUT_S),
    )


def _perguntar_ollama(prompt, url, modelo, timeout, deve_cancelar=None):
    """Uma chamada JSON ao Ollama. Devolve o dict, ou {} em qualquer falha."""
    try:
        resposta = ollama_bridge.gerar(
            prompt, modelo=modelo, url=url, timeout=timeout, formato='json',
            options={'temperature': 0, 'num_ctx': 8192},
            keep_alive=OLLAMA_KEEP_ALIVE, deve_cancelar=deve_cancelar)
        dados = json.loads(resposta or '{}')
    except (ollama_bridge.OllamaErro, json.JSONDecodeError, ValueError) as exc:
        logger.warning('Fallback IA local (Ollama) falhou | url=%s | modelo=%s | erro=%s',
                        url, modelo, exc)
        return {}
    return dados if isinstance(dados, dict) else {}


def _secoes_ia_por_cabecalho(cabecalhos, secoes, url, modelo, timeout,
                             deve_cancelar=None):
    """Pergunta qual cabeçalho abre cada seção. Retorna {secao: indice}.

    Manda a lista de cabeçalhos, não o texto do artigo. Medido no corpus: com
    o texto truncado em 15 mil caracteres, 14 de 20 seções de métodos e todas
    as conclusões ficavam fora da janela enviada — a IA não errava, não via.
    A lista cabe inteira, custa uma fração dos tokens, e a resposta é um
    índice, que não pode ser parafraseado como um trecho literal podia.
    """
    lista = '\n'.join(
        f'{i}: {texto[:100]}'
        for i, (_, _, _chave, texto) in enumerate(cabecalhos[:_MAX_CABECALHOS_IA])
    )
    prompt = (
        'Abaixo está a lista numerada dos cabeçalhos de um artigo científico, '
        'na ordem em que aparecem no documento.\n'
        f'Diga qual número abre cada uma destas seções: {", ".join(secoes)}.\n'
        'Considere variações de título: "Experimental Section" e "Materials '
        'and Methods" são metodos; "Concluding Remarks" é conclusao. Em muitas '
        'revistas os métodos aparecem no fim, depois da discussão ou até das '
        'referências. Se uma seção não existir na lista, não a inclua.\n'
        'Responda apenas com JSON no formato {"nome_da_secao": numero}.\n\n'
        f'CABECALHOS:\n{lista}'
    )
    escolhas = {}
    resposta = _perguntar_ollama(prompt, url, modelo, timeout, deve_cancelar)
    for sec, val in resposta.items():
        if sec not in secoes:
            continue
        try:
            idx = int(val)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < min(len(cabecalhos), _MAX_CABECALHOS_IA):
            escolhas[sec] = idx
    return escolhas


# Onde cada seção vive no documento. Só importa no caminho de reserva, que
# manda texto corrido: procurar a conclusão nos primeiros caracteres é olhar
# na metade errada do artigo — era o que devolvia abertura de discussão
# rotulada como conclusão.
_SECOES_NO_FIM = {'conclusao', 'referencias'}
# Janela do caminho de reserva. Era 15 mil quando esse era o único caminho;
# agora ele só entra para seções sem cabeçalho, que são curtas e localizadas.
_JANELA_TEXTO = 5000


def _trecho_relevante(md, secoes):
    """Recorte do documento onde as seções procuradas costumam estar."""
    if len(md) <= _JANELA_TEXTO:
        return md
    quer_fim    = any(s in _SECOES_NO_FIM for s in secoes)
    quer_inicio = any(s not in _SECOES_NO_FIM for s in secoes)
    if quer_fim and quer_inicio:
        meio = _JANELA_TEXTO // 2
        return f'{md[:meio]}\n\n[...]\n\n{md[-meio:]}'
    return md[-_JANELA_TEXTO:] if quer_fim else md[:_JANELA_TEXTO]


def _secoes_ia_por_texto(md_texto, secoes, url, modelo, timeout,
                         deve_cancelar=None):
    """Caminho de reserva: manda o texto e pede a primeira frase literal.

    Usado só quando a conversão não produziu cabeçalhos, ou quando a lista de
    cabeçalhos não contém a seção procurada — aí o texto corrido é a única
    pista. Manda o trecho onde a seção costuma estar, não sempre o começo.
    """
    prompt = (
        'Você recebe abaixo um trecho do texto (Markdown) de um artigo '
        'científico. '
        f'Localize onde começa o CORPO de cada uma destas seções: {", ".join(secoes)}.\n'
        'Para cada seção que encontrar, copie EXATAMENTE (sem parafrasear, '
        'sem resumir) a primeira frase do conteúdo da seção — não o título/'
        'cabeçalho, o texto logo depois dele. Se não encontrar uma seção, '
        'simplesmente não a inclua na resposta.\n'
        'Responda apenas com um objeto JSON no formato '
        '{"nome_da_secao": "trecho inicial literal"}.\n\n'
        f'TEXTO:\n{_trecho_relevante(md_texto, secoes)}'
    )
    marcadores = _perguntar_ollama(prompt, url, modelo, timeout, deve_cancelar)
    return {k: v for k, v in marcadores.items()
            if k in secoes and isinstance(v, str) and v.strip()}


def _fallback_secoes_ia(md, secoes_faltando, deve_cancelar=None):
    """Tenta recuperar seções que o regex/fuzzy não encontraram, via IA local.

    Retorna (partes, encontradas) no mesmo formato de _extrair_secoes. Prefere
    perguntar por cabeçalho — o artigo inteiro fica visível e a delimitação do
    bloco é a mesma do regex. Só cai no texto corrido quando a conversão não
    produziu cabeçalhos suficientes.
    """
    if not secoes_faltando:
        return [], []

    url, modelo, timeout = _config_ollama()
    linhas, cabecalhos = _indexar_cabecalhos(md)

    if len(cabecalhos) >= _MIN_CABECALHOS_IA:
        escolhas = _secoes_ia_por_cabecalho(cabecalhos, secoes_faltando,
                                            url, modelo, timeout, deve_cancelar)
        partes, encontradas = [], []
        for sec, cab_idx in sorted(escolhas.items(), key=lambda kv: kv[1]):
            conteudo = _bloco_de(linhas, cabecalhos, cab_idx)
            if conteudo:
                partes.append(f'[{sec.upper()} — via IA local]\n{conteudo}')
                encontradas.append(sec)
        if encontradas:
            return partes, encontradas

    marcadores = _secoes_ia_por_texto(md, secoes_faltando, url, modelo,
                                      timeout, deve_cancelar)
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
