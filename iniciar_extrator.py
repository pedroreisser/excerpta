#!/usr/bin/env python3
"""Excerpta — launcher universal (Linux e Windows). Não requer administrador."""

import sys
import subprocess
import importlib.util
import os
import shutil
import threading

# ── Dependências ──────────────────────────────────────────────────────────────
# pymupdf4llm: extração de PDFs digitais (rápido, leve)
# customtkinter / tkinterdnd2: interface gráfica
DEPS_OBRIGATORIAS = [
    ("pymupdf4llm",   "pymupdf4llm"),
    ("customtkinter", "customtkinter"),
    ("tkinterdnd2",   "tkinterdnd2"),
]
# docling: OCR para PDFs escaneados (opcional, ~2 GB, lento de instalar)
DEPS_DOCLING = [("docling", "docling")]
# ollama: IA local opcional, usada só como reforço para achar seções que o
# regex/fuzzy não encontrou (não substitui o OCR — trabalha no texto já extraído)
OLLAMA_INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excerpta.py")
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".excerpta_settings.json")
IS_WIN   = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# Gerenciadores de pacotes suportados, em ordem de detecção.
# (binário, comando de instalação, pacote do Tk, pacote do pip)
PKG_MANAGERS = [
    ("apt-get", ["apt-get", "install", "-y"],                "python3-tk",      "python3-pip"),
    ("dnf",     ["dnf", "install", "-y"],                     "python3-tkinter", "python3-pip"),
    ("yum",     ["yum", "install", "-y"],                     "python3-tkinter", "python3-pip"),
    ("zypper",  ["zypper", "--non-interactive", "install"],   "python3-tk",      "python3-pip"),
    ("pacman",  ["pacman", "-S", "--noconfirm"],               "tk",              "python-pip"),
    ("apk",     ["apk", "add"],                                "python3-tkinter", "py3-pip"),
]
_ENV_JA_TENTOU = "_EXCERPTA_TENTOU_INSTALAR_SO"


def _pip_flags():
    """Flags para pip que evitam precisar de permissão de administrador."""
    if IS_WIN:
        return ["--user"]
    else:
        # Ubuntu 23+/Debian 12+ exigem --break-system-packages para pip fora de venv
        return ["--break-system-packages"]


def _tk_disponivel():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def _pip_disponivel():
    return importlib.util.find_spec("pip") is not None


def _detectar_gerenciador():
    """Retorna (binario, cmd_instalar, pkg_tk, pkg_pip) do primeiro gerenciador achado."""
    for binario, cmd, pkg_tk, pkg_pip in PKG_MANAGERS:
        if shutil.which(binario):
            return binario, cmd, pkg_tk, pkg_pip
    return None


def _instalar_pacotes_sistema(cmd, pacotes):
    """Instala pacotes do sistema operacional via sudo. Retorna True se ok."""
    try:
        res = subprocess.run(["sudo"] + cmd + pacotes)
        return res.returncode == 0
    except FileNotFoundError:
        # Sem sudo disponível — tenta direto (ex: já rodando como root)
        res = subprocess.run(cmd + pacotes)
        return res.returncode == 0


def _garantir_tk_e_pip():
    """No Linux, garante tkinter e pip no sistema, instalando via apt/dnf/pacman/…

    Independe da distro: detecta o gerenciador de pacotes disponível. Se
    faltar algo e conseguir instalar, reinicia o processo (os.execv) para
    já rodar com as dependências disponíveis.
    """
    faltando = []
    if not _tk_disponivel():
        faltando.append('tk')
    if not _pip_disponivel():
        faltando.append('pip')

    if not faltando:
        return True

    info = _detectar_gerenciador()

    if os.environ.get(_ENV_JA_TENTOU) == '1':
        print(f"\n⚠ Ainda faltam pacotes do sistema: {', '.join(faltando)}")
        if info:
            _, cmd, pkg_tk, pkg_pip = info
            pacotes = [p for p, nome in ((pkg_tk, 'tk'), (pkg_pip, 'pip')) if nome in faltando]
            print(f"Instale manualmente: sudo {' '.join(cmd)} {' '.join(pacotes)}")
        input("Pressione Enter para sair.")
        return False

    if not info:
        print("Não consegui detectar o gerenciador de pacotes da sua distribuição.")
        print(f"Instale manualmente os pacotes de sistema para: {', '.join(faltando)} "
              "(ex. Debian/Ubuntu: python3-tk, python3-pip)")
        input("Pressione Enter para sair.")
        return False

    binario, cmd, pkg_tk, pkg_pip = info
    pacotes = []
    if 'tk' in faltando:
        pacotes.append(pkg_tk)
    if 'pip' in faltando:
        pacotes.append(pkg_pip)

    print(f"Faltam pacotes do sistema ({binario} detectado): {', '.join(pacotes)}")
    print("Vou instalar agora — pode pedir sua senha de administrador.\n")
    ok = _instalar_pacotes_sistema(cmd, pacotes)
    if not ok:
        print("\n⚠ Falha ao instalar automaticamente.")
        print(f"Rode manualmente: sudo {' '.join(cmd)} {' '.join(pacotes)}")
        input("Pressione Enter para sair.")
        return False

    print("\n✓ Pacotes do sistema instalados. Reiniciando o Excerpta...\n")
    os.environ[_ENV_JA_TENTOU] = '1'
    os.execv(sys.executable, [sys.executable] + sys.argv)


