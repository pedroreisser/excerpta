"""Pipeline de extração: percorre os artigos e grava o resultado.

Não conhece a GUI. Todo contato com a interface passa pelos callbacks
recebidos em `executar` — a camada de cima é que decide como (e em que
thread) desenhar o progresso.
"""

import os
import time

from log import logger, setup_logging
from motor_pdf import (
    _TAXA_DIGITAL_S_KB, _TAXA_OCR_S_KB,
    _e_suplementar_integral, converter_pdf, detectar_tipo_pdf,
)
from secoes import (
    _extrair_secoes, _fallback_secoes_ia, _md_remover_secoes,
    _review_com_motivo, diagnostico_estrutura,
)


def _truncar_nome(nome, maxlen=45):
    """Encurta o nome do arquivo para não distorcer o layout do rodapé."""
    if len(nome) <= maxlen:
        return nome
    base, ext = os.path.splitext(nome)
    disponivel = maxlen - len(ext) - 1
    return f'{base[:disponivel]}…{ext}'


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


def executar(artigos, path_saida, secoes_cfg, fallback_cfg,
             incluir_supl, usar_ocr, fragmentar, frag_nome, frag_kb, fmt='txt',
             tipos_pdf=None, usar_ia_local=False, log_diagnostico=True, *,
             deve_cancelar, on_status, on_progresso, on_concluido,
             on_concluido_frag, on_erro_salvar, on_fim):
    """Roda a extração dos artigos e grava a saída.

    Callbacks, todos obrigatórios:
      deve_cancelar()                       -> bool, consultado a cada artigo
      on_status(texto, pct)                 -> texto do rodapé + barra
      on_progresso(pct)                     -> só a barra
      on_concluido(path, n, stats, log)     -> saída em arquivo único
      on_concluido_frag(pasta, n, n_frags, kb_frags, stats, log)
      on_erro_salvar(exc)                   -> falha ao gravar
      on_fim(cancelado, processados, total) -> sempre, no encerramento
    """
    tipos_pdf = tipos_pdf or {}
    blocos = []
    stats  = {'completos': 0, 'adaptados': 0, 'reviews': 0,
              'ignorados': 0, 'erros': 0}
    total       = len(artigos)
    processados = 0

    dest_folder = path_saida if fragmentar else os.path.dirname(path_saida)
    debug_log_path = setup_logging(dest_folder or '.', log_diagnostico)
    if debug_log_path:
        logger.info('Extração iniciada | %d artigo(s) | secoes=%s | fallback=%s | '
                    'ocr=%s | ia_local=%s', total, secoes_cfg, fallback_cfg,
                    usar_ocr, usar_ia_local)

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
        if deve_cancelar():
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
        nome_curto = _truncar_nome(art['arquivo'])
        status_txt = f'Extraindo {i}/{total}: {nome_curto} · ~{t_str} restantes'

        on_status(status_txt, pct)

        t_art_inicio = time.time()
        partes       = []
        secoes_usadas = secoes_cfg[:]
        art_erro      = False

        try:
            # Detecção antecipada de PDF inteiramente suplementar
            if not incluir_supl and _e_suplementar_integral(art['caminho']):
                stats['ignorados'] += 1
                if debug_log_path:
                    logger.info('Ignorado (suplementar integral) | arquivo=%s', art['arquivo'])
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
            # tipo já calculado pela GUI; só redetecta se veio faltando
            tipo_art = (tipos_pdf.get(art['arquivo'])
                        or detectar_tipo_pdf(art['caminho']))
            if not usar_ocr and tipo_art == 'escaneado':
                stats['ignorados'] += 1
                if debug_log_path:
                    logger.info('Ignorado (OCR desativado) | arquivo=%s', art['arquivo'])
                blocos.append(
                    '--- INICIO ARTIGO ---\n'
                    f'ARQUIVO_ORIGINAL: {art["arquivo"]}\n'
                    f'STATUS: IGNORADO — PDF escaneado (OCR desativado pelo usuário)\n'
                    f'--- FIM ARTIGO ---'
                )
                peso_acum += pesos[i - 1]
                processados += 1
                continue

            md, _ = converter_pdf(art['caminho'], tipo=tipo_art)

            if debug_log_path:
                d = diagnostico_estrutura(md)
                logger.info(
                    'Estrutura | arquivo=%s | cabecalhos=%d | hierarquia_plana=%s '
                    '| niveis=%s | classificados=%s | sem_classificar=%d %s',
                    art['arquivo'], d['cabecalhos'], d['plana'], d['niveis'],
                    d['classificados'] or '{}', d['total_nao_classificados'],
                    d['nao_classificados'])

            if 'tudo' in secoes_cfg:
                partes.append(md)
                stats['completos'] += 1
            else:
                incluir_refs = 'referencias' in secoes_cfg

                # Mudança 2: detecção de review
                e_review, motivo_review = _review_com_motivo(art['caminho'], md)
                if debug_log_path:
                    logger.info('Classificacao | arquivo=%s | review=%s | motivo=%s',
                                art['arquivo'], e_review, motivo_review)
                if e_review:
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
                    if debug_log_path:
                        logger.info(
                            'Secoes (regex) | arquivo=%s | pedidas=%s | achadas=%s | faltando=%s',
                            art['arquivo'], secoes_cfg, encontradas or '-',
                            nao_encontradas or '-')

                    # Fallback via IA local (Ollama): só roda se o regex/fuzzy
                    # deixou seções sem encontrar e a opção está ligada nas
                    # configurações. Falha silenciosa -> segue pro fallback antigo.
                    if nao_encontradas and usar_ia_local:
                        t_ia = time.time()
                        partes_ia, encontradas_ia = _fallback_secoes_ia(
                            md, nao_encontradas, deve_cancelar=deve_cancelar)
                        if debug_log_path:
                            logger.info(
                                'Secoes (IA local) | arquivo=%s | tentou=%s | '
                                'recuperou=%s | %.1fs',
                                art['arquivo'], nao_encontradas,
                                encontradas_ia or 'nada', time.time() - t_ia)
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
            art_erro = True
            if debug_log_path:
                logger.exception('Falha ao extrair artigo | arquivo=%s', art['arquivo'])

        if debug_log_path and not art_erro:
            logger.info('Artigo processado | arquivo=%s | secoes=%s',
                       art['arquivo'], ', '.join(secoes_usadas))

        blocos.append(
            '--- INICIO ARTIGO ---\n'
            f'ARQUIVO_ORIGINAL: {art["arquivo"]}\n'
            f'SECOES_EXTRAIDAS: {", ".join(secoes_usadas)}\n\n'
            + '\n\n'.join(partes)
            + '\n--- FIM ARTIGO ---'
        )
        t_art    = time.time() - t_art_inicio
        taxa_art = t_art / kbs[i - 1]
        if tipo_art == 'escaneado' and usar_ocr:
            taxa_ocr_real.append(taxa_art)
        else:
            taxa_dig_real.append(taxa_art)
        peso_acum += pesos[i - 1]
        processados += 1

    cancelado = deve_cancelar()
    on_progresso(1.0)

    if debug_log_path:
        logger.info('Fim da execução | %s | cancelado=%s', stats, cancelado)

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
                    on_concluido_frag(path_saida, processados, n_frags,
                                      kb_frags, _st, debug_log_path)
            else:
                conteudo = '\n\n'.join(blocos)
                with open(path_saida, 'w', encoding='utf-8') as f:
                    f.write(conteudo)
                if not cancelado:
                    _st = dict(stats); _st['total'] = processados
                    on_concluido(path_saida, processados, _st, debug_log_path)
        except Exception as exc:
            on_erro_salvar(exc)

    on_fim(cancelado, processados, total)
