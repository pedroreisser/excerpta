"""Preferências do usuário e histórico de pastas recentes.

Ambos são persistidos em JSON ao lado do programa. Módulo-folha: não importa
nada do resto do projeto.
"""

import json
import os
import sys


def _pip_flags():
    """Flags para pip que evitam precisar de permissão de administrador."""
    if sys.platform == 'win32':
        return ['--user']
    # Ubuntu 23+/Debian 12+ exigem --break-system-packages para pip fora de venv
    return ['--break-system-packages']


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


OLLAMA_URL_PADRAO    = 'http://localhost:11434'
OLLAMA_MODELO_PADRAO = 'qwen2.5:7b-instruct'
# Sugestões pro dropdown de modelo — cobrem desde PC sem GPU até com GPU
# dedicada. O usuário pode digitar qualquer outro nome do catálogo do Ollama.
OLLAMA_MODELOS_RECOMENDADOS = [
    'qwen2.5:3b-instruct',
    'qwen2.5:7b-instruct',
    'qwen2.5:14b-instruct',
    'llama3.1:8b-instruct',
    'mistral:7b-instruct',
    'phi3:mini',
]
# Uma chamada com ~15k caracteres de contexto leva ~2 min num 7B em CPU
# comum — os 25 s antigos estouravam sempre, e o erro era engolido em
# silêncio. Ajustável na GUI, porque o tempo varia muito com o hardware.
OLLAMA_TIMEOUT_S     = 180
# Mantém o modelo carregado entre artigos. O padrão do Ollama é 5 min, e o
# maior intervalo medido entre duas chamadas num lote real foi de 4 min —
# perto demais: um trecho de artigos que não precisam da IA descarregaria o
# modelo e a chamada seguinte pagaria o recarregamento dentro do timeout.
OLLAMA_KEEP_ALIVE    = '30m'
OLLAMA_INSTALL_CMD   = 'curl -fsSL https://ollama.com/install.sh | sh'
OLLAMA_DOWNLOAD_URL  = 'https://ollama.com/download'


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
