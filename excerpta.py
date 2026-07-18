import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import re
import os
import sys
import json
import subprocess
import shutil
import time
import urllib.request
import urllib.error
import webbrowser
from functools import lru_cache

# ── Motores de extração ────────────────────────────────────────────────────────

try:
    import pymupdf4llm as _pymupdf4llm
    PYMUPDF4LLM_OK = True
except ImportError:
    _pymupdf4llm = None
    PYMUPDF4LLM_OK = False

try:
    from docling.document_converter import DocumentConverter as _DoclingConverter
    DOCLING_OK = True
except ImportError:
    _DoclingConverter = None
    DOCLING_OK = False

MOTOR_OK = PYMUPDF4LLM_OK or DOCLING_OK

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


# ══════════════════════════════════════ constantes ══════════════════════════════


_NUM_PREFIX_RE = re.compile(
    r'^[\d]+(?:\.[\d]+)*\.?\s*\|\s*'                      # "1 | ", "2.3 | "
    r'|^[\d]+(?:\.[\d]+)*\.?\s+'                           # "1.", "2.1.", "1 "
    r'|^[A-Z]\.\s+'                                        # "A. "
    r'|^(?:II|III|IV|VI{0,3}|IX|XI{0,2}|XII)\.?\s+'      # "II.", "III.", "IV." …
)

_SUPLEMENTAR_INTEGRAL_RE = re.compile(
    r'supporting\s+information|supplementary\s+material|supplementary\s+data'
    r'|table\s+s\d+|figure\s+s\d+|supplemental|material\s+suplementar',
    re.IGNORECASE
)

RECENTS_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '.excerpta_recent.json')
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              '.excerpta_settings.json')


def _ler_settings():
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _salvar_settings(dados):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _get_setting(chave, padrao=None):
    return _ler_settings().get(chave, padrao)


def _set_setting(chave, valor):
    dados = _ler_settings()
    dados[chave] = valor
    _salvar_settings(dados)

_TEMPO_DIGITAL_S   = 2.0
_TEMPO_OCR_S       = 45.0
# Taxas por KB usadas para estimar tempo proporcional ao tamanho do arquivo.
# Derivadas dos tempos acima assumindo um PDF "típico" de 2 MB.
_TAXA_DIGITAL_S_KB = _TEMPO_DIGITAL_S / 2048.0
_TAXA_OCR_S_KB     = _TEMPO_OCR_S     / 2048.0
_FUZZY_LIMIAR    = 0.85

OLLAMA_URL_PADRAO    = 'http://localhost:11434'
OLLAMA_MODELO_PADRAO = 'qwen2.5:7b-instruct'
OLLAMA_TIMEOUT_S     = 25
OLLAMA_INSTALL_CMD   = 'curl -fsSL https://ollama.com/install.sh | sh'
OLLAMA_DOWNLOAD_URL  = 'https://ollama.com/download'


def _pip_flags():
    """Flags para pip que evitam precisar de permissão de administrador."""
    if sys.platform == 'win32':
        return ['--user']
    return ['--break-system-packages']

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

# ── Paleta Zotero-inspirada (tema claro) ──────────────────────────────────────
F           = "Ubuntu Sans"

BG_WINDOW   = "#F3F4F6"
BG_CARD     = "#FFFFFF"
BG_PANEL    = "#ECEEF1"

ACCENT      = "#3D6CAE"
ACCENT_HOV  = "#2D5599"
ACCENT_LT   = "#EEF3FA"
ACCENT_BORD = "#B6CCE8"
ACCENT_TXT  = "#2B4F8C"

GREEN       = "#2E7D4F"
GREEN_HOV   = "#1E6040"
GREEN_LT    = "#EEF8F2"
GREEN_BORD  = "#A4D4B4"
GREEN_HDR   = "#EAF5EF"
GREEN_TXT   = "#1A5C36"

GRAY_BORD   = "#D4D6DA"
GRAY_TEXT   = "#666C75"

TEXT_PRI    = "#1F2328"
TEXT_SEC    = "#6B7280"
DIVIDER     = "#E2E4E8"

C_OK        = "#2E7D4F"
C_WARN      = "#B45309"
C_ERR       = "#B91C1C"

_COR_PYMUPDF = ACCENT
_COR_DOCLING = "#7C3AED"


# ══════════════════════════════════════ motores ══════════════════════════════════

def detectar_tipo_pdf(caminho):
    """Retorna 'digital' se PDF tem texto nativo, 'escaneado' caso contrário."""
    try:
        import fitz
        doc = fitz.open(caminho)
        chars = sum(len(doc[i].get_text().strip()) for i in range(min(3, len(doc))))
        doc.close()
        return 'digital' if chars > 100 else 'escaneado'
    except ImportError:
        return 'digital'
    except Exception:
        return 'digital'


def _motor_para_tipo(tipo):
    if tipo == 'digital' and PYMUPDF4LLM_OK:
        return 'pymupdf4llm'
    if DOCLING_OK:
        return 'docling'
    if PYMUPDF4LLM_OK:
        return 'pymupdf4llm'
    return 'indisponível'


_IMG_LINE_RE   = re.compile(r'^==>.+<==\s*$')
_IMG_START_RE  = re.compile(r'^\*\*-+\s*Start of picture text\s*-+\*\*\s*$', re.IGNORECASE)
_IMG_END_RE    = re.compile(r'^\*\*-+\s*End of picture text\s*-+\*\*\s*$',   re.IGNORECASE)
_PICTURE_METADADOS_RE = re.compile(
    r'Received\s*:|Accepted\s*:|Published\s*:|\[\d|@|doi\.org|https?://'
    r'|\b(?:University|Institute|Department|Laboratory|Center|Centre)\b',
    re.IGNORECASE
)


def _limpar_markdown_imagens(md):
    """Remove marcações de imagem residuais do pymupdf4llm."""
    linhas = md.splitlines()
    resultado = []
    i = 0
    while i < len(linhas):
        linha = linhas[i]

        # Tipo 1: linha ==> ... <==
        if _IMG_LINE_RE.match(linha):
            i += 1
            continue

        # Tipo 2: bloco Start/End of picture text
        if _IMG_START_RE.match(linha):
            i += 1
            bloco = []
            while i < len(linhas):
                l = linhas[i]
                if _IMG_END_RE.match(l):
                    i += 1
                    break
                # Dupla linha em branco → fim de bloco sem marcador de fim
                if (l.strip() == '' and i + 1 < len(linhas)
                        and linhas[i + 1].strip() == ''):
                    break
                bloco.append(l)
                i += 1
            conteudo = '\n'.join(bloco).strip()
            if _PICTURE_METADADOS_RE.search(conteudo):
                pass              # lixo de afiliação/metadados → descarta tudo
            elif len(conteudo) > 80:
                resultado.append(conteudo)   # conteúdo útil → mantém sem marcações
            # conteúdo curto sem lixo → descarta
            continue

        resultado.append(linha)
        i += 1

    return re.sub(r'\n{3,}', '\n\n', '\n'.join(resultado))


# Marca d'água/rodapé de sites de PDF pirata (Sci-Hub, Library Genesis, Anna's
# Archive) que às vezes fica gravada como texto real na página — o motor de
# extração copia junto. Removida por linha para não afetar o resto do conteúdo.
_SITE_PIRATA_RE = re.compile(
    r"sci[-\s]?hub(?:\.\w{2,6})?"
    r"|library\s*genesis|libgen(?:\.\w{2,6})?"
    r"|anna'?s?[-\s]?archive(?:\.\w{2,6})?",
    re.IGNORECASE
)


def _limpar_marcas_pirata(md):
    """Remove linhas com marca d'água de sites de PDF pirata do Markdown."""
    linhas = md.splitlines()
    return '\n'.join(l for l in linhas if not _SITE_PIRATA_RE.search(l))


def converter_pdf(caminho):
    """Converte PDF para Markdown. Retorna (markdown, nome_motor)."""
    tipo = detectar_tipo_pdf(caminho)
    if tipo == 'digital' and PYMUPDF4LLM_OK:
        md = _pymupdf4llm.to_markdown(
            caminho, ignore_images=True, ignore_graphics=True)
        md = _limpar_marcas_pirata(_limpar_markdown_imagens(md))
        return md, 'pymupdf4llm'
    if DOCLING_OK:
        conv = _DoclingConverter()
        result = conv.convert(caminho)
        md = _limpar_marcas_pirata(result.document.export_to_markdown())
        return md, 'docling'
    if PYMUPDF4LLM_OK:
        md = _pymupdf4llm.to_markdown(
            caminho, ignore_images=True, ignore_graphics=True)
        md = _limpar_marcas_pirata(_limpar_markdown_imagens(md))
        return md, 'pymupdf4llm'
    raise ImportError("Nenhum motor instalado. Execute: pip install pymupdf4llm")



def _e_suplementar_integral(caminho):
    """Retorna True se as primeiras páginas indicam ser material suplementar integral."""
    try:
        import fitz
        doc = fitz.open(caminho)
        texto = ''.join(doc[i].get_text() for i in range(min(3, len(doc))))
        doc.close()
    except Exception:
        return False
    matches = _SUPLEMENTAR_INTEGRAL_RE.findall(texto[:3000])
    return len(matches) >= 2


# ══════════════════════════════════════ helpers ══════════════════════════════════

def extrair_chave_zotero(caminho):
    sem_ext = os.path.splitext(os.path.basename(caminho))[0]
    return sem_ext.split(" - ")[0].strip() if " - " in sem_ext else sem_ext.strip()



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


def _fragmentar_blocos(blocos, max_kb):
    """Agrupa blocos em fragmentos sem exceder max_kb. Nunca corta um bloco."""
    fragmentos, atual, kb_atual = [], [], 0.0
    for bloco in blocos:
        kb = len(bloco.encode('utf-8')) / 1024
        if atual and kb_atual + kb > max_kb:
            fragmentos.append(atual)
            atual, kb_atual = [bloco], kb
        else:
            atual.append(bloco)
            kb_atual += kb
    if atual:
        fragmentos.append(atual)
    return fragmentos



