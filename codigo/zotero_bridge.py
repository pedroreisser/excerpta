"""Acesso somente-leitura à API HTTP local do Zotero.

O Zotero desktop, quando aberto e com "Allow other applications on this
computer to communicate with Zotero" marcado em Preferências → Avançado,
serve a API web v3 em http://127.0.0.1:23119/api/ sem autenticação, com os
dados do banco local. O prefixo /users/0/ aponta para a biblioteca do
usuário logado.

Módulo-folha: só stdlib, nada de tkinter, nada do resto do projeto. Nunca
escreve — apenas GET.

A API local é recente e tem falhas conhecidas (anexo que volta incompleto,
metadado ausente), então nada aqui confia em uma resposta só: o caminho do
PDF sempre é confirmado com /file e depois com os.path.isfile.
"""

import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HOST_PADRAO = 'http://127.0.0.1:23119'
BASE_PADRAO = HOST_PADRAO + '/api/users/0'
TIMEOUT_S   = 8
_PAGINA     = 100
_MAX_PAGINAS = 500          # trava contra paginação que não termina

MSG_FECHADO = (
    'Zotero não encontrado. Verifique se o Zotero está aberto neste '
    'computador.'
)
MSG_BLOQUEADO = (
    'O Zotero respondeu, mas recusou o acesso. Em Zotero → Preferências → '
    'Avançado, marque "Allow other applications on this computer to '
    'communicate with Zotero".'
)


class ZoteroErro(Exception):
    """Falha ao falar com a API local do Zotero."""


class ZoteroIndisponivel(ZoteroErro):
    """Zotero fechado, conexão recusada ou tempo esgotado."""


class ZoteroBloqueado(ZoteroErro):
    """403 — a preferência de acesso externo não está habilitada."""


# ══════════════════════════════════════ camada HTTP ══════════════════════════