def checar_faltando(deps):
    faltando = []
    for modulo, pacote in deps:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltando.append((modulo, pacote))
    return faltando


def _ollama_instalado():
    return shutil.which("ollama") is not None


def _ativar_ia_local_setting():
    """Liga 'usar_ia_local' em .excerpta_settings.json após instalar o Ollama."""
    import json
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            dados = json.load(f)
    except Exception:
        dados = {}
    dados['usar_ia_local'] = True
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _instalar_ollama(callback_log):
    """Roda o instalador oficial do Ollama com log ao vivo. Retorna True se ok."""
    if not IS_LINUX:
        callback_log("  ✗ Instalação automática só disponível no Linux. "
                      "Baixe em: https://ollama.com/download")
        return False
    callback_log("> Instalando Ollama (IA local, opcional)")
    try:
        proc = subprocess.Popen(
            ["sh", "-c", OLLAMA_INSTALL_CMD],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for linha in proc.stdout:
            callback_log("  " + linha.rstrip("\n"))
        code = proc.wait()
    except Exception as exc:
        callback_log(f"  ✗ Erro: {exc}")
        return False
    if code == 0:
        callback_log("  ✓ Ollama instalado com sucesso")
        _ativar_ia_local_setting()
        return True
    callback_log("  ✗ Falha ao instalar Ollama")
    return False


def _instalar_pacotes(pacotes, callback_log, callback_fim, instalar_ollama=False):
    """Instala lista de pacotes (+ Ollama, se pedido). Roda em thread separada."""
    erros = []
    flags = _pip_flags()
    for _mod, pacote in pacotes:
        if pacote == "docling":
            callback_log(f"> pip install {pacote}")
            callback_log("  (pacote grande, pode demorar 10-20 min e baixar ~2 GB)")
        else:
            callback_log(f"> pip install {pacote}")
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", pacote] + flags,
            capture_output=True, text=True
        )
        if res.returncode == 0:
            callback_log(f"  ✓ {pacote} instalado com sucesso")
            if pacote == "docling":
                callback_log("  ℹ  Modelos de OCR serão baixados no primeiro uso")
        else:
            # Tenta sem --user / sem --break-system-packages como último recurso
            res2 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", pacote],
                capture_output=True, text=True
            )
            if res2.returncode == 0:
                callback_log(f"  ✓ {pacote} instalado")
            else:
                erros.append(pacote)
                callback_log(f"  ✗ Falha: {(res.stderr or res2.stderr).strip()[:200]}")

    if instalar_ollama and not _instalar_ollama(callback_log):
        erros.append("ollama")

    callback_fim(erros)


# ── Interface gráfica ─────────────────────────────────────────────────────────

def _abrir_app():
    subprocess.Popen([sys.executable, SCRIPT])


