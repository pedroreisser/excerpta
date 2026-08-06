"""Acesso à API HTTP local do Ollama.

O Ollama serve em http://localhost:11434 uma API REST sem autenticação.
Este módulo concentra todo o protocolo: listar modelos instalados
(/api/tags), ver quais estão carregados em memória (/api/ps), baixar
modelo com progresso (/api/pull) e gerar texto (/api/generate).

Módulo-folha: só stdlib + constantes de config, nada de tkinter, nada do
resto do projeto. Segue o mesmo padrão do zotero_bridge.
"""

import json
import time
import urllib.error
import urllib.request

from config import (OLLAMA_KEEP_ALIVE, OLLAMA_MODELO_PADRAO, OLLAMA_TIMEOUT_S,
                    OLLAMA_URL_PADRAO)


# Estados de um modelo, do ponto de vista do usuário:
#   AUSENTE   — não consta em /api/tags (precisa de pull)
#   INSTALADO — baixado, mas ainda não carregado em memória (a primeira
#               chamada vai pagar o custo do load, que pode levar minutos)
#   CARREGADO — aparece em /api/ps, pronto pra responder rápido
AUSENTE   = 'ausente'
INSTALADO = 'instalado'
CARREGADO = 'carregado'


class OllamaErro(Exception):
    """Falha ao falar com a API local do Ollama."""


class OllamaIndisponivel(OllamaErro):
    """Serviço fora do ar, conexão recusada ou tempo esgotado."""


def _post_json(url_base, caminho, payload, timeout):
    req = urllib.request.Request(
        f'{url_base.rstrip("/")}{caminho}',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OllamaIndisponivel(str(exc)) from exc


def _get_json(url_base, caminho, timeout=6):
    try:
        with urllib.request.urlopen(f'{url_base.rstrip("/")}{caminho}',
                                    timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OllamaIndisponivel(str(exc)) from exc


def _mesmo_modelo(a, b):
    """Compara nomes de modelo; sem tag explícita o Ollama assume :latest."""
    norm = lambda n: n if ':' in n else f'{n}:latest'
    return norm(a) == norm(b)


def listar_modelos(url=OLLAMA_URL_PADRAO):
    """Nomes dos modelos instalados na máquina (/api/tags)."""
    dados = _get_json(url, '/api/tags')
    return [m.get('name', '') for m in dados.get('models', []) if m.get('name')]


def modelos_carregados(url=OLLAMA_URL_PADRAO):
    """Nomes dos modelos atualmente carregados em memória (/api/ps)."""
    dados = _get_json(url, '/api/ps')
    return [m.get('name', '') for m in dados.get('models', []) if m.get('name')]


def status_modelo(nome, url=OLLAMA_URL_PADRAO):
    """AUSENTE, INSTALADO ou CARREGADO. Levanta OllamaErro se o serviço não responde."""
    if any(_mesmo_modelo(m, nome) for m in modelos_carregados(url)):
        return CARREGADO
    if any(_mesmo_modelo(m, nome) for m in listar_modelos(url)):
        return INSTALADO
    return AUSENTE


def puxar_modelo(nome, url=OLLAMA_URL_PADRAO, on_progresso=None,
                 deve_cancelar=None):
    """Baixa um modelo (equivalente a `ollama pull nome`), com progresso.

    on_progresso(status, baixado_bytes, total_bytes) é chamado a cada linha
    do stream; total_bytes pode ser 0 enquanto o Ollama ainda não sabe o
    tamanho. deve_cancelar() -> bool interrompe o download (o que já foi
    baixado fica em cache no Ollama e é retomado num pull futuro).

    Retorna True se concluiu, False se cancelado. Levanta OllamaErro em
    falha de rede ou erro reportado pelo próprio Ollama (ex.: modelo
    inexistente no catálogo).
    """
    req = urllib.request.Request(
        f'{url.rstrip("/")}/api/pull',
        data=json.dumps({'model': nome, 'stream': True}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        # Sem timeout de leitura curto: cada linha chega conforme o download
        # avança; 30 s sem nenhuma linha indica conexão morta.
        with urllib.request.urlopen(req, timeout=30) as resp:
            for linha in resp:
                if deve_cancelar and deve_cancelar():
                    return False
                try:
                    ev = json.loads(linha.decode('utf-8'))
                except ValueError:
                    continue
                if ev.get('error'):
                    raise OllamaErro(ev['error'])
                if on_progresso:
                    on_progresso(ev.get('status', ''),
                                 ev.get('completed', 0) or 0,
                                 ev.get('total', 0) or 0)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OllamaIndisponivel(str(exc)) from exc
    return True


def pre_carregar(nome, url=OLLAMA_URL_PADRAO, timeout=300):
    """Força o load do modelo em memória sem gerar nada (prompt vazio).

    Útil pra pagar o custo do carregamento na hora em que o usuário escolhe
    o modelo, em vez de no meio da extração do primeiro artigo. Timeout
    largo: carregar um 7B em CPU fraca pode levar minutos.
    """
    _post_json(url, '/api/generate',
               {'model': nome, 'prompt': '', 'stream': False}, timeout)


def gerar(prompt, modelo=OLLAMA_MODELO_PADRAO, url=OLLAMA_URL_PADRAO,
          timeout=OLLAMA_TIMEOUT_S, formato=None, options=None,
          keep_alive=None, deve_cancelar=None):
    """Chamada a /api/generate; devolve a string de resposta do modelo.

    Lê em streaming por dois motivos, mesmo devolvendo a resposta inteira no
    fim: permite abortar no meio da inferência (deve_cancelar) em vez de
    esperar o timeout inteiro, e evita uma conexão parada por minutos, que
    algumas redes derrubam. O prazo continua sendo de relógio, não por
    pedaço — senão um fluxo lento driblaria o timeout indefinidamente.
    """
    payload = {'model': modelo, 'prompt': prompt, 'stream': True}
    if formato:
        payload['format'] = formato
    if options:
        payload['options'] = options
    if keep_alive:
        payload['keep_alive'] = keep_alive

    req = urllib.request.Request(
        f'{url.rstrip("/")}/api/generate',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    inicio, partes = time.monotonic(), []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for linha in resp:
                if deve_cancelar and deve_cancelar():
                    raise OllamaErro('cancelado pelo usuário')
                if time.monotonic() - inicio > timeout:
                    raise OllamaIndisponivel(f'tempo esgotado ({timeout}s)')
                try:
                    ev = json.loads(linha.decode('utf-8'))
                except ValueError:
                    continue
                if ev.get('error'):
                    raise OllamaErro(ev['error'])
                partes.append(ev.get('response', ''))
                if ev.get('done'):
                    break
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OllamaIndisponivel(str(exc)) from exc
    return ''.join(partes)
