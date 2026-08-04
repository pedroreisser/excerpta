"""Preferências do usuário e histórico de pastas recentes.

Ambos são persistidos em JSON ao lado do programa. Módulo-folha: não importa
nada do resto do projeto.
"""

import json
import os


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


OLLAMA_URL_PADRAO    = 'http://localhost:11434'
OLLAMA_MODELO_PADRAO = 'qwen2.5:7b-instruct'
OLLAMA_TIMEOUT_S     = 25
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
