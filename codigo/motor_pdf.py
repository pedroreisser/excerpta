"""Motores de extração de PDF (pymupdf4llm / docling).

Detecção do tipo de PDF (digital vs. escaneado), conversão para Markdown,
limpeza das marcações residuais e detecção de material suplementar integral.
Módulo-folha: não importa nada do resto do projeto.
"""

import re

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

_TEMPO_DIGITAL_S   = 2.0
_TEMPO_OCR_S       = 45.0
# Taxas por KB usadas para estimar tempo proporcional ao tamanho do arquivo.
# Derivadas dos tempos acima assumindo um PDF "típico" de 2 MB.
_TAXA_DIGITAL_S_KB = _TEMPO_DIGITAL_S / 2048.0
_TAXA_OCR_S_KB     = _TEMPO_OCR_S     / 2048.0


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


_SUPLEMENTAR_INTEGRAL_RE = re.compile(
    r'supporting\s+information|supplementary\s+material|supplementary\s+data'
    r'|table\s+s\d+|figure\s+s\d+|supplemental|material\s+suplementar',
    re.IGNORECASE
)


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
