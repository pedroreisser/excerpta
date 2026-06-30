#!/usr/bin/env python3
"""Excerpta — launcher universal (Linux e Windows). Não requer administrador."""

import sys
import subprocess
import importlib
import os
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

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excerpta.py")
IS_WIN = sys.platform == "win32"


def _pip_flags():
    """Flags para pip que evitam precisar de permissão de administrador."""
    if IS_WIN:
        return ["--user"]
    else:
        # Ubuntu 23+/Debian 12+ exigem --break-system-packages para pip fora de venv
        return ["--break-system-packages"]


def checar_faltando(deps):
    faltando = []
    for modulo, pacote in deps:
        try:
            importlib.import_module(modulo)
        except ImportError:
            faltando.append((modulo, pacote))
    return faltando


def _instalar_pacotes(pacotes, callback_log, callback_fim):
    """Instala lista de pacotes e chama callbacks. Roda em thread separada."""
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
    frame_ocr.pack(fill="x", pady=(0, 16))
    ttk.Checkbutton(frame_ocr,
                    text="Instalar suporte a OCR (PDFs escaneados)  ",
                    variable=var_docling).pack(side="left")
    ttk.Label(frame_ocr, text="  ≈2 GB, 10-20 min",
              foreground="gray", font=("TkDefaultFont", 8)).pack(side="left")

    if not faltando and not tem_docling:
        ttk.Label(frame,
                  text="Excerpta já está instalado.\nDeseja adicionar o suporte a OCR?",
                  justify="center", font=("TkDefaultFont", 10)
                  ).pack(pady=(0, 12))

    def _iniciar():
        pacotes = list(faltando)
        if var_docling.get() and not tem_docling:
            pacotes += DEPS_DOCLING
        if not pacotes:
            root.destroy()
            _abrir_app()
            return
        _instalar_gui(pacotes, root, lambda: [root.destroy(), _abrir_app()])

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
        if not IS_WIN:
            print("\nSe persistir, instale python3-tk:")
            print("  sudo apt install python3-tk python3-pip")
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

    # Tentar GUI, cair em console se tkinter não estiver disponível
    try:
        import tkinter  # noqa: F401
        main_gui()
    except ImportError:
        print("tkinter não disponível — usando modo console.")
        print("Para interface gráfica, instale: sudo apt install python3-tk\n")
        main_console()