def _instalar_gui(pacotes, root, depois_de_instalar, instalar_ollama=False):
    import tkinter as tk
    from tkinter import ttk, messagebox

    dlg = tk.Toplevel(root)
    dlg.title("Instalando dependências…")
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.transient(root)

    ttk.Label(dlg, text="Instalando pacotes, aguarde…",
              font=("TkDefaultFont", 10)).pack(padx=24, pady=(16, 6))

    barra = ttk.Progressbar(dlg, mode="indeterminate", length=340)
    barra.pack(padx=24, pady=(0, 6))
    barra.start(10)

    log = tk.Text(dlg, height=10, width=62, state="disabled",
                  font=("TkFixedFont", 8), bg="#1e1e1e", fg="#d4d4d4")
    log.pack(padx=24, pady=(0, 16))

    def _log(txt):
        log.config(state="normal")
        log.insert("end", txt + "\n")
        log.see("end")
        log.config(state="disabled")
        dlg.update()

    def _fim(erros):
        barra.stop()
        if erros:
            _log(f"\n⚠  Falha ao instalar: {', '.join(erros)}")
            pip_erros = [e for e in erros if e != "ollama"]
            partes = []
            if pip_erros:
                cmd = (f"pip install {' '.join(pip_erros)}" +
                       ("" if IS_WIN else " --break-system-packages"))
                partes.append(f"Tente manualmente no terminal:\n  {cmd}")
            if "ollama" in erros:
                partes.append(f"Ollama, manualmente:\n  {OLLAMA_INSTALL_CMD}")
            root.after(0, lambda: messagebox.showwarning(
                "Atenção",
                f"Não foi possível instalar: {', '.join(erros)}\n\n"
                + "\n\n".join(partes) + "\n\n"
                f"{'Obs: no Windows, abra o terminal como usuário normal (sem admin).' if IS_WIN else ''}"
            ))
        else:
            _log("\n✅ Tudo instalado! Abrindo o Excerpta…")
            root.after(800, lambda: [dlg.destroy(), depois_de_instalar()])

    threading.Thread(target=_instalar_pacotes,
                     args=(pacotes, _log, _fim, instalar_ollama),
                     daemon=True).start()
    dlg.wait_window()