class _SemRedirect(urllib.request.HTTPRedirectHandler):
    """Impede o urllib de seguir o file:// que o /file devolve no 302."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener_sem_redirect = urllib.request.build_opener(_SemRedirect)


def _url(caminho, base, params):
    url = base.rstrip('/') + caminho
    if params:
        limpos = {k: v for k, v in params.items() if v is not None}
        if limpos:
            url += '?' + urllib.parse.urlencode(limpos)
    return url


def _get(caminho, base=BASE_PADRAO, timeout=TIMEOUT_S, **params):
    """GET que devolve (dados_json, headers). Traduz falhas em ZoteroErro."""
    try:
        with urllib.request.urlopen(_url(caminho, base, params), timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8')), dict(r.headers)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ZoteroBloqueado(MSG_BLOQUEADO) from e
        raise ZoteroErro(f'Zotero respondeu HTTP {e.code} em {caminho}') from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ZoteroIndisponivel(MSG_FECHADO) from e
    except (json.JSONDecodeError, ValueError) as e:
        raise ZoteroErro(f'Resposta inválida do Zotero em {caminho}') from e


def _get_paginado(caminho, base=BASE_PADRAO, timeout=TIMEOUT_S,
                  on_progresso=None, **params):
    """Percorre todas as páginas de um endpoint de lista."""
    itens, start, total = [], 0, None
    for _ in range(_MAX_PAGINAS):
        dados, headers = _get(caminho, base, timeout,
                              limit=_PAGINA, start=start, **params)
        if not isinstance(dados, list):
            break
        itens.extend(dados)
        if total is None:
            try:
                total = int(headers.get('Total-Results', len(dados)))
            except (TypeError, ValueError):
                total = len(dados)
        if on_progresso:
            on_progresso(len(itens), total)
        start += _PAGINA
        if not dados or start >= total:
            break
    return itens


def _caminho_do_anexo(anexo_key, base=BASE_PADRAO, timeout=TIMEOUT_S):
    """Resolve o caminho do arquivo no disco perguntando ao próprio Zotero.

    /items/<key>/file responde 302 com Location: file:///caminho/absoluto —
    isso cobre dataDir customizado, anexo linkado fora do storage/ e nomes
    acentuados. Devolve None se a chave não for um anexo de arquivo.

    Atenção: o Zotero devolve o caminho mesmo quando o arquivo não foi
    baixado para esta máquina. Quem chama precisa conferir a existência.
    """
    url = _url(f'/items/{anexo_key}/file', base, None)
    try:
        _opener_sem_redirect.open(url, timeout=timeout)
        return None                      # 200 sem redirect: não é o que esperamos
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ZoteroBloqueado(MSG_BLOQUEADO) from e
        if e.code not in (301, 302, 303, 307, 308):
            return None                  # 404 (não existe) ou 400 (não é anexo)
        destino = e.headers.get('Location')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ZoteroIndisponivel(MSG_FECHADO) from e

    if not destino:
        return None
    partes = urllib.parse.urlparse(destino)
    if partes.scheme != 'file':
        return None
    caminho = urllib.parse.unquote(partes.path)
    # No Windows o caminho vem como /C:/Users/... — a barra inicial sobra.
    if os.name == 'nt' and re.match(r'^/[A-Za-z]:', caminho):
        caminho = caminho[1:]
    return caminho or None


# ══════════════════════════════════════ API pública ══════════════════════════


def _formatar_autores(creators):
    nomes = []
    for c in creators or []:
        if not isinstance(c, dict):
            continue
        nome = (c.get('lastName') or c.get('name') or '').strip()
        if nome:
            nomes.append(nome)
    if not nomes:
        return ''
    if len(nomes) == 1:
        return nomes[0]
    if len(nomes) == 2:
        return f'{nomes[0]} e {nomes[1]}'
    return f'{nomes[0]} et al.'


def _extrair_ano(dados, meta):
    for valor in (meta.get('parsedDate'), dados.get('date')):
        m = re.search(r'(1[6-9]\d{2}|20\d{2})', str(valor or ''))
        if m:
            return m.group(1)
    return ''


def _pdfs_do_item(item, mapa_anexos, base, timeout):
    """PDFs de um item: (chave_do_principal, total_de_pdfs, tamanho).

    Três fontes, da mais barata para a mais cara, porque a API local às
    vezes devolve o vínculo de anexo incompleto:
      1. links.attachment — o anexo que o próprio Zotero elege como principal
      2. o lote de anexos da coleção, agrupado por parentItem
      3. /items/<key>/children, só para o item que sobrou sem nada
    """
    chave = item.get('key')
    principal, tamanho = None, None

    link = (item.get('links') or {}).get('attachment') or {}
    if link.get('attachmentType') == 'application/pdf':
        href = str(link.get('href') or '')
        if '/items/' in href:
            principal = href.rsplit('/items/', 1)[-1].strip('/') or None
            tamanho = link.get('attachmentSize')

    do_lote = mapa_anexos.get(chave, [])

    # Item que é ele próprio um anexo PDF solto na biblioteca
    dados_item = item.get('data') or {}
    if (dados_item.get('itemType') == 'attachment'
            and dados_item.get('contentType') == 'application/pdf'):
        return chave, 1, tamanho

    if not principal and not do_lote:
        try:
            filhos = _get(f'/items/{chave}/children', base, timeout)[0]
        except ZoteroErro:
            filhos = []
        do_lote = [{'key': f['key'], 'filename': (f.get('data') or {}).get('filename') or ''}
                   for f in filhos if isinstance(f, dict) and 'key' in f
                   and (f.get('data') or {}).get('contentType') == 'application/pdf']

    chaves = [a['key'] for a in do_lote]
    if principal and principal not in chaves:
        chaves.append(principal)
    if not principal and chaves:
        principal = chaves[0]
    return principal, len(chaves), tamanho


def _colecoes_planas(base, timeout):
    """Todas as coleções, sem aninhamento: [{'key','nome','n_itens'}, ...]."""
    saida = []
    for c in _get_paginado('/collections', base, timeout):
        if isinstance(c, dict) and 'key' in c:
            saida.append({
                'key': c['key'],
                'nome': (c.get('data') or {}).get('name') or '(sem nome)',
                'n_itens': (c.get('meta') or {}).get('numItems', 0),
            })
    return saida


def colecao_selecionada(host=HOST_PADRAO, base=BASE_PADRAO, timeout=4):
    """Coleção aberta agora na janela do Zotero, ou None se ele não responder.

    POST /connector/getSelectedCollection é o único endpoint que reflete o
    estado da interface — itens selecionados não são expostos por nenhum.
    Devolve {'nome', 'key', 'n_itens'}; 'key' é None na raiz da biblioteca ou
    quando o nome não identifica uma coleção só.
    """
    req = urllib.request.Request(
        host.rstrip('/') + '/connector/getSelectedCollection',
        data=b'{}', method='POST',
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            dados = json.loads(r.read().decode('utf-8'))
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    nome = (dados.get('name') or '').strip()
    if dados.get('id') is None:              # raiz da biblioteca
        return {'nome': nome or 'Minha Biblioteca', 'key': None, 'n_itens': None}
    # O id vem como "C9" (interno) e a API só conhece a chave de 8 caracteres;
    # não há campo ligando os dois, então o casamento é pelo nome.
    try:
        iguais = [c for c in _colecoes_planas(base, timeout) if c['nome'] == nome]
    except ZoteroErro:
        iguais = []
    if len(iguais) == 1:
        return {'nome': nome, 'key': iguais[0]['key'],
                'n_itens': iguais[0]['n_itens']}
    return {'nome': nome or '(sem nome)', 'key': None, 'n_itens': None}


def listar_itens(colecao_key, base=BASE_PADRAO, timeout=TIMEOUT_S,
                 on_progresso=None):
    """Itens de topo de uma coleção, no formato que resolver_pdfs consome."""
    brutos = _get_paginado(f'/collections/{colecao_key}/items/top', base,
                           timeout, on_progresso=on_progresso)
    # Anexos da coleção em lote: ~0,05 s para 59, contra ~10 s fazendo
    # /children item a item numa coleção de 1424.
    mapa = {}
    try:
        for a in _get_paginado(f'/collections/{colecao_key}/items', base,
                               timeout, itemType='attachment'):
            dados = (a or {}).get('data') or {}
            if dados.get('contentType') == 'application/pdf' and a.get('key'):
                pai = dados.get('parentItem')
                if isinstance(pai, str):
                    mapa.setdefault(pai, []).append(
                        {'key': a['key'], 'filename': dados.get('filename') or ''})
    except ZoteroErro:
        pass                                 # sem o lote, o /children cobre

    itens = []
    for it in brutos:
        if not isinstance(it, dict) or 'key' not in it:
            continue
        dados = it.get('data') or {}
        if dados.get('itemType') == 'note':
            continue
        principal, n_pdfs, tamanho = _pdfs_do_item(it, mapa, base, timeout)
        itens.append({
            'key': it['key'],
            'titulo': (dados.get('title') or '(sem título)').strip(),
            'autores': _formatar_autores(dados.get('creators')),
            'ano': _extrair_ano(dados, it.get('meta') or {}),
            'doi': (dados.get('DOI') or '').strip(),
            'anexo_key': principal,
            'n_pdfs': n_pdfs,
            'tamanho': tamanho,
        })
    return itens


_LIMIAR_CASAMENTO = 0.5     # sobreposição mínima do título para aceitar o par


def _palavras(texto):
    """Conjunto de palavras normalizadas (sem acento, minúsculas, 3+ letras)."""
    t = unicodedata.normalize('NFKD', (texto or '').lower())
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]{3,}', t))


def _casar_referencia(texto, base, timeout):
    """Acha na biblioteca o item que uma referência solta descreve.

    O Zotero, ao arrastar itens **pais**, não entrega arquivo nenhum — só os
    metadados formatados pelo Quick Copy (citação, JSON, etc.). Este é o
    caminho de volta: sobrenome + ano estreitam os candidatos (a busca da API
    é boa nisso), e a sobreposição de palavras do título desempata.

    Devolve (item_bruto, nota) ou (None, 0.0).
    """
    anos = re.findall(r'\b(?:19|20)\d{2}\b', texto)
    m = re.search(r'[^\W\d_]{2,}', texto, re.UNICODE)
    if not m or not anos:
        return None, 0.0
    sobrenome = m.group(0)
    alvo = _palavras(texto)

    melhor, melhor_nota = None, 0.0
    for ano in dict.fromkeys(reversed(anos)):     # o ano final costuma ser o certo
        try:
            candidatos, _ = _get('/items/top', base, timeout,
                                 q=f'{sobrenome} {ano}', limit=15)
        except ZoteroErro:
            continue
        for cand in candidatos or []:
            if not isinstance(cand, dict):
                continue
            titulo = (cand.get('data') or {}).get('title', '')
            palavras_titulo = _palavras(titulo)
            if not palavras_titulo:
                continue
            nota = len(palavras_titulo & alvo) / len(palavras_titulo)
            if nota > melhor_nota:
                melhor, melhor_nota = cand, nota
        if melhor_nota >= 0.99:
            break
    return (melhor, melhor_nota) if melhor_nota >= _LIMIAR_CASAMENTO else (None, melhor_nota)


def _referencias_do_texto(texto):
    """Separa o payload arrastado em referências individuais.

    Aceita CSL JSON (Quick Copy em modo de exportação) ou texto de citação,
    uma referência por parágrafo.
    """
    try:
        obj = json.loads(texto)
        regs = obj if isinstance(obj, list) else [obj]
        saida = []
        for r in regs:
            if not isinstance(r, dict):
                continue
            partes = [str(r.get('title') or '')]
            autores = r.get('author') or []
            if isinstance(autores, list) and autores:
                a = autores[0]
                if isinstance(a, dict):
                    partes.insert(0, str(a.get('family') or a.get('literal') or ''))
            emitido = r.get('issued') or {}
            if isinstance(emitido, dict):
                pecas = emitido.get('date-parts') or []
                if pecas and isinstance(pecas[0], list) and pecas[0]:
                    partes.append(str(pecas[0][0]))
            ref = ' '.join(p for p in partes if p).strip()
            if ref:
                saida.append(ref)
        if saida:
            return saida
    except (ValueError, TypeError):
        pass

    # Texto: cada parágrafo é uma referência. Junta linhas quebradas no meio.
    blocos, atual = [], []
    for linha in texto.splitlines():
        if linha.strip():
            atual.append(linha.strip())
        elif atual:
            blocos.append(' '.join(atual))
            atual = []
    if atual:
        blocos.append(' '.join(atual))
    return [b for b in blocos if len(b) > 30]


def itens_de_texto_arrastado(texto, base=BASE_PADRAO, timeout=TIMEOUT_S,
                             on_progresso=None):
    """Converte o texto arrastado do Zotero em itens desta biblioteca.

    Devolve (itens, nao_casadas): 'itens' no mesmo formato de listar_itens,
    prontos para resolver_pdfs; 'nao_casadas' são as referências que não deu
    para identificar com segurança.
    """
    referencias = _referencias_do_texto(texto)
    itens, nao_casadas, vistos = [], [], set()
    for i, ref in enumerate(referencias, 1):
        if on_progresso:
            on_progresso(i, len(referencias))
        bruto, _nota = _casar_referencia(ref, base, timeout)
        if not bruto:
            nao_casadas.append(ref[:90])
            continue
        chave = bruto.get('key')
        if chave in vistos:
            continue
        vistos.add(chave)
        dados = bruto.get('data') or {}
        principal, n_pdfs, tamanho = _pdfs_do_item(bruto, {}, base, timeout)
        itens.append({
            'key': chave,
            'titulo': (dados.get('title') or '(sem título)').strip(),
            'autores': _formatar_autores(dados.get('creators')),
            'ano': _extrair_ano(dados, bruto.get('meta') or {}),
            'doi': (dados.get('DOI') or '').strip(),
            'anexo_key': principal,
            'n_pdfs': n_pdfs,
            'tamanho': tamanho,
        })
    return itens, nao_casadas


def resolver_pdfs(itens, base=BASE_PADRAO, timeout=TIMEOUT_S, on_progresso=None):
    """Resolve o PDF principal de cada item num caminho existente no disco.

    Devolve (prontos, problemas):
      prontos   — dicts no formato que a lista do Excerpta consome:
                  {'arquivo', 'secoes': ['tudo'], 'caminho_completo'}
      problemas — {'sem_pdf':     [titulo, ...],
                   'sem_arquivo': [(titulo, caminho_esperado), ...],
                   'multiplos':   [(titulo, n_pdfs), ...]}

    'multiplos' é informativo: importa-se apenas o anexo principal, o mesmo
    que o Zotero destaca na interface dele.
    """
    prontos = []
    problemas = {'sem_pdf': [], 'sem_arquivo': [], 'multiplos': []}
    total = len(itens)

    for i, item in enumerate(itens, 1):
        if on_progresso:
            on_progresso(i, total)
        titulo = item.get('titulo') or '(sem título)'
        chave = item.get('anexo_key')
        if not chave:
            problemas['sem_pdf'].append(titulo)
            continue
        try:
            caminho = _caminho_do_anexo(chave, base, timeout)
        except ZoteroErro:
            caminho = None
        if not caminho:
            problemas['sem_pdf'].append(titulo)
            continue
        if not os.path.isfile(caminho):
            problemas['sem_arquivo'].append((titulo, caminho))
            continue
        if item.get('n_pdfs', 1) > 1:
            problemas['multiplos'].append((titulo, item['n_pdfs']))
        prontos.append({
            'arquivo': os.path.basename(caminho),
            'secoes': ['tudo'],
            'caminho_completo': caminho,
        })
    return prontos, problemas
