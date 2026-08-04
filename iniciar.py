#!/usr/bin/env python3
"""Excerpta — launcher universal (Linux e Windows). Não requer administrador."""

import sys
import subprocess
import importlib.util
import os
import shutil
import threading

# ── Dependências obrigatórias ────────────────────────────────────────────────
# pymupdf4llm: extração de PDFs digitais (rápido, leve)
# customtkinter / tkinterdnd2: interface gráfica
DEPS_OBRIGATORIAS = [
    ("pymupdf4llm",   "pymupdf4llm"),
    ("customtkinter", "customtkinter"),
    ("tkinterdnd2",   "tkinterdnd2"),
]
# OCR (docling) e IA local (Ollama) são opcionais e ficam disponíveis para
# instalar dentro do próprio Excerpta, em Configurações — não bloqueiam a
# abertura do programa nem aparecem aqui no launcher.

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "codigo", "excerpta.py")
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


def _instalar_pacotes(pacotes, callback_log, callback_fim):
    """Instala a lista de pacotes obrigatórios. Roda em thread separada."""
    erros = []
    flags = _pip_flags()
    for _mod, pacote in pacotes:
        callback_log(f"> pip install {pacote}")
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", pacote] + flags,
            capture_output=True, text=True
        )
        if res.returncode == 0:
            callback_log(f"  ✓ {pacote} instalado com sucesso")
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

    callback_fim(erros)


# ── Interface gráfica ─────────────────────────────────────────────────────────

def _abrir_app():
    subprocess.Popen([sys.executable, SCRIPT])


def _instalar_gui(pacotes, root, depois_de_instalar):
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
            cmd = (f"pip install {' '.join(erros)}" +
                   ("" if IS_WIN else " --break-system-packages"))
            root.after(0, lambda: messagebox.showwarning(
                "Atenção",
                f"Não foi possível instalar: {', '.join(erros)}\n\n"
                f"Tente manualmente no terminal:\n  {cmd}\n\n"
                f"{'Obs: no Windows, abra o terminal como usuário normal (sem admin).' if IS_WIN else ''}"
            ))
        else:
            _log("\n✅ Tudo instalado! Abrindo o Excerpta…")
            root.after(800, lambda: [dlg.destroy(), depois_de_instalar()])

    threading.Thread(target=_instalar_pacotes,
                     args=(pacotes, _log, _fim),
                     daemon=True).start()
    dlg.wait_window()


def main_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.withdraw()

    faltando = checar_faltando(DEPS_OBRIGATORIAS)

    if not faltando:
        # Tudo instalado — abrir direto, sem mostrar nenhuma janela
        root.destroy()
        _abrir_app()
        return

    # ── Janela de instalação (só aparece quando falta algo obrigatório) ────────
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

    ttk.Label(frame,
              text="Dependências a instalar (nenhuma requer administrador):",
              font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
    for _, p in faltando:
        ttk.Label(frame, text=f"  • {p}", foreground="#333").pack(anchor="w")
    ttk.Label(frame, text="").pack()
    ttk.Label(frame,
              text="OCR para PDFs escaneados e IA local (Ollama) são opcionais\n"
                   "e podem ser instalados depois, em Configurações, dentro do Excerpta.",
              foreground="gray", justify="left").pack(anchor="w", pady=(0, 12))

    def _iniciar():
        _instalar_gui(faltando, root, lambda: [root.destroy(), _abrir_app()])

    frame_btn = ttk.Frame(frame)
    frame_btn.pack()
    ttk.Button(frame_btn, text="Instalar e abrir",
               command=_iniciar).pack(side="left", padx=6)
    ttk.Button(frame_btn, text="Cancelar",
               command=root.destroy).pack(side="left", padx=6)

    root.mainloop()


def main_console():
    """Fallback sem tkinter (Linux sem python3-tk instalado)."""
    print("=" * 52)
    print("  Excerpta — Configuração inicial")
    print("=" * 52)
    print()

    faltando = checar_faltando(DEPS_OBRIGATORIAS)

    if not faltando:
        print("Tudo instalado. Abrindo Excerpta...")
        subprocess.Popen([sys.executable, SCRIPT])
        return

    print("Dependências a instalar:")
    for _, p in faltando:
        print(f"  • {p}")
    print()
    print("OCR (docling) e IA local (Ollama) são opcionais e podem ser instalados")
    print("depois, em Configurações, dentro do Excerpta.")
    print()
    resp = input("Instalar agora? [S/n]: ").strip().lower()
    if resp in ('n', 'no', 'nao', 'não'):
        print("Cancelado.")
        return

    print()

    def _log(txt):
        print(txt)

    erros = []

    def _fim(e):
        erros.extend(e)

    t = threading.Thread(target=_instalar_pacotes, args=(faltando, _log, _fim))
    t.start()
    t.join()

    if erros:
        print(f"\n⚠  Falha: {', '.join(erros)}")
        print("Tente manualmente:")
        for e in erros:
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
        if IS_WIN:
            # No Windows o tkinter vem embutido no instalador oficial do
            # Python (marcado por padrão em "tcl/tk and IDLE"); se faltar,
            # não dá pra instalar via pip — precisa reinstalar o Python.
            # Cair pro modo console não adianta: customtkinter (usado pelo
            # codigo/gui.py) também depende do tkinter e falharia do mesmo jeito.
            print("tkinter não está disponível nesta instalação do Python.")
            print()
            print("Reinstale o Python marcando a opção \"tcl/tk and IDLE\":")
            print("  https://python.org/downloads")
            input("Pressione Enter para sair.")
            sys.exit(1)
        print("tkinter não disponível — usando modo console.")
        main_console()