def abrir_no_sistema(caminho):
    try:
        if os.name == 'nt':
            os.startfile(caminho)
        else:
            subprocess.Popen(['xdg-open', caminho])
    except Exception:
        pass


def _font(size, weight='normal'):
    return ctk.CTkFont(family=F, size=size, weight=weight)


def _sep(parent, pady=(0, 0)):
    f = ctk.CTkFrame(parent, fg_color=DIVIDER, height=1, corner_radius=0)
    f.pack(fill='x', pady=pady)
    return f


# ── Histórico de pastas / arquivos recentes ───────────────────────────────────

def _ler_recentes():
    try:
        with open(RECENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'pastas': [], 'ia_files': []}


def _gravar_recente(chave, valor):
    dados = _ler_recentes()
    lista = dados.get(chave, [])
    if valor in lista:
        lista.remove(valor)
    lista.insert(0, valor)
    dados[chave] = lista[:6]
    try:
        with open(RECENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _btn_recentes(parent, entry, chave, on_select):
    """Botão ▾ que abre popup com os caminhos recentes."""
    import tkinter as tk
    btn_ref = [None]

    def _abrir():
        dados = _ler_recentes()
        lista = dados.get(chave, [])
        menu = tk.Menu(parent, tearoff=0, font=('Ubuntu Sans', 12))
        if lista:
            for item in lista:
                display = item if len(item) <= 60 else '…' + item[-57:]
                menu.add_command(
                    label=display,
                    command=lambda v=item: [
                        entry.delete(0, 'end'),
                        entry.insert(0, v),
                        on_select(v),
                    ]
                )
        else:
            menu.add_command(label='(sem histórico)', state='disabled')
        b = btn_ref[0]
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 2)

    btn = ctk.CTkButton(parent, text='▾', width=34, height=34,
                        fg_color=BG_PANEL, hover_color=GRAY_BORD,
                        text_color=TEXT_SEC, font=_font(14),
                        border_width=0, command=_abrir)
    btn.pack(side='left', padx=(4, 0))
    btn_ref[0] = btn
    return btn


def _bind_dnd_entry(entry, callback):
    """Registra drag-and-drop no campo de entrada (requer tkinterdnd2)."""
    try:
        from tkinterdnd2 import DND_FILES
        inner = entry._entry
        inner.drop_target_register(DND_FILES)
        inner.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


def _bind_dnd_lista(scrollable_frame, callback):
    """Registra drag-and-drop na lista de artigos (CTkScrollableFrame)."""
    try:
        from tkinterdnd2 import DND_FILES
        for w in (scrollable_frame._parent_canvas, scrollable_frame):
            w.drop_target_register(DND_FILES)
            w.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


def _bind_dnd_widget(widget, callback):
    """Registra drag-and-drop num CTkFrame individual (via _canvas interno)."""
    try:
        from tkinterdnd2 import DND_FILES
        widget._canvas.drop_target_register(DND_FILES)
        widget._canvas.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


# ══════════════════════════════════════ cabeçalho compartilhado ═══════════════════

def _header(parent, bg, txt_color, titulo):
    hdr = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0, height=52)
    hdr.pack(fill='x')
    hdr.pack_propagate(False)
    ctk.CTkFrame(parent, fg_color=DIVIDER, height=1, corner_radius=0).pack(fill='x')

    ctk.CTkLabel(hdr, text=titulo, font=_font(15, 'bold'),
                 text_color=txt_color).pack(side='left', padx=14, pady=10)

    return hdr


