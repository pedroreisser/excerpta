"""Log técnico de diagnóstico (excerpta_debug.log).

Separado do arquivo de saída da extração: aqui vai o rastro técnico que o
usuário anexa quando reporta um problema.
"""

import logging
import os
import platform

from motor_pdf import DOCLING_OK, PYMUPDF4LLM_OK


# Log de diagnóstico: técnico, com traceback, pensado para o usuário anexar e
# enviar quando reportar um problema — diferente do arquivo de saída da
# extração em si.
logger = logging.getLogger('excerpta')
logger.setLevel(logging.DEBUG)


def setup_logging(dest_folder, enabled):
    """(Re)configura o log de diagnóstico desta execução; None se desativado.

    Grava em <dest_folder>/excerpta_debug.log. Reseta os handlers a cada
    chamada porque a mesma sessão pode rodar várias extrações.
    """
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    if not enabled:
        return None
    log_path = os.path.join(dest_folder, 'excerpta_debug.log')
    handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.info('Início da execução | Python %s | %s', platform.python_version(), platform.platform())
    logger.info('Motores disponíveis | pymupdf4llm=%s docling=%s', PYMUPDF4LLM_OK, DOCLING_OK)
    return log_path
