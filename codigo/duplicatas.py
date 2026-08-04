"""Detecção de duplicatas na lista de artigos.

Duplicata exata é resolvida por hash do conteúdo; nomes truncados em pontos
diferentes viram "possíveis duplicatas", que exigem confirmação do usuário.
O cache de hash é sempre recebido por parâmetro — não há estado global aqui.
"""

import hashlib
import os


def _hash_arquivo(caminho, cache):
    """MD5 do conteúdo do arquivo, com cache por (caminho, mtime, tamanho)."""
    try:
        st = os.stat(caminho)
    except OSError:
        return None
    chave = (caminho, st.st_mtime, st.st_size)
    entrada = cache.get(caminho)
    if entrada and entrada[0] == chave:
        return entrada[1]
    h = hashlib.md5()
    try:
        with open(caminho, 'rb') as f:
            for bloco in iter(lambda: f.read(1 << 20), b''):
                h.update(bloco)
    except OSError:
        return None
    digest = h.hexdigest()
    cache[caminho] = (chave, digest)
    return digest


_MIN_PREFIXO_POSSIVEL_DUP = 15


def _stem_normalizado(nome):
    stem, _ext = os.path.splitext(nome)
    return stem.strip().lower()


def _detectar_grupos_possiveis_duplicatas(artigos, hash_cache, excluir):
    """Agrupa arquivos cujo nome normalizado de um é prefixo do outro — sinal de
    que é o mesmo paper salvo duas vezes com o título truncado em pontos
    diferentes (ex.: Zotero) — mas o conteúdo não é byte-idêntico, então não dá
    pra remover sozinho: precisa de confirmação manual do usuário.
    Retorna lista de grupos (cada grupo é uma lista de nomes de arquivo)."""
    candidatos = [a for a in artigos if a['encontrado'] and a['arquivo'] not in excluir]
    stems = [_stem_normalizado(a['arquivo']) for a in candidatos]

    pai = {}

    def find(x):
        raiz = x
        while pai[raiz] != raiz:
            raiz = pai[raiz]
        while pai[x] != raiz:
            pai[x], x = raiz, pai[x]
        return raiz

    def unir(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            pai[ra] = rb

    for i in range(len(candidatos)):
        for j in range(i + 1, len(candidatos)):
            menor, maior = ((stems[i], stems[j]) if len(stems[i]) <= len(stems[j])
                            else (stems[j], stems[i]))
            if len(menor) < _MIN_PREFIXO_POSSIVEL_DUP or not maior.startswith(menor):
                continue
            h1 = _hash_arquivo(candidatos[i]['caminho'], hash_cache)
            h2 = _hash_arquivo(candidatos[j]['caminho'], hash_cache)
            if h1 and h2 and h1 == h2:
                continue
            nome_i, nome_j = candidatos[i]['arquivo'], candidatos[j]['arquivo']
            pai.setdefault(nome_i, nome_i)
            pai.setdefault(nome_j, nome_j)
            unir(nome_i, nome_j)

    grupos = {}
    for nome in pai:
        grupos.setdefault(find(nome), []).append(nome)
    return list(grupos.values())


def extrair_chave_zotero(caminho):
    sem_ext = os.path.splitext(os.path.basename(caminho))[0]
    return sem_ext.split(" - ")[0].strip() if " - " in sem_ext else sem_ext.strip()