# ══════════════════════════════════════ configurações ════════════════════════════

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('Configurações')
        self.resizable(False, False)
        self.withdraw()
        self.protocol('WM_DELETE_WINDOW', self._fechar)

        cfg = _ler_settings()

        frame = ctk.CTkFrame(self, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True)

        ctk.CTkLabel(frame, text='Configurações', font=_font(15, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 4), anchor='w')
        _sep(frame, pady=(0, 10))

        tabs = ctk.CTkTabview(
            frame, width=420, height=380, fg_color=BG_CARD,
            segmented_button_fg_color=BG_PANEL,
            segmented_button_selected_color=GREEN,
            segmented_button_selected_hover_color=GREEN_HOV,
            text_color=TEXT_PRI)
        tabs.pack(fill='both', expand=True, padx=20, pady=(0, 12))
        aba_geral = tabs.add('Geral')
        aba_ia    = tabs.add('IA local (Ollama)')

        self._build_aba_geral(aba_geral, cfg)
        self._build_aba_ia(aba_ia, cfg)

        _sep(frame, pady=(0, 12))

        ctk.CTkButton(frame, text='Fechar', width=100, height=32,
                     fg_color=GREEN, hover_color=GREEN_HOV,
                     text_color='white', font=_font(13, 'bold'),
                     command=self._fechar).pack(pady=(0, 16))

        self.update_idletasks()
        self.geometry('480x560')
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(50, self.grab_set)

    # ── aba Geral ───────────────────────────────────────────────────────────

    def _build_aba_geral(self, tab, cfg):
        inner = ctk.CTkScrollableFrame(tab, fg_color='transparent')
        inner.pack(fill='both', expand=True)

        # ── Arquivo de saída ──────────────────────────────────────────────────
        ctk.CTkLabel(inner, text='Arquivo de saída',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(6, 6), anchor='w')

        row_nome = ctk.CTkFrame(inner, fg_color='transparent')
        row_nome.pack(fill='x', pady=(0, 4))
        ctk.CTkLabel(row_nome, text='Nome base dos fragmentos:',
                     font=_font(12), text_color=TEXT_PRI,
                     width=190, anchor='w').pack(side='left')
        self._entry_nome = ctk.CTkEntry(row_nome, width=170, height=30,
                                         font=_font(13),
                                         placeholder_text='ex: minha_extracao')
        self._entry_nome.pack(side='left')
        nome_salvo = cfg.get('nome_base', '')
        if nome_salvo:
            self._entry_nome.insert(0, nome_salvo)

        ctk.CTkLabel(inner,
                     text='Atualizado automaticamente com o nome da pasta selecionada.',
                     font=_font(11), text_color=TEXT_SEC
                     ).pack(pady=(0, 12), anchor='w')

        _sep(inner, pady=(0, 10))

        # ── Motores de extração ───────────────────────────────────────────────
        row_ocr_hdr = ctk.CTkFrame(inner, fg_color='transparent')
        row_ocr_hdr.pack(fill='x', pady=(6, 6))
        ctk.CTkLabel(row_ocr_hdr, text='Motores de extração',
                     font=_font(12, 'bold'), text_color=TEXT_SEC,
                     anchor='w').pack(side='left')

        row_ocr = ctk.CTkFrame(inner, fg_color='transparent')
        row_ocr.pack(fill='x', pady=(0, 4))
        self._var_ocr = ctk.BooleanVar(value=cfg.get('usar_ocr', True))
        ctk.CTkCheckBox(row_ocr,
                        text='Processar PDFs escaneados com OCR',
                        variable=self._var_ocr,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(side='left')
        ctk.CTkButton(row_ocr, text='?', width=26, height=26,
                      fg_color=BG_PANEL, hover_color=ACCENT_LT,
                      text_color=ACCENT, font=_font(12, 'bold'),
                      border_width=1, border_color=ACCENT_BORD,
                      command=self._ajuda_ocr
                      ).pack(side='left', padx=(8, 0))

        ctk.CTkLabel(inner,
                     text='Requer docling instalado. PDFs escaneados são imagens '
                          'sem texto digital.',
                     font=_font(11), text_color=TEXT_SEC, wraplength=340
                     ).pack(pady=(0, 12), anchor='w')

        if not DOCLING_OK:
            row_docling = ctk.CTkFrame(inner, fg_color='transparent')
            row_docling.pack(fill='x', pady=(0, 4))
            ctk.CTkLabel(row_docling, text='✗ docling não instalado',
                         font=_font(12), text_color=C_ERR).pack(side='left')
            ctk.CTkButton(row_docling, text='Instalar suporte a OCR',
                         width=170, height=28,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=self._confirmar_instalar_docling
                         ).pack(side='left', padx=(10, 0))
            ctk.CTkLabel(inner, text='≈2 GB, 10-20 min. Baixado uma única vez.',
                         font=_font(11), text_color=TEXT_SEC
                         ).pack(pady=(4, 12), anchor='w')
        else:
            ctk.CTkLabel(inner, text='✓ docling instalado',
                         font=_font(12), text_color=C_OK
                         ).pack(pady=(0, 12), anchor='w')

        _sep(inner, pady=(0, 10))

        # ── Ao concluir ───────────────────────────────────────────────────────
        ctk.CTkLabel(inner, text='Ao concluir a extração',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(6, 6), anchor='w')

        self._var_resumo = ctk.BooleanVar(value=cfg.get('mostrar_resumo', True))
        ctk.CTkCheckBox(inner,
                        text='Mostrar resumo de resultados ao terminar',
                        variable=self._var_resumo,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 8))

        self._var_pasta = ctk.BooleanVar(value=cfg.get('perguntar_abrir_pasta', True))
        ctk.CTkCheckBox(inner,
                        text='Perguntar se deseja abrir a pasta ao terminar',
                        variable=self._var_pasta,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 8))

    # ── aba IA local ────────────────────────────────────────────────────────

    def _build_aba_ia(self, tab, cfg):
        inner = ctk.CTkScrollableFrame(tab, fg_color='transparent')
        inner.pack(fill='both', expand=True)

        ctk.CTkLabel(inner, text='O que é isso?',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(6, 4), anchor='w')
        ctk.CTkLabel(inner,
                     text='Ollama roda modelos de IA no seu computador, sem mandar '
                          'nada pra internet. O Excerpta usa isso só como reforço: '
                          'quando o regex/fuzzy não acha alguma seção do artigo, '
                          'pergunta pro modelo local onde ela começa, antes de cair '
                          'no fallback padrão.',
                     font=_font(11), text_color=TEXT_SEC, wraplength=340,
                     justify='left'
                     ).pack(pady=(0, 12), anchor='w')

        if shutil.which('ollama'):
            ctk.CTkLabel(inner, text='✓ Ollama instalado',
                         font=_font(12), text_color=C_OK
                         ).pack(pady=(0, 12), anchor='w')
        else:
            ctk.CTkLabel(inner, text='✗ Ollama não instalado',
                         font=_font(12), text_color=C_ERR
                         ).pack(pady=(0, 12), anchor='w')

        _sep(inner, pady=(0, 10))

        self._var_ia_local = ctk.BooleanVar(value=cfg.get('usar_ia_local', False))
        ctk.CTkCheckBox(inner,
                        text='Usar IA local (Ollama) quando seções não são encontradas',
                        variable=self._var_ia_local,
                        command=self._on_ia_local_toggle,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(6, 4))
        ctk.CTkLabel(inner,
                     text='Desligado por padrão. Se o Ollama não responder, o '
                          'Excerpta usa o fallback normal sem travar.',
                     font=_font(11), text_color=TEXT_SEC, wraplength=340
                     ).pack(pady=(0, 8), anchor='w')

        self._row_ollama_fields = ctk.CTkFrame(inner, fg_color='transparent')
        self._row_ollama_fields.pack(fill='x', pady=(0, 6))

        row_url = ctk.CTkFrame(self._row_ollama_fields, fg_color='transparent')
        row_url.pack(fill='x', pady=(0, 6))
        ctk.CTkLabel(row_url, text='Servidor:', font=_font(12),
                     text_color=TEXT_PRI, width=80, anchor='w').pack(side='left')
        self._entry_ollama_url = ctk.CTkEntry(row_url, width=220, height=30,
                                               font=_font(12))
        self._entry_ollama_url.insert(0, cfg.get('ollama_url', OLLAMA_URL_PADRAO))
        self._entry_ollama_url.pack(side='left')

        row_modelo = ctk.CTkFrame(self._row_ollama_fields, fg_color='transparent')
        row_modelo.pack(fill='x')
        ctk.CTkLabel(row_modelo, text='Modelo:', font=_font(12),
                     text_color=TEXT_PRI, width=80, anchor='w').pack(side='left')
        self._entry_ollama_modelo = ctk.CTkEntry(row_modelo, width=220, height=30,
                                                  font=_font(12))
        self._entry_ollama_modelo.insert(0, cfg.get('ollama_modelo', OLLAMA_MODELO_PADRAO))
        self._entry_ollama_modelo.pack(side='left')

        row_status = ctk.CTkFrame(inner, fg_color='transparent')
        row_status.pack(fill='x', pady=(6, 0))
        ctk.CTkButton(row_status, text='Verificar conexão', width=140, height=28,
                      fg_color=BG_PANEL, hover_color=GRAY_BORD,
                      text_color=TEXT_PRI, font=_font(12),
                      border_width=1, border_color=GRAY_BORD,
                      command=self._verificar_ollama
                      ).pack(side='left')
        self._lbl_status_ollama = ctk.CTkLabel(row_status, text='', font=_font(12),
                                                text_color=TEXT_SEC)
        self._lbl_status_ollama.pack(side='left', padx=(10, 0))

        self._on_ia_local_toggle()

        _sep(inner, pady=(12, 10))

        ctk.CTkLabel(inner, text='Não tem o Ollama instalado?',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(0, 6), anchor='w')

        if sys.platform.startswith('linux'):
            ctk.CTkLabel(inner,
                         text='Roda o instalador oficial (ollama.com/install.sh) '
                              'com log ao vivo, direto por aqui. Pode pedir senha '
                              'de administrador no processo.',
                         font=_font(11), text_color=TEXT_SEC, wraplength=340
                         ).pack(pady=(0, 8), anchor='w')
            ctk.CTkButton(inner, text='Instalar Ollama', width=150, height=30,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=self._confirmar_instalar_ollama
                         ).pack(anchor='w', pady=(0, 12))
        else:
            ctk.CTkLabel(inner,
                         text='Baixe o instalador oficial pro seu sistema em '
                              'ollama.com.',
                         font=_font(11), text_color=TEXT_SEC, wraplength=340
                         ).pack(pady=(0, 8), anchor='w')
            ctk.CTkButton(inner, text='Abrir página de download', width=180, height=30,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=lambda: webbrowser.open(OLLAMA_DOWNLOAD_URL)
                         ).pack(anchor='w', pady=(0, 12))

    def _on_ia_local_toggle(self):
        estado = 'normal' if self._var_ia_local.get() else 'disabled'
        self._entry_ollama_url.configure(state=estado)
        self._entry_ollama_modelo.configure(state=estado)

    def _verificar_ollama(self):
        self._lbl_status_ollama.configure(text='verificando…', text_color=TEXT_SEC)
        url = self._entry_ollama_url.get().strip() or OLLAMA_URL_PADRAO

        def _checar():
            try:
                req = urllib.request.Request(f'{url.rstrip("/")}/api/tags')
                with urllib.request.urlopen(req, timeout=4):
                    ok = True
            except Exception:
                ok = False
            self.after(0, lambda: self._lbl_status_ollama.configure(
                text='✓ Ollama respondendo' if ok else '✗ Não consegui conectar',
                text_color=C_OK if ok else C_ERR))

        threading.Thread(target=_checar, daemon=True).start()

    def _confirmar_instalar_ollama(self):
        if not messagebox.askyesno(
                'Instalar Ollama',
                'Isso vai rodar o instalador oficial num terminal embutido:\n\n'
                f'  {OLLAMA_INSTALL_CMD}\n\n'
                'O script pode pedir a senha de administrador (sudo) — se travar '
                'pedindo senha, cancele e rode o comando acima manualmente num '
                'terminal.\n\nContinuar?'):
            return
        self._instalar_ollama_gui()

    def _instalar_ollama_gui(self):
        win = ctk.CTkToplevel(self)
        win.title('Instalando Ollama…')
        win.resizable(False, False)
        win.withdraw()
        win.transient(self)

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='Instalando Ollama…', font=_font(13, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 8), anchor='w')

        log = ctk.CTkTextbox(fr, width=440, height=220, font=('TkFixedFont', 10))
        log.pack(padx=20, pady=(0, 12))
        log.configure(state='disabled')

        def _log(txt):
            log.configure(state='normal')
            log.insert('end', txt + '\n')
            log.see('end')
            log.configure(state='disabled')

        btn_fechar = ctk.CTkButton(fr, text='Fechar', width=90, height=30,
                                    fg_color=BG_PANEL, hover_color=GRAY_BORD,
                                    text_color=TEXT_PRI, font=_font(12),
                                    state='disabled', command=win.destroy)
        btn_fechar.pack(pady=(0, 16))

        def _run():
            inicio = time.time()
            code = 1
            try:
                proc = subprocess.Popen(
                    ['sh', '-c', OLLAMA_INSTALL_CMD],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for linha in proc.stdout:
                    win.after(0, lambda l=linha: _log(l.rstrip('\n')))
                    if time.time() - inicio > 600:
                        proc.kill()
                        win.after(0, lambda: _log(
                            '\n✗ Tempo limite excedido (10 min). Abortado.'))
                        break
                code = proc.wait()
            except Exception as exc:
                win.after(0, lambda e=exc: _log(f'Erro: {e}'))

            def _fim():
                if code == 0:
                    _log('\n✓ Instalação concluída. Use "Verificar conexão" na '
                         'aba anterior (pode levar alguns segundos pro serviço subir).')
                else:
                    _log('\n✗ Falha na instalação automática.')
                    _log('Rode manualmente num terminal:')
                    _log(f'  {OLLAMA_INSTALL_CMD}')
                btn_fechar.configure(state='normal')
            win.after(0, _fim)

        threading.Thread(target=_run, daemon=True).start()

        win.update_idletasks()
        win.geometry('480x330')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _confirmar_instalar_docling(self):
        if not messagebox.askyesno(
                'Instalar suporte a OCR',
                'Isso vai instalar o pacote "docling" via pip (≈2 GB, pode '
                'levar 10-20 min dependendo da conexão).\n\nContinuar?'):
            return
        self._instalar_docling_gui()

    def _instalar_docling_gui(self):
        win = ctk.CTkToplevel(self)
        win.title('Instalando suporte a OCR…')
        win.resizable(False, False)
        win.withdraw()
        win.transient(self)

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='Instalando docling (OCR)…', font=_font(13, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 8), anchor='w')

        log = ctk.CTkTextbox(fr, width=440, height=220, font=('TkFixedFont', 10))
        log.pack(padx=20, pady=(0, 12))
        log.configure(state='disabled')

        def _log(txt):
            log.configure(state='normal')
            log.insert('end', txt + '\n')
            log.see('end')
            log.configure(state='disabled')

        btn_fechar = ctk.CTkButton(fr, text='Fechar', width=90, height=30,
                                    fg_color=BG_PANEL, hover_color=GRAY_BORD,
                                    text_color=TEXT_PRI, font=_font(12),
                                    state='disabled', command=win.destroy)
        btn_fechar.pack(pady=(0, 16))

        def _run():
            _log('> pip install docling')
            res = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', 'docling']
                + _pip_flags(),
                capture_output=True, text=True
            )
            ok = res.returncode == 0
            if not ok:
                res2 = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--upgrade', 'docling'],
                    capture_output=True, text=True
                )
                ok = res2.returncode == 0
                if not ok:
                    win.after(0, lambda: _log(
                        f'✗ Falha: {(res.stderr or res2.stderr).strip()[:400]}'))

            def _fim():
                if ok:
                    _log('\n✓ docling instalado. Feche e reabra o Excerpta para '
                         'usar o OCR.')
                else:
                    _log('\nRode manualmente num terminal:')
                    _log('  pip install docling' +
                         ('' if sys.platform == 'win32' else ' --break-system-packages'))
                btn_fechar.configure(state='normal')
            win.after(0, _fim)

        threading.Thread(target=_run, daemon=True).start()

        win.update_idletasks()
        win.geometry('480x330')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _ajuda_ocr(self):
        win = ctk.CTkToplevel(self)
        win.title('O que é OCR?')
        win.resizable(False, False)
        win.withdraw()

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='OCR — Reconhecimento Óptico de Caracteres',
                     font=_font(13, 'bold'), text_color=TEXT_PRI
                     ).pack(padx=20, pady=(16, 8), anchor='w')

        texto = (
            'PDFs podem ser de dois tipos:\n\n'
            '• Digital: o texto já está embutido no arquivo — '
            'rápido de extrair (~2s por artigo).\n\n'
            '• Escaneado: o PDF é uma foto de uma página impressa, '
            'sem texto legível por software. O OCR "lê" a imagem e '
            'converte em texto — mais lento (~45s por artigo) e requer '
            'o pacote docling instalado.\n\n'
            'Se desativado, os PDFs escaneados são simplesmente '
            'ignorados durante a extração.'
        )
        ctk.CTkLabel(fr, text=texto, font=_font(12), text_color=TEXT_PRI,
                     justify='left', wraplength=360, anchor='w'
                     ).pack(padx=20, pady=(0, 12), anchor='w')

        ctk.CTkButton(fr, text='Entendi', width=90, height=30,
                      fg_color=GREEN, hover_color=GREEN_HOV,
                      text_color='white', font=_font(12, 'bold'),
                      command=win.destroy).pack(pady=(0, 16))

        win.update_idletasks()
        h = win.winfo_reqheight() + 20
        win.geometry(f'400x{max(h, 200)}')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _fechar(self):
        _salvar_settings({
            **_ler_settings(),
            'nome_base': self._entry_nome.get().strip(),
            'usar_ocr': self._var_ocr.get(),
            'mostrar_resumo': self._var_resumo.get(),
            'perguntar_abrir_pasta': self._var_pasta.get(),
            'usar_ia_local': self._var_ia_local.get(),
            'ollama_url': self._entry_ollama_url.get().strip() or OLLAMA_URL_PADRAO,
            'ollama_modelo': self._entry_ollama_modelo.get().strip() or OLLAMA_MODELO_PADRAO,
        })
        self.destroy()