def main_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.withdraw()

    # ── Checar deps obrigatórias ──────────────────────────────────────────────
    faltando = checar_faltando(DEPS_OBRIGATORIAS)
    tem_docling = not checar_faltando(DEPS_DOCLING)

    if not faltando and tem_docling:
        # Tudo instalado — abrir direto
        root.destroy()
        _abrir_app()
        return

    # ── Janela de boas-vindas ─────────────────────────────────────────────────
    root.deiconify()
    root.title("Excerpta — Configuração inicial")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=28)
    frame.pack()

    ttk.Label(frame, text="Excerpta",
              font=("TkDefaultFont", 14, "bold")).pack(pady=(0, 2))
    ttk.Label(frame,
              text="Ferramenta para extração de artigos científicos em PDF",
              foreground="gray").pack(pady=(0, 16))

    # Mostrar o que será instalado
    if faltando:
        ttk.Label(frame,
                  text="Dependências a instalar (nenhuma requer administrador):",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        for _, p in faltando:
            ttk.Label(frame, text=f"  • {p}", foreground="#333").pack(anchor="w")
        ttk.Label(frame, text="").pack()

    # Checkbox para docling (opcional)
    var_docling = tk.BooleanVar(value=not tem_docling)
    frame_ocr = ttk.Frame(frame)
    frame_ocr.pack(fill="x", pady=(0, 4))
    ttk.Checkbutton(frame_ocr,
                    text="Instalar suporte a OCR (PDFs escaneados)  ",
                    variable=var_docling).pack(side="left")
    ttk.Label(frame_ocr, text="  ≈2 GB, 10-20 min",
              foreground="gray", font=("TkDefaultFont", 8)).pack(side="left")

    # Checkbox para Ollama (opcional, só Linux por enquanto — instalador oficial
    # não cobre Windows/Mac). Não tem relação com OCR: OCR lê PDFs escaneados,
    # o Ollama só ajuda a localizar seções no texto que já foi extraído.
    ollama_ja_instalado = _ollama_instalado()
    var_ollama = tk.BooleanVar(value=False)
    if IS_LINUX and not ollama_ja_instalado:
        frame_ollama = ttk.Frame(frame)
        frame_ollama.pack(fill="x", pady=(0, 16))
        ttk.Checkbutton(frame_ollama,
                        text="Instalar IA local (Ollama) — reforço p/ achar seções  ",
                        variable=var_ollama).pack(side="left")
        ttk.Label(frame_ollama, text="  opcional, ~5 GB",
                  foreground="gray", font=("TkDefaultFont", 8)).pack(side="left")
    else:
        ttk.Label(frame, text="").pack(pady=(0, 12))

    if not faltando and not tem_docling:
        ttk.Label(frame,
                  text="Excerpta já está instalado.\nDeseja adicionar OCR ou IA local?",
                  justify="center", font=("TkDefaultFont", 10)
                  ).pack(pady=(0, 12))

    def _iniciar():
        pacotes = list(faltando)
        if var_docling.get() and not tem_docling:
            pacotes += DEPS_DOCLING
        instalar_ollama = var_ollama.get() and not ollama_ja_instalado
        if not pacotes and not instalar_ollama:
            root.destroy()
            _abrir_app()
            return
        _instalar_gui(pacotes, root, lambda: [root.destroy(), _abrir_app()],
                      instalar_ollama)

    frame_btn = ttk.Frame(frame)
    frame_btn.pack()
    ttk.Button(frame_btn, text="Instalar e abrir",
               command=_iniciar).pack(side="left", padx=6)
    ttk.Button(frame_btn,
               text="Abrir sem instalar" if not faltando else "Cancelar",
               command=lambda: [root.destroy(), (_abrir_app() if not faltando else None)]
               ).pack(side="left", padx=6)

    root.mainloop()


def main_console():
    """Fallback sem tkinter (Linux sem python3-tk instalado)."""
    print("=" * 52)
    print("  Excerpta — Configuração inicial")
    print("=" * 52)
    print()

    faltando = checar_faltando(DEPS_OBRIGATORIAS)
    tem_docling = not checar_faltando(DEPS_DOCLING)

    if not faltando and tem_docling:
        print("Tudo instalado. Abrindo Excerpta...")
        subprocess.Popen([sys.executable, SCRIPT])
        return

    if faltando:
        print("Dependências a instalar:")
        for _, p in faltando:
            print(f"  • {p}")
        print()
        resp = input("Instalar agora? [S/n]: ").strip().lower()
        if resp in ('n', 'no', 'nao', 'não'):
            print("Cancelado.")
            return

    if not tem_docling:
        print()
        print("Docling: OCR para PDFs escaneados (~2 GB, 10-20 min para instalar)")
        resp_ocr = input("Instalar suporte a OCR? [s/N]: ").strip().lower()
        if resp_ocr in ('s', 'y', 'sim', 'yes'):
            faltando = list(faltando) + DEPS_DOCLING

    instalar_ollama = False
    if IS_LINUX and not _ollama_instalado():
        print()
        print("Ollama: IA local opcional, reforço p/ achar seções que o regex não "
              "encontrou (não é OCR — trabalha no texto já extraído) (~5 GB)")
        resp_ollama = input("Instalar Ollama? [s/N]: ").strip().lower()
        if resp_ollama in ('s', 'y', 'sim', 'yes'):
            instalar_ollama = True

    print()

    def _log(txt):
        print(txt)

    erros = []

    def _fim(e):
        erros.extend(e)

    t = threading.Thread(target=_instalar_pacotes, args=(faltando, _log, _fim, instalar_ollama))
    t.start()
    t.join()

    if erros:
        print(f"\n⚠  Falha: {', '.join(erros)}")
        print("Tente manualmente:")
        for e in erros:
            if e == "ollama":
                print(f"  {OLLAMA_INSTALL_CMD}")
            else:
                print(f"  pip install {e} --break-system-packages")
    else:
        print("\n✓ Instalação concluída! Abrindo Excerpta…")
        subprocess.Popen([sys.executable, SCRIPT])


if __name__ == "__main__":
    # Verificar versão do Python
    if sys.version_info < (3, 9):
        print(f"Excerpta requer Python 3.9+. Versão atual: {sys.version}")
        print("Baixe a versão mais recente em: https://python.org/downloads")
        input("Pressione Enter para sair.")
        sys.exit(1)

    # Linux: garante tkinter e pip no sistema (independente da distro),
    # instalando via apt/dnf/yum/zypper/pacman/apk conforme detectado.
    if IS_LINUX and not _garantir_tk_e_pip():
        sys.exit(1)

    # Tentar GUI, cair em console se tkinter não estiver disponível
    try:
        import tkinter  # noqa: F401
        main_gui()
    except ImportError:
        print("tkinter não disponível — usando modo console.")
        main_console()