# ══════════════════════════════════════ resumo pós-extração ══════════════════════

class StatsWindow(ctk.CTkToplevel):
    def __init__(self, parent, stats, path_saida,
                 fragmentado=False, n_frags=0, kb_frags=None):
        super().__init__(parent)
        self.title('Extração concluída')
        self.resizable(False, False)
        self.withdraw()

        total     = stats.get('total', 0)
        completos = stats.get('completos', 0)
        adaptados = stats.get('adaptados', 0)
        reviews   = stats.get('reviews', 0)
        ignorados = stats.get('ignorados', 0)
        erros     = stats.get('erros', 0)

        extraidos     = completos + adaptados + reviews
        nao_extraidos = ignorados + erros

        frame = ctk.CTkFrame(self, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True)

        # Cabeçalho — diz de cara COMO foi (quantos saíram com texto), não só
        # "processados". Ex.: "39 de 42 artigos extraídos".
        hdr_f = ctk.CTkFrame(frame, fg_color=GREEN_HDR, corner_radius=0, height=52)
        hdr_f.pack(fill='x')
        hdr_f.pack_propagate(False)
        if nao_extraidos == 0:
            hdr_txt = f'✓  Todos os {total} artigos foram extraídos'
        else:
            hdr_txt = f'✓  {extraidos} de {total} artigos extraídos'
        ctk.CTkLabel(hdr_f, text=hdr_txt, font=_font(14, 'bold'),
                     text_color=GREEN_TXT).pack(side='left', padx=16, pady=12)
        ctk.CTkFrame(frame, fg_color=DIVIDER, height=1, corner_radius=0).pack(fill='x')

        # Frase-resumo em linguagem simples, logo abaixo do cabeçalho.
        if nao_extraidos == 0:
            resumo_txt = 'Todos os artigos saíram com texto.'
        elif extraidos == 0:
            resumo_txt = 'Nenhum artigo pôde ser extraído — veja abaixo o motivo.'
        else:
            resumo_txt = (f'{extraidos} artigos saíram com texto e {nao_extraidos} não — '
                          f'veja abaixo o que aconteceu.')
        ctk.CTkLabel(frame, text=resumo_txt, font=_font(12), text_color=TEXT_SEC,
                     wraplength=410, justify='left', anchor='w').pack(
                         fill='x', padx=20, pady=(12, 0))

        body = ctk.CTkFrame(frame, fg_color='transparent')
        body.pack(fill='x', padx=20, pady=(8, 6))

        # Rótulos em linguagem direta. Mantém "Complementados automaticamente"
        # (termo já adotado, explicado na nota abaixo).
        rows = []
        if completos > 0:
            rows.append((C_OK,    '✓', str(completos), 'Seções pedidas encontradas'))
        if adaptados > 0:
            rows.append((C_WARN,  '≈', str(adaptados), 'Complementados automaticamente'))
        if reviews > 0:
            rows.append((ACCENT,  '▤', str(reviews),   'Artigos de revisão (texto inteiro)'))
        if ignorados > 0:
            rows.append((GRAY_TEXT,'○', str(ignorados), 'Pulados (suplementar ou PDF sem texto)'))
        if erros > 0:
            rows.append((C_ERR,   '✗', str(erros),     'Falharam (não foi possível ler)'))

        for cor, icone, count, desc in rows:
            r = ctk.CTkFrame(body, fg_color='transparent')
            r.pack(fill='x', pady=3)
            ctk.CTkLabel(r, text=icone, font=_font(13, 'bold'), text_color=cor,
                         width=20, anchor='center').pack(side='left')
            ctk.CTkLabel(r, text=count, font=_font(13, 'bold'), text_color=cor,
                         width=32, anchor='center').pack(side='left', padx=(4, 10))
            ctk.CTkLabel(r, text=desc, font=_font(13), text_color=TEXT_PRI,
                         anchor='w').pack(side='left')

        if adaptados > 0:
            nota = ctk.CTkFrame(frame, fg_color=ACCENT_LT, border_color=ACCENT_BORD,
                                border_width=1, corner_radius=6)
            nota.pack(fill='x', padx=20, pady=(6, 4))
            ctk.CTkLabel(nota,
                         text='"Complementados automaticamente": algumas seções solicitadas\n'
                              'não foram encontradas. O restante do artigo foi incluído\n'
                              'como complemento para não perder conteúdo.',
                         font=_font(11), text_color=ACCENT_TXT,
                         justify='left').pack(padx=12, pady=8, anchor='w')

        if fragmentado and kb_frags:
            resumo = '  ·  '.join(f'parte {i+1}: ~{kb:.0f} KB'
                                   for i, kb in enumerate(kb_frags))
            ctk.CTkLabel(frame, text=resumo, font=_font(11), text_color=TEXT_SEC,
                         wraplength=400, anchor='w').pack(padx=20, pady=(4, 0), fill='x')

        _sep(frame, pady=(10, 0))

        btn_row = ctk.CTkFrame(frame, fg_color='transparent')
        btn_row.pack(pady=12)

        if _get_setting('perguntar_abrir_pasta', True):
            pasta_final = path_saida if fragmentado else os.path.dirname(path_saida)
            ctk.CTkButton(btn_row, text='Abrir pasta de saída', width=164, height=32,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(13, 'bold'),
                         command=lambda: [abrir_no_sistema(pasta_final), self.destroy()]
                         ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(btn_row, text='Fechar', width=90, height=32,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(13),
                     border_width=1, border_color=GRAY_BORD,
                     command=self.destroy).pack(side='left')

        self.update_idletasks()
        h = self.winfo_reqheight() + 20
        self.geometry(f'450x{max(h, 220)}')
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(50, self.grab_set)


# ══════════════════════════════════════ etapa 3 ══════════════════════════════════

class Etapa3Frame(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG_WINDOW)
        self._app          = app
        self._pasta        = ''
        self._selecao      = []
        self._selecao_raw  = []
        self._var_tudo       = ctk.BooleanVar(value=True)
        self._var_fallback   = ctk.BooleanVar(value=True)
        self._var_fragmentar = ctk.BooleanVar(value=False)
        self._sec_vars       = {}
        self._sec_btns       = {}
        self._btn_tudo_chip  = None
        self._engine_labels  = {}
        self._tipos_pdf      = {}
        self._cancelando     = False
        self._n_digital      = 0
        self._n_ocr          = 0
        self._lbl_tempo_analise = None
        self._build()

    def _build(self):
        hdr = _header(self, bg=GREEN_HDR, txt_color=GREEN_TXT,
                      titulo='Excerpta — Extração Seletiva de Artigos')
        ctk.CTkButton(hdr, text='⚙  Configurações', width=130, height=34,
                      fg_color='transparent', hover_color=GREEN_LT,
                      text_color=GREEN_TXT, font=_font(12),
                      border_width=1, border_color=GREEN_BORD,
                      command=self._abrir_settings
                      ).pack(side='right', padx=14, pady=9)

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.pack(fill='both', expand=True, padx=20, pady=16)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(4, weight=1)   # lista expande (row 4)

        # ── Linha 0: inputs (pasta + IA + atalho direto) ──────────────────────
        cfg = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                           border_width=1, corner_radius=8)
        cfg.grid(row=0, column=0, sticky='ew', pady=(0, 6))

        r1 = ctk.CTkFrame(cfg, fg_color='transparent')
        r1.pack(fill='x', padx=16, pady=(12, 12))
        ctk.CTkLabel(r1, text='Pasta dos PDFs', font=_font(14, 'bold'),
                    text_color=TEXT_PRI, width=148, anchor='w').pack(side='left')
        self._entry_pasta3 = ctk.CTkEntry(
            r1, placeholder_text='Cole, arraste ou selecione a pasta…',
            font=_font(14), height=34)
        self._entry_pasta3.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._entry_pasta3.bind('<Return>', lambda _: self._aplicar_pasta3(
            self._entry_pasta3.get().strip()))
        ctk.CTkButton(r1, text='Selecionar', width=110,
                     fg_color=GREEN, hover_color=GREEN_HOV, height=34,
                     font=_font(14), command=self._sel_pasta3).pack(side='left')
        _btn_recentes(r1, self._entry_pasta3, 'pastas', self._aplicar_pasta3)
        _bind_dnd_entry(self._entry_pasta3, self._on_dnd_pasta3)

        # ── Linha 1: seções a extrair ──────────────────────────────────────────
        sec_card = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                                border_width=1, corner_radius=8)
        sec_card.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        inner3 = ctk.CTkFrame(sec_card, fg_color='transparent')
        inner3.pack(fill='x', padx=14, pady=(10, 12))

        row_hdr = ctk.CTkFrame(inner3, fg_color='transparent')
        row_hdr.pack(fill='x', pady=(0, 8))
        ctk.CTkLabel(row_hdr, text='Seções a extrair', font=_font(13, 'bold'),
                     text_color=TEXT_PRI, anchor='w').pack(side='left')
        self._btn_tudo_chip = ctk.CTkButton(
            row_hdr, text='Artigo completo', width=140, height=30, corner_radius=15,
            fg_color=GREEN, hover_color=GREEN_HOV, text_color='white',
            font=_font(12, 'bold'), border_width=1, border_color=GREEN_BORD,
            command=self._on_tudo_toggle)
        self._btn_tudo_chip.pack(side='right')

        sec_labels = [
            ('abstract',   'Abstract'),
            ('introducao', 'Introdução'),
            ('metodos',    'Métodos'),
            ('resultados', 'Resultados'),
            ('discussao',  'Discussão'),
            ('conclusao',  'Conclusão'),
            ('referencias','Referências'),
            ('suplementar','Suplementar'),
        ]

        r1 = ctk.CTkFrame(inner3, fg_color='transparent')
        r1.pack(fill='x', pady=(0, 6))
        r2 = ctk.CTkFrame(inner3, fg_color='transparent')
        r2.pack(fill='x')

        for i, (key, label) in enumerate(sec_labels):
            var = ctk.BooleanVar(value=False)
            self._sec_vars[key] = var
            row = r1 if i < 4 else r2
            b = ctk.CTkButton(row, text=label, height=30, corner_radius=15,
                              fg_color=BG_PANEL, text_color=GRAY_BORD,
                              border_color=GRAY_BORD, hover_color=ACCENT_LT,
                              font=_font(12), border_width=1, state='disabled',
                              command=lambda k=key: self._on_sec_chip_click(k))
            b.pack(side='left', padx=(0, 8))
            self._sec_btns[key] = b

        _sep(inner3, pady=(10, 6))
        ctk.CTkCheckBox(inner3,
                        text='Se nenhuma seção encontrada, extrair artigo completo',
                        variable=self._var_fallback,
                        font=_font(11), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD).pack(anchor='w')

        # ── Linha 2: painel de análise de motores ──────────────────────────────
        self._frame_analise = ctk.CTkFrame(main, fg_color=BG_CARD,
                                            border_color=DIVIDER, border_width=1,
                                            corner_radius=8)
        self._frame_analise.grid(row=2, column=0, sticky='ew', pady=(0, 4))
        self._frame_analise.grid_remove()

        # ── Linha 3: cabeçalho da lista ────────────────────────────────────────
        row_lhdr = ctk.CTkFrame(main, fg_color='transparent')
        row_lhdr.grid(row=3, column=0, sticky='ew', pady=(0, 4))
        ctk.CTkLabel(row_lhdr, text='Artigos selecionados',
                     font=_font(14, 'bold'), text_color=TEXT_PRI,
                     anchor='w').pack(side='left')
        self._lbl_resumo = ctk.CTkLabel(row_lhdr, text='', font=_font(13),
                                         text_color=TEXT_SEC)
        self._lbl_resumo.pack(side='left', padx=10)
        self._btn_rem_dup3 = ctk.CTkButton(
            row_lhdr, text='Remover duplicatas', width=148, height=28,
            fg_color=C_WARN, hover_color='#8B3A00',
            text_color='white', font=_font(12, 'bold'),
            command=self._remover_duplicatas_e3)
        self._btn_limpar = ctk.CTkButton(
            row_lhdr, text='Limpar lista', width=104, height=28,
            fg_color=BG_PANEL, hover_color=GRAY_BORD,
            text_color=TEXT_SEC, font=_font(12),
            border_width=1, border_color=GRAY_BORD,
            command=self._limpar_lista)

        # ── Linha 4: lista de artigos (expande) ────────────────────────────────
        self._frame_arts = ctk.CTkScrollableFrame(
            main, fg_color=BG_CARD, border_color=DIVIDER,
            border_width=1, corner_radius=8)
        self._frame_arts.grid(row=4, column=0, sticky='nsew')
        self._lbl_sem_ia = ctk.CTkLabel(
            self._frame_arts,
            text='Selecione uma pasta, ou arraste PDFs diretamente aqui.',
            text_color=TEXT_SEC, font=_font(13))
        self._lbl_sem_ia.pack(pady=28)
        _bind_dnd_lista(self._frame_arts, self._on_dnd_lista)

        # ── Linha 5: fragmentação ──────────────────────────────────────────────
        frag_card = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                                  border_width=1, corner_radius=8)
        frag_card.grid(row=5, column=0, sticky='ew', pady=(6, 0))
        frag_inner = ctk.CTkFrame(frag_card, fg_color='transparent')
        frag_inner.pack(fill='x', padx=14, pady=(8, 10))

        row_fhdr = ctk.CTkFrame(frag_inner, fg_color='transparent')
        row_fhdr.pack(fill='x', pady=(0, 6))
        ctk.CTkCheckBox(row_fhdr, text='Fragmentar arquivo de saída',
                        variable=self._var_fragmentar,
                        command=self._on_fragmentar_toggle,
                        font=_font(13, 'bold'), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD).pack(side='left')
        ctk.CTkLabel(row_fhdr,
                     text='Divide em vários arquivos respeitando artigos inteiros',
                     font=_font(11), text_color=TEXT_SEC).pack(side='left', padx=(10, 0))

        self._row_frag_fields = ctk.CTkFrame(frag_inner, fg_color='transparent')
        self._row_frag_fields.pack(fill='x')

        ctk.CTkLabel(self._row_frag_fields, text='Máximo por arquivo:',
                     font=_font(12), text_color=TEXT_SEC, anchor='w'
                     ).grid(row=0, column=0, sticky='w', padx=(0, 10))
        kb_row = ctk.CTkFrame(self._row_frag_fields, fg_color='transparent')
        kb_row.grid(row=0, column=1, sticky='w')
        self._entry_frag_kb = ctk.CTkEntry(
            kb_row, width=72, height=30, font=_font(13),
            justify='center')
        self._entry_frag_kb.insert(0, '500')
        self._entry_frag_kb.configure(state='disabled')
        self._entry_frag_kb.pack(side='left', padx=(0, 4))
        ctk.CTkLabel(kb_row, text='KB', font=_font(12),
                     text_color=TEXT_SEC).pack(side='left')
        ctk.CTkButton(kb_row, text='?', width=28, height=28,
                     fg_color=BG_PANEL, hover_color=ACCENT_LT,
                     text_color=ACCENT, font=_font(13, 'bold'),
                     border_width=1, border_color=ACCENT_BORD,
                     command=self._mostrar_ajuda_fragmentar
                     ).pack(side='left', padx=(8, 0))
        ctk.CTkLabel(kb_row, text='· padrão otimizado para Claude',
                     font=_font(11), text_color=TEXT_SEC
                     ).pack(side='left', padx=(10, 0))

        self._entry_frag_kb.bind('<KeyRelease>',
                                  lambda _: self._atualizar_preview_fragmentos())

        self._lbl_preview_frags = ctk.CTkLabel(
            frag_inner, text='', font=_font(12, 'bold'),
            text_color=GREEN_TXT, anchor='w')
        self._lbl_preview_frags.pack(fill='x', pady=(6, 0))

        # ── Linha 6: separador + rodapé ────────────────────────────────────────
        ctk.CTkFrame(main, fg_color=DIVIDER, height=1, corner_radius=0
                     ).grid(row=6, column=0, sticky='ew', pady=(10, 8))
        foot = ctk.CTkFrame(main, fg_color='transparent')
        foot.grid(row=7, column=0, sticky='ew')
        self._lbl_status3 = ctk.CTkLabel(foot, text='', font=_font(13),
                                          text_color=TEXT_SEC)
        self._lbl_status3.pack(side='left')
        self._prog3 = ctk.CTkProgressBar(foot, mode='determinate', width=200,
                                          height=6, progress_color=GREEN, fg_color=DIVIDER)
        self._btn_extrair = ctk.CTkButton(
            foot, text='↓  Extrair artigos', width=160,
            fg_color=GREEN, hover_color=GREEN_HOV, height=36,
            font=_font(14, 'bold'), state='disabled', command=self._extrair)
        self._btn_extrair.pack(side='right')

    # ── lógica ──────────────────────────────────────────────────────────────────

    def _on_tudo_toggle(self):
        self._var_tudo.set(not self._var_tudo.get())
        tudo_on = self._var_tudo.get()
        if tudo_on:
            self._btn_tudo_chip.configure(
                fg_color=GREEN, text_color='white', border_color=GREEN_BORD)
            for key in self._sec_btns:
                self._sec_vars[key].set(False)
                self._sec_btns[key].configure(
                    state='disabled', fg_color=BG_PANEL,
                    text_color=GRAY_BORD, border_color=GRAY_BORD)
        else:
            self._btn_tudo_chip.configure(
                fg_color=BG_CARD, text_color=TEXT_SEC, border_color=GRAY_BORD)
            for key in self._sec_btns:
                self._sec_btns[key].configure(state='normal')
                self._render_sec_chip(key)

    def _render_sec_chip(self, key):
        on = self._sec_vars[key].get()
        self._sec_btns[key].configure(
            fg_color=GREEN     if on else BG_CARD,
            text_color='white' if on else TEXT_SEC,
            border_color=GREEN_BORD if on else GRAY_BORD,
            hover_color=GREEN_HOV   if on else ACCENT_LT)

    def _on_sec_chip_click(self, key):
        self._sec_vars[key].set(not self._sec_vars[key].get())
        self._render_sec_chip(key)

    def _on_fragmentar_toggle(self):
        estado = 'normal' if self._var_fragmentar.get() else 'disabled'
        self._entry_frag_kb.configure(state=estado)
        self._atualizar_preview_fragmentos()

    def _atualizar_preview_fragmentos(self):
        prontos = [a for a in self._selecao if a['encontrado']]
        n = len(prontos)

        if n == 0:
            self._lbl_preview_frags.configure(text='')
            return

        if not self._var_fragmentar.get():
            self._lbl_preview_frags.configure(
                text=f'→ 1 arquivo de saída com {n} artigo(s)',
                text_color=GREEN_TXT)
            return

        try:
            max_kb = max(10.0, float(
                self._entry_frag_kb.get().strip().replace(',', '.')))
        except ValueError:
            self._lbl_preview_frags.configure(
                text='⚠  Valor de KB inválido', text_color=C_WARN)
            return

        total_est_kb = 0.0
        for art in prontos:
            try:
                total_est_kb += os.path.getsize(art['caminho']) / 1024 * 0.008
            except OSError:
                total_est_kb += 25.0

        import math
        n_frags = max(1, math.ceil(total_est_kb / max_kb))
        arts_por_frag = math.ceil(n / n_frags)

        self._lbl_preview_frags.configure(
            text=f'→ estimativa: {n_frags} arquivo(s) '
                 f'· ~{arts_por_frag} artigo(s) cada '
                 f'· total estimado ~{total_est_kb:.0f} KB',
            text_color=GREEN_TXT if n_frags <= 5 else C_WARN)

    def _get_secoes_config(self):
        if self._var_tudo.get():
            return ['tudo']
        selecionadas = [k for k, v in self._sec_vars.items() if v.get()]
        return selecionadas if selecionadas else ['tudo']

    def _sel_pasta3(self):
        pasta = filedialog.askdirectory(
            title='Selecionar pasta com PDFs originais',
            initialdir=self._pasta or os.path.expanduser('~'))
        if pasta:
            self._aplicar_pasta3(pasta)

    def _aplicar_pasta3(self, pasta):
        if not pasta or not os.path.isdir(pasta):
            return
        self._pasta = pasta
        self._entry_pasta3.delete(0, 'end')
        self._entry_pasta3.insert(0, pasta)
        _gravar_recente('pastas', pasta)
        _set_setting('nome_base', os.path.basename(pasta))
        # Carrega automaticamente todos os PDFs da pasta
        pdfs = sorted(f for f in os.listdir(pasta) if f.lower().endswith('.pdf'))
        if not pdfs:
            self._selecao_raw = []
            self._rebuild_artigos()
            self._lbl_status3.configure(text='Nenhum PDF encontrado na pasta.')
            return
        self._selecao_raw = [{'arquivo': nome, 'secoes': ['tudo']} for nome in pdfs]
        self._rebuild_artigos()
        self._lbl_status3.configure(text=f'{len(pdfs)} PDF(s) carregado(s) da pasta')

    def _on_dnd_pasta3(self, event):
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        if not paths:
            return
        path = paths[0]
        pasta = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.isdir(pasta):
            self._aplicar_pasta3(pasta)

    def _on_dnd_lista(self, event):
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        pdfs = [p for p in paths if p.lower().endswith('.pdf') and os.path.isfile(p)]
        if not pdfs:
            return
        for p in pdfs:
            self._selecao_raw.append({
                'arquivo': os.path.basename(p),
                'secoes': ['tudo'],
                'caminho_completo': p,
            })
        pastas = {os.path.dirname(p) for p in pdfs}
        if len(pastas) == 1:
            pasta_unica = pastas.pop()
            _set_setting('nome_base', os.path.basename(pasta_unica))
        self._rebuild_artigos()
        self._lbl_status3.configure(text=f'{len(pdfs)} PDF(s) adicionado(s)')

    def _rebuild_artigos(self):
        if not self._selecao_raw:
            return
        self._selecao = []
        self._engine_labels.clear()
        self._tipos_pdf = {}
        for item in self._selecao_raw:
            caminho    = item.get('caminho_completo') or (
                os.path.join(self._pasta, item['arquivo']) if self._pasta else '')
            encontrado = os.path.isfile(caminho) if caminho else False
            self._selecao.append({
                'arquivo': item['arquivo'], 'secoes': item['secoes'],
                'caminho': caminho, 'encontrado': encontrado,
            })

        vistos = set()
        duplicatas = set()
        for art in self._selecao:
            nome = art['arquivo']
            if nome in vistos:
                duplicatas.add(nome)
            vistos.add(nome)

        for w in self._frame_arts.winfo_children():
            w.destroy()

        n_ok  = sum(1 for a in self._selecao if a['encontrado'])
        n_err = len(self._selecao) - n_ok

        for i, art in enumerate(self._selecao):
            e_dup  = art['arquivo'] in duplicatas
            row_bg = '#FFF7ED' if e_dup else (BG_CARD if i % 2 == 0 else '#F7F8FA')
            linha  = ctk.CTkFrame(self._frame_arts, fg_color=row_bg,
                                  corner_radius=0, height=38)
            linha.pack(fill='x', padx=0)
            linha.pack_propagate(False)
            _bind_dnd_widget(linha, self._on_dnd_lista)

            icone = '✓' if art['encontrado'] else '✗'
            icor  = C_OK if art['encontrado'] else C_ERR
            ctk.CTkLabel(linha, text=icone, text_color=icor,
                        font=_font(14, 'bold'), width=28
                        ).pack(side='left', padx=(12, 4))

            nome_exib = art['arquivo'] if len(art['arquivo']) <= 52 else art['arquivo'][:49] + '…'
            ctk.CTkLabel(linha, text=nome_exib,
                        font=_font(13, 'bold'), text_color=C_WARN if e_dup else TEXT_PRI,
                        anchor='w').pack(side='left', padx=(0, 8))
            if e_dup:
                ctk.CTkLabel(linha, text='duplicata', text_color=C_WARN,
                            font=_font(11)).pack(side='left')

            if art['encontrado']:
                lbl_engine = ctk.CTkLabel(linha, text='…', font=_font(10),
                                           text_color=TEXT_SEC, width=90, anchor='e')
                lbl_engine.pack(side='right', padx=(0, 12))
                self._engine_labels[art['arquivo']] = lbl_engine
            else:
                aviso = 'pasta não definida' if not art['caminho'] else 'arquivo não encontrado'
                ctk.CTkLabel(linha, text=aviso, text_color=C_ERR,
                            font=_font(12), anchor='e'
                            ).pack(side='right', padx=12)

        resumo = f'{len(self._selecao)} artigo(s)'
        if n_err:
            resumo += f'  ·  ⚠ {n_err} não encontrado(s)'
        if duplicatas:
            resumo += f'  ·  ⚠ {len(duplicatas)} duplicata(s)'
        self._lbl_resumo.configure(text=resumo)

        self._btn_extrair.configure(
            state='normal' if n_ok > 0 and MOTOR_OK else 'disabled')
        if not MOTOR_OK:
            self._lbl_status3.configure(
                text='⚠  Nenhum motor instalado. Execute: pip install pymupdf4llm')

        if duplicatas:
            self._btn_rem_dup3.pack(side='right', padx=(0, 6))
        else:
            self._btn_rem_dup3.pack_forget()
        self._btn_limpar.pack(side='right')
        self._atualizar_preview_fragmentos()

        # Iniciar análise de motores em background
        prontos = [a for a in self._selecao if a['encontrado']]
        if prontos:
            threading.Thread(
                target=self._analise_motores_bg,
                args=(prontos,), daemon=True).start()

    def _analise_motores_bg(self, artigos):
        """Thread de background: detecta tipo de cada PDF e atualiza UI."""
        resultados = {}
        for art in artigos:
            tipo = detectar_tipo_pdf(art['caminho'])
            motor = _motor_para_tipo(tipo)
            resultados[art['arquivo']] = (tipo, motor)

        def _aplicar():
            self._tipos_pdf = {arq: tipo for arq, (tipo, _) in resultados.items()}
            n_digital  = sum(1 for t, _ in resultados.values() if t == 'digital')
            n_ocr      = sum(1 for t, _ in resultados.values() if t == 'escaneado')
            _usar_ocr  = _get_setting('usar_ocr', True)
            t_est      = n_digital * _TEMPO_DIGITAL_S + (n_ocr * _TEMPO_OCR_S if _usar_ocr else 0)

            for arquivo, (tipo, motor) in resultados.items():
                lbl = self._engine_labels.get(arquivo)
                if lbl:
                    cor = _COR_PYMUPDF if motor == 'pymupdf4llm' else _COR_DOCLING
                    lbl.configure(text=motor, text_color=cor)

            self._atualizar_frame_analise(n_digital, n_ocr, t_est)

        self.after(0, _aplicar)

    def _atualizar_frame_analise(self, n_digital, n_ocr, t_est):
        for w in self._frame_analise.winfo_children():
            w.destroy()

        if n_digital == 0 and n_ocr == 0:
            self._frame_analise.grid_remove()
            return

        self._n_digital = n_digital
        self._n_ocr     = n_ocr

        inner = ctk.CTkFrame(self._frame_analise, fg_color='transparent')
        inner.pack(fill='x', padx=12, pady=(6, 7))

        hdr = ctk.CTkFrame(inner, fg_color='transparent')
        hdr.pack(fill='x')
        ctk.CTkLabel(hdr, text='Motores de extração', font=_font(12, 'bold'),
                     text_color=TEXT_PRI, anchor='w').pack(side='left')

        t_str = f'~{t_est:.0f}s' if t_est < 60 else f'~{t_est / 60:.1f}min'
        self._lbl_tempo_analise = ctk.CTkLabel(
            hdr, text=f'⏱ {t_str} estimados', font=_font(11), text_color=TEXT_SEC)
        self._lbl_tempo_analise.pack(side='right')

        rows_data = []
        if n_digital > 0:
            rows_data.append((f'pymupdf4llm  ·  digital  ·  {n_digital} PDF(s)', _COR_PYMUPDF))
        if n_ocr > 0:
            rows_data.append((f'docling OCR  ·  escaneado  ·  {n_ocr} PDF(s)', _COR_DOCLING))

        for txt, cor in rows_data:
            r = ctk.CTkFrame(inner, fg_color='transparent')
            r.pack(fill='x', pady=(2, 0))
            ctk.CTkLabel(r, text='●', font=_font(10), text_color=cor,
                         width=14).pack(side='left')
            ctk.CTkLabel(r, text=txt, font=_font(11), text_color=TEXT_SEC,
                         anchor='w').pack(side='left', padx=(2, 0))

        if n_ocr > 0 and not _get_setting('usar_ocr', True):
            r_ocr = ctk.CTkFrame(inner, fg_color='transparent')
            r_ocr.pack(fill='x', pady=(6, 0))
            ctk.CTkLabel(r_ocr, text='○', font=_font(10), text_color=GRAY_TEXT,
                         width=14).pack(side='left')
            ctk.CTkLabel(r_ocr,
                         text=f'OCR desativado — {n_ocr} escaneado(s) serão ignorados'
                              '  ·  ativar em Configurações',
                         font=_font(11), text_color=GRAY_TEXT,
                         anchor='w').pack(side='left', padx=(2, 0))

        self._frame_analise.grid()

    def _remover_duplicatas_e3(self):
        vistos = set()
        nova_selecao = []
        for item in self._selecao_raw:
            if item['arquivo'] not in vistos:
                nova_selecao.append(item)
                vistos.add(item['arquivo'])
        removidos = len(self._selecao_raw) - len(nova_selecao)
        self._selecao_raw = nova_selecao
        self._rebuild_artigos()
        self._lbl_status3.configure(
            text=f'✓  {removidos} entrada(s) duplicada(s) removida(s)')

    def _limpar_lista(self):
        self._selecao_raw = []
        self._selecao     = []
        self._pasta       = ''
        self._engine_labels.clear()
        self._entry_pasta3.delete(0, 'end')
        self._frame_analise.grid_remove()
        for w in self._frame_arts.winfo_children():
            w.destroy()
        self._lbl_sem_ia = ctk.CTkLabel(
            self._frame_arts,
            text='Selecione uma pasta, ou arraste PDFs diretamente aqui.',
            text_color=TEXT_SEC, font=_font(13))
        self._lbl_sem_ia.pack(pady=28)
        self._lbl_resumo.configure(text='')
        self._lbl_status3.configure(text='')
        self._lbl_preview_frags.configure(text='')
        self._btn_rem_dup3.pack_forget()
        self._btn_limpar.pack_forget()
        self._btn_extrair.configure(state='disabled')

    def _extrair(self):
        prontos = [a for a in self._selecao if a['encontrado']]
        if not prontos:
            return

        secoes_cfg   = self._get_secoes_config()
        fallback_cfg = self._var_fallback.get()
        fragmentar   = self._var_fragmentar.get()
        fmt          = 'md'
        _nome_base   = _get_setting('nome_base', '')
        frag_nome    = _nome_base or (os.path.basename(self._pasta) if self._pasta else 'extracao_parte')
        try:
            frag_kb = max(10, float(self._entry_frag_kb.get().strip().replace(',', '.')))
        except ValueError:
            frag_kb = 500.0

        var_supl   = self._sec_vars.get('suplementar')
        incluir_supl = self._var_tudo.get() or (var_supl.get() if var_supl else False)

        if self._pasta:
            # Pasta já definida — salva direto, sem diálogo
            if fragmentar:
                path = self._pasta
            else:
                nome_arq = (_get_setting('nome_base', '') or 'extracao_seletiva') + f'.{fmt}'
                path = os.path.join(self._pasta, nome_arq)
        else:
            # Sem pasta — pergunta onde salvar
            if fragmentar:
                path = filedialog.askdirectory(
                    title='Escolher pasta para salvar os fragmentos',
                    initialdir=os.path.expanduser('~'))
                if not path:
                    return
            else:
                _nome_dlg = _get_setting('nome_base', '') or 'extracao_seletiva'
                path = filedialog.asksaveasfilename(
                    title='Salvar extração seletiva',
                    defaultextension='.md',
                    initialfile=f'{_nome_dlg}.md',
                    filetypes=[('Markdown', '*.md'), ('Todos', '*.*')]
                )
                if not path:
                    return
                if not path.lower().endswith('.md'):
                    path += '.md'

        self._cancelando = False
        self._btn_extrair.configure(
            text='✕  Cancelar', fg_color=C_ERR, hover_color='#991818',
            command=self._cancelar_extracao)
        self._prog3.set(0)
        self._prog3.pack(side='left', padx=(10, 0))
        usar_ocr = _get_setting('usar_ocr', True)
        usar_ia_local = _get_setting('usar_ia_local', False)
        threading.Thread(
            target=self._extrair_worker,
            args=(prontos, path, secoes_cfg, fallback_cfg, incluir_supl,
                  usar_ocr, fragmentar, frag_nome, frag_kb, fmt,
                  dict(getattr(self, '_tipos_pdf', {})), usar_ia_local),
            daemon=True).start()

    def _cancelar_extracao(self):
        self._cancelando = True
        self._btn_extrair.configure(state='disabled')
        self._lbl_status3.configure(text='Cancelando…')

    def _extrair_worker(self, artigos, path_saida, secoes_cfg, fallback_cfg,
                        incluir_supl, usar_ocr, fragmentar, frag_nome, frag_kb, fmt='txt',
                        tipos_pdf=None, usar_ia_local=False):
        tipos_pdf = tipos_pdf or {}
        blocos = []
        stats  = {'completos': 0, 'adaptados': 0, 'reviews': 0,
                  'ignorados': 0, 'erros': 0}
        total       = len(artigos)
        processados = 0

        def _kb(art):
            try:
                return max(1.0, os.path.getsize(art['caminho']) / 1024)
            except OSError:
                return 2048.0

        kbs = [_kb(a) for a in artigos]

        def _peso(idx):
            tipo = tipos_pdf.get(artigos[idx]['arquivo'], 'digital')
            rate = _TAXA_OCR_S_KB if (tipo == 'escaneado' and usar_ocr) else _TAXA_DIGITAL_S_KB
            return kbs[idx] * rate

        pesos         = [_peso(j) for j in range(total)]
        peso_total    = sum(pesos) or 1.0
        peso_acum     = 0.0
        taxa_dig_real = []   # taxas reais em s/KB para PDFs digitais
        taxa_ocr_real = []   # taxas reais em s/KB para PDFs OCR

        for i, art in enumerate(artigos, 1):
            if self._cancelando:
                break

            pct = peso_acum / peso_total

            # Taxas calibradas: usa médias reais se já houver amostras
            taxa_ref_dig = ((sum(taxa_dig_real) / len(taxa_dig_real))
                            if taxa_dig_real else _TAXA_DIGITAL_S_KB)
            taxa_ref_ocr = ((sum(taxa_ocr_real) / len(taxa_ocr_real))
                            if taxa_ocr_real else _TAXA_OCR_S_KB)

            # Tempo restante = taxa calibrada × tamanho real de cada artigo pendente
            t_rest = sum(
                taxa_ref_ocr * kbs[j]
                if (tipos_pdf.get(artigos[j]['arquivo'], 'digital') == 'escaneado' and usar_ocr)
                else taxa_ref_dig * kbs[j]
                for j in range(i - 1, total)
            )
            t_str = f'{t_rest:.0f}s' if t_rest < 60 else f'{t_rest / 60:.1f}min'
            status_txt = f'Extraindo {i}/{total}: {art["arquivo"]} · ~{t_str} restantes'

            self.after(0, lambda t=status_txt, p=pct: (
                self._lbl_status3.configure(text=t),
                self._prog3.set(p)
            ))

            t_art_inicio = time.time()
            partes       = []
            secoes_usadas = secoes_cfg[:]

            try:
                # Detecção antecipada de PDF inteiramente suplementar
                if not incluir_supl and _e_suplementar_integral(art['caminho']):
                    stats['ignorados'] += 1
                    blocos.append(
                        '--- INICIO ARTIGO ---\n'
                        f'ARQUIVO_ORIGINAL: {art["arquivo"]}\n'
                        f'STATUS: IGNORADO — material suplementar integral\n'
                        f'--- FIM ARTIGO ---'
                    )
                    peso_acum += pesos[i - 1]
                    processados += 1
                    continue

                # Opt-out de OCR: pular PDFs escaneados se usuário desativou
                if not usar_ocr and detectar_tipo_pdf(art['caminho']) == 'escaneado':
                    stats['ignorados'] += 1
                    blocos.append(
                        '--- INICIO ARTIGO ---\n'
                        f'ARQUIVO_ORIGINAL: {art["arquivo"]}\n'
                        f'STATUS: IGNORADO — PDF escaneado (OCR desativado pelo usuário)\n'
                        f'--- FIM ARTIGO ---'
                    )
                    peso_acum += pesos[i - 1]
                    processados += 1
                    continue

                md, _ = converter_pdf(art['caminho'])

                if 'tudo' in secoes_cfg:
                    partes.append(md)
                    stats['completos'] += 1
                else:
                    incluir_refs = 'referencias' in secoes_cfg

                    # Mudança 2: detecção de review
                    if _e_review(art['caminho'], md):
                        excluir_rev = set()
                        if not incluir_refs:
                            excluir_rev.add('referencias')
                        if not incluir_supl:
                            excluir_rev.add('suplementar')
                        conteudo_rev = _md_remover_secoes(md, excluir_rev) if excluir_rev else md
                        partes.append(f'[REVIEW — artigo entregue completo]\n\n{conteudo_rev}')
                        secoes_usadas = ['review (artigo completo)']
                        stats['reviews'] += 1
                    else:
                        # Mudança 3: extração com suporte a compostos
                        encontradas, nao_encontradas, partes_sec = _extrair_secoes(md, secoes_cfg)
                        partes.extend(partes_sec)

                        # Fallback via IA local (Ollama): só roda se o regex/fuzzy
                        # deixou seções sem encontrar e a opção está ligada nas
                        # configurações. Falha silenciosa -> segue pro fallback antigo.
                        if nao_encontradas and usar_ia_local:
                            partes_ia, encontradas_ia = _fallback_secoes_ia(md, nao_encontradas)
                            if encontradas_ia:
                                partes.extend(partes_ia)
                                encontradas = encontradas + encontradas_ia
                                nao_encontradas = [s for s in nao_encontradas
                                                    if s not in encontradas_ia]

                        # Mudança 1: fallback = artigo completo menos seções já extraídas
                        if nao_encontradas and fallback_cfg:
                            excluir_fb = set(encontradas)
                            if not incluir_refs:
                                excluir_fb.add('referencias')
                            if not incluir_supl:
                                excluir_fb.add('suplementar')
                            resto = _md_remover_secoes(md, excluir_fb)
                            nomes = ', '.join(nao_encontradas)
                            if resto.strip():
                                partes.append(
                                    f'[RESTANTE — {nomes} não detectados separadamente]\n\n{resto}')
                            secoes_usadas = (encontradas + ['restante (fallback)']
                                             if encontradas else ['tudo (fallback)'])
                            stats['adaptados'] += 1
                        elif not encontradas:
                            partes.append('[Nenhuma seção detectada — artigo não extraído]')
                            stats['adaptados'] += 1
                        elif nao_encontradas:
                            secoes_usadas = encontradas
                            nomes = ', '.join(nao_encontradas)
                            partes.insert(0, f'[Seções não encontradas: {nomes}]')
                            stats['adaptados'] += 1
                        else:
                            secoes_usadas = encontradas
                            stats['completos'] += 1

            except Exception as exc:
                partes.append(f'[ERRO ao extrair: {exc}]')
                stats['erros'] += 1

            blocos.append(
                '--- INICIO ARTIGO ---\n'
                f'ARQUIVO_ORIGINAL: {art["arquivo"]}\n'
                f'SECOES_EXTRAIDAS: {", ".join(secoes_usadas)}\n\n'
                + '\n\n'.join(partes)
                + '\n--- FIM ARTIGO ---'
            )
            t_art    = time.time() - t_art_inicio
            tipo_art = tipos_pdf.get(art['arquivo'], 'digital')
            taxa_art = t_art / kbs[i - 1]
            if tipo_art == 'escaneado' and usar_ocr:
                taxa_ocr_real.append(taxa_art)
            else:
                taxa_dig_real.append(taxa_art)
            peso_acum += pesos[i - 1]
            processados += 1

        cancelado = self._cancelando
        self.after(0, lambda: self._prog3.set(1.0))

        if blocos:
            try:
                if fragmentar:
                    fragmentos = _fragmentar_blocos(blocos, frag_kb)
                    n_frags    = len(fragmentos)
                    kb_frags   = [sum(len(b.encode('utf-8')) for b in f) / 1024
                                   for f in fragmentos]
                    for j, frag in enumerate(fragmentos, 1):
                        nome_arq = os.path.join(path_saida, f'{frag_nome}_{j}.{fmt}')
                        with open(nome_arq, 'w', encoding='utf-8') as f:
                            f.write('\n\n'.join(frag))
                    if not cancelado:
                        _st = dict(stats); _st['total'] = processados
                        self.after(0, lambda p=path_saida, t=processados, n=n_frags,
                                   kb=kb_frags, s=_st:
                                   self._extracao_concluida_frag(p, t, n, kb, s))
                else:
                    conteudo = '\n\n'.join(blocos)
                    with open(path_saida, 'w', encoding='utf-8') as f:
                        f.write(conteudo)
                    if not cancelado:
                        _st = dict(stats); _st['total'] = processados
                        self.after(0, lambda s=_st:
                                   self._extracao_concluida(path_saida, processados, s))
            except Exception as exc:
                self.after(0, lambda e=exc: messagebox.showerror('Erro ao salvar', str(e)))

        def _done():
            self._prog3.pack_forget()
            self._btn_extrair.configure(
                text='↓  Extrair artigos', fg_color=GREEN, hover_color=GREEN_HOV,
                command=self._extrair, state='normal')
            if cancelado:
                self._lbl_status3.configure(
                    text=f'Cancelado — {processados} de {total} artigo(s) processado(s)')

        self.after(0, _done)

    def _mostrar_ajuda_fragmentar(self):
        MODELOS = [
            ('Claude Sonnet / Opus',   500,  '200K tokens de contexto'),
            ('GPT-4o / GPT-4.1',       300,  '128K tokens de contexto'),
            ('Gemini 1.5 / 2.0 Flash', 2500, '1M tokens de contexto'),
            ('DeepSeek V3 / R1',       300,  '128K tokens de contexto'),
        ]

        win = ctk.CTkToplevel(self)
        win.title('Tamanho recomendado por modelo')
        win.resizable(False, False)
        win.withdraw()

        frame = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True, padx=0, pady=0)

        ctk.CTkLabel(frame, text='Tamanhos recomendados por modelo de IA',
                     font=_font(14, 'bold'), text_color=TEXT_PRI
                     ).pack(padx=20, pady=(16, 2), anchor='w')
        ctk.CTkLabel(frame,
                     text='Valores deixam margem para o prompt e a resposta da IA.',
                     font=_font(11), text_color=TEXT_SEC
                     ).pack(padx=20, pady=(0, 8), anchor='w')

        for modelo, kb, ctx in MODELOS:
            row = ctk.CTkFrame(frame, fg_color='transparent')
            row.pack(fill='x', padx=20, pady=3)
            ctk.CTkLabel(row, text=modelo, font=_font(13),
                         text_color=TEXT_PRI, width=200, anchor='w').pack(side='left')
            ctk.CTkLabel(row, text=f'~{kb} KB', font=_font(13, 'bold'),
                         text_color=ACCENT, width=70).pack(side='left')
            ctk.CTkLabel(row, text=ctx, font=_font(11),
                         text_color=TEXT_SEC).pack(side='left', padx=(4, 0))

            def _usar(k=kb):
                self._entry_frag_kb.configure(state='normal')
                self._entry_frag_kb.delete(0, 'end')
                self._entry_frag_kb.insert(0, str(k))
                if not self._var_fragmentar.get():
                    self._var_fragmentar.set(True)
                    self._on_fragmentar_toggle()
                win.destroy()

            ctk.CTkButton(row, text='Usar', width=52, height=26,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=_usar).pack(side='right')

        ctk.CTkButton(frame, text='Fechar', width=90, height=30,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(13),
                     command=win.destroy).pack(pady=(12, 16))

        win.update_idletasks()
        win.geometry('480x270')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _abrir_settings(self):
        SettingsDialog(self)

    def _extracao_concluida(self, path_saida, total, stats):
        self._lbl_status3.configure(text=f'✓  {total} artigo(s) extraídos')
        if _get_setting('mostrar_resumo', True):
            StatsWindow(self, stats, path_saida, fragmentado=False)
        elif _get_setting('perguntar_abrir_pasta', True):
            if messagebox.askyesno('Concluído',
                                   f'Extração salva em:\n{path_saida}\n\nAbrir a pasta?'):
                abrir_no_sistema(os.path.dirname(path_saida))

    def _extracao_concluida_frag(self, pasta, total_arts, n_frags, kb_frags, stats):
        self._lbl_status3.configure(
            text=f'✓  {total_arts} artigo(s) → {n_frags} arquivo(s)')
        if _get_setting('mostrar_resumo', True):
            StatsWindow(self, stats, pasta,
                        fragmentado=True, n_frags=n_frags, kb_frags=kb_frags)
        elif _get_setting('perguntar_abrir_pasta', True):
            resumo = '  |  '.join(f'parte {i+1}: ~{kb:.0f} KB'
                                   for i, kb in enumerate(kb_frags))
            if messagebox.askyesno(
                    'Concluído',
                    f'{total_arts} artigo(s) divididos em {n_frags} arquivo(s)\n\n'
                    f'{resumo}\n\n'
                    f'Pasta: {pasta}\n\nAbrir a pasta?'):
                abrir_no_sistema(pasta)


# ══════════════════════════════════════ app ══════════════════════════════════════

class ExcerptaApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD.require(self)
        except Exception:
            pass
        self.title('Excerpta')
        self.geometry('1200x880')
        self.minsize(1060, 760)
        self.configure(fg_color=BG_WINDOW)

        frame = Etapa3Frame(self, self)
        frame.pack(fill='both', expand=True)


if __name__ == '__main__':
    app = ExcerptaApp()
    app.mainloop()
