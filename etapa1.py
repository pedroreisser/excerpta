"""Excerpta — Etapa 1: Extração de Títulos e Abstracts (módulo standalone)."""

# Importa tudo do módulo principal (funções de extração, constantes, UI helpers)
from excerpta import (
    ctk, filedialog, messagebox, threading, re, os, json, hashlib,
    INSTRUCAO_IA, TOK_OK, TOK_WARN, RECENTS_FILE,
    BG_WINDOW, BG_CARD, BG_PANEL,
    ACCENT, ACCENT_HOV, ACCENT_LT, ACCENT_BORD, ACCENT_HDR, ACCENT_TXT,
    GREEN, GREEN_HOV, GREEN_LT,
    GRAY_CARD, GRAY_BORD, GRAY_TEXT,
    TEXT_PRI, TEXT_SEC, DIVIDER,
    C_OK, C_WARN, C_ERR, C_WARN_SOFT,
    extrair_titulo_abstract_pdf, estimar_tokens, cor_tokens,
    abrir_no_sistema, _font, _sep, _header,
    _ler_recentes, _gravar_recente, _btn_recentes, _bind_dnd_entry,
    _e_suplementar_integral, MOTOR_OK, PYMUPDF4LLM_OK, DOCLING_OK,
    _TEMPO_DIGITAL_S, _TEMPO_OCR_S,
)
import difflib
import time


# ══════════════════════════════════════ etapa 1 ══════════════════════════════════

class Etapa1Frame(ctk.CTkFrame):
    def __init__(self, parent, app=None):
        super().__init__(parent, fg_color=BG_WINDOW)
        self._app               = app
        self._pasta             = ''
        self._conteudo          = ''
        self._checks            = {}
        self._pdf_sizes         = {}
        self._checkbox_widgets  = {}
        self._cancelando        = False
        self._hashes_texto      = {}   # hash → [nome, ...]
        self._titulos_extraidos = {}   # nome → titulo
        self._build()

    def _build(self):
        _header(self, bg=ACCENT_HDR, txt_color=ACCENT_TXT,
                titulo='Excerpta — Extração de Títulos e Abstracts')

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.pack(fill='both', expand=True, padx=20, pady=16)
        main.columnconfigure(0, weight=2)
        main.columnconfigure(1, weight=3)
        main.rowconfigure(0, weight=1)

        left  = ctk.CTkFrame(main, fg_color='transparent')
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        right = ctk.CTkFrame(main, fg_color='transparent')
        right.grid(row=0, column=1, sticky='nsew')

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, p):
        p.grid_rowconfigure(4, weight=1)
        p.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(p, text='Pasta dos PDFs', font=_font(13, 'bold'),
                     text_color=TEXT_SEC, anchor='w'
                     ).grid(row=0, column=0, sticky='ew', pady=(0, 4))

        row_p = ctk.CTkFrame(p, fg_color='transparent')
        row_p.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        self._entry_pasta = ctk.CTkEntry(
            row_p, placeholder_text='Cole, arraste ou selecione a pasta…',
            font=_font(14), height=34)
        self._entry_pasta.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self._entry_pasta.bind('<Return>', lambda _: self._carregar_pdfs(
            self._entry_pasta.get().strip()))
        ctk.CTkButton(row_p, text='Selecionar', width=110,
                     fg_color=ACCENT, hover_color=ACCENT_HOV, height=34,
                     font=_font(14), command=self._selecionar_pasta).pack(side='left')
        _btn_recentes(row_p, self._entry_pasta, 'pastas', self._carregar_pdfs)
        _bind_dnd_entry(self._entry_pasta, self._on_dnd_pasta)

        ctk.CTkFrame(p, fg_color=DIVIDER, height=1, corner_radius=0
                     ).grid(row=2, column=0, sticky='ew', pady=(0, 10))

        row_lbl = ctk.CTkFrame(p, fg_color='transparent')
        row_lbl.grid(row=3, column=0, sticky='ew', pady=(0, 4))
        ctk.CTkLabel(row_lbl, text='PDFs encontrados', font=_font(13, 'bold'),
                     text_color=TEXT_SEC, anchor='w').pack(side='left')
        self._lbl_contagem = ctk.CTkLabel(row_lbl, text='', font=_font(11),
                                           text_color=TEXT_SEC)
        self._lbl_contagem.pack(side='left', padx=(8, 0))
        self._btn_desmarcar = ctk.CTkButton(
            row_lbl, text='Desmarcar todos', width=112, height=24,
            fg_color='transparent', hover_color=ACCENT_LT,
            text_color=ACCENT, font=_font(11), border_width=0,
            state='disabled', command=lambda: self._marcar_todos(False))
        self._btn_desmarcar.pack(side='right')
        self._btn_marcar = ctk.CTkButton(
            row_lbl, text='Marcar todos', width=92, height=24,
            fg_color='transparent', hover_color=ACCENT_LT,
            text_color=ACCENT, font=_font(11), border_width=0,
            state='disabled', command=lambda: self._marcar_todos(True))
        self._btn_marcar.pack(side='right', padx=(0, 4))

        self._frame_lista = ctk.CTkScrollableFrame(p, fg_color=BG_CARD,
                                                    border_color=DIVIDER, border_width=1,
                                                    corner_radius=8)
        self._frame_lista.grid(row=4, column=0, sticky='nsew')
        self._lbl_vazio = ctk.CTkLabel(self._frame_lista,
                                        text='Nenhuma pasta selecionada.',
                                        text_color=TEXT_SEC, font=_font(13))
        self._lbl_vazio.pack(pady=24)

        self._frame_dist = ctk.CTkFrame(p, fg_color=BG_CARD,
                                         border_color=DIVIDER, border_width=1,
                                         corner_radius=8)
        # não gridado ainda — aparece após carregar pasta

        ctk.CTkFrame(p, fg_color=DIVIDER, height=1, corner_radius=0
                     ).grid(row=6, column=0, sticky='ew', pady=(10, 10))

        row_gen = ctk.CTkFrame(p, fg_color='transparent')
        row_gen.grid(row=7, column=0, sticky='ew')
        self._btn_gerar = ctk.CTkButton(
            row_gen, text='⚙  Extrair Abstracts', width=165,
            fg_color=ACCENT, hover_color=ACCENT_HOV, height=36,
            font=_font(14, 'bold'), state='disabled', command=self._gerar)
        self._btn_gerar.pack(side='left', padx=(0, 8))
        self._btn_check_dup1 = ctk.CTkButton(
            row_gen, text='Checar duplicatas', width=138,
            fg_color=BG_PANEL, hover_color=ACCENT_LT,
            text_color=TEXT_PRI, font=_font(13),
            border_width=1, border_color=GRAY_BORD,
            height=36, state='disabled', command=self._checar_duplicatas)
        self._btn_check_dup1.pack(side='left', padx=(0, 8))
        self._lbl_tokens = ctk.CTkLabel(row_gen, text='', font=_font(14, 'bold'))
        self._lbl_tokens.pack(side='left')

        self._prog = ctk.CTkProgressBar(p, mode='determinate', height=6,
                                         progress_color=ACCENT, fg_color=BG_WINDOW)
        self._prog.set(0)
        self._prog.grid(row=8, column=0, sticky='ew', pady=(6, 2))

        self._lbl_status = ctk.CTkLabel(p, text='', font=_font(13),
                                         text_color=TEXT_SEC, anchor='w')
        self._lbl_status.grid(row=9, column=0, sticky='ew')

    def _build_right(self, p):
        row_top = ctk.CTkFrame(p, fg_color='transparent')
        row_top.pack(fill='x', pady=(0, 2))
        ctk.CTkLabel(row_top, text='Prévia do arquivo gerado',
                     font=_font(13, 'bold'), text_color=TEXT_SEC,
                     anchor='w').pack(side='left')
        self._btn_salvar = ctk.CTkButton(
            row_top, text='↓  Salvar arquivo', width=148,
            fg_color=ACCENT, hover_color=ACCENT_HOV, height=34,
            font=_font(14, 'bold'), state='disabled', command=self._salvar)
        self._btn_salvar.pack(side='right')

        ctk.CTkLabel(p, text='Editável — ajuste o texto ou o prompt da IA antes de salvar',
                     font=_font(11), text_color=TEXT_SEC, anchor='w').pack(fill='x', pady=(0, 6))

        self._textbox = ctk.CTkTextbox(
            p, font=ctk.CTkFont(family='Ubuntu Mono', size=12),
            state='normal', wrap='none',
            fg_color=BG_CARD, border_color=DIVIDER, border_width=1,
            corner_radius=8, text_color=TEXT_PRI)
        self._textbox.pack(fill='both', expand=True)

    # ── lógica ──────────────────────────────────────────────────────────────────

    def _selecionar_pasta(self):
        pasta = filedialog.askdirectory(
            title='Selecionar pasta com PDFs',
            initialdir=self._pasta or os.path.expanduser('~'))
        if pasta:
            self._carregar_pdfs(pasta)

    def _on_dnd_pasta(self, event):
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        if not paths:
            return
        path = paths[0]
        pasta = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.isdir(pasta):
            self._carregar_pdfs(pasta)

    def _carregar_pdfs(self, pasta):
        if not pasta or not os.path.isdir(pasta):
            return
        self._pasta = pasta
        self._entry_pasta.delete(0, 'end')
        self._entry_pasta.insert(0, pasta)
        _gravar_recente('pastas', pasta)

        for w in self._frame_lista.winfo_children():
            w.destroy()
        self._checks.clear()
        self._pdf_sizes.clear()
        self._checkbox_widgets.clear()
        self._hashes_texto.clear()
        self._titulos_extraidos.clear()
        self._frame_dist.grid_forget()
        self._lbl_status.configure(text='Escaneando pasta…')
        self.update_idletasks()

        pdfs = sorted(f for f in os.listdir(pasta) if f.lower().endswith('.pdf'))
        if not pdfs:
            ctk.CTkLabel(self._frame_lista, text='Nenhum PDF encontrado nesta pasta.',
                        text_color=TEXT_SEC, font=_font(13)).pack(pady=24)
            for btn in (self._btn_gerar, self._btn_marcar,
                        self._btn_desmarcar, self._btn_check_dup1):
                btn.configure(state='disabled')
            return

        for nome in pdfs:
            caminho   = os.path.join(pasta, nome)
            tam_bytes = os.path.getsize(caminho)
            self._pdf_sizes[nome] = tam_bytes
            var = ctk.BooleanVar(value=True)
            self._checks[nome] = var

            nome_exib = nome if len(nome) <= 42 else nome[:39] + '…'
            tam_str   = f'  ({tam_bytes / 1_000_000:.1f} MB)'
            chk = ctk.CTkCheckBox(self._frame_lista,
                                   text=nome_exib + tam_str,
                                   variable=var,
                                   font=_font(13), checkmark_color='white',
                                   fg_color=ACCENT, hover_color=ACCENT_HOV,
                                   border_color=GRAY_BORD)
            chk.pack(anchor='w', pady=3, padx=8)
            self._checkbox_widgets[nome] = chk

        self._btn_gerar.configure(state='normal' if MOTOR_OK else 'disabled')
        self._btn_marcar.configure(state='normal')
        self._btn_desmarcar.configure(state='normal')
        self._btn_check_dup1.configure(state='disabled')  # só após extração
        self._atualizar_contagem()

        n = len(pdfs)
        if MOTOR_OK:
            msg = f'{n} arquivo(s) encontrado(s) — ajuste o filtro ou clique em Extrair Abstracts'
            if PYMUPDF4LLM_OK and not DOCLING_OK:
                msg += ' (sem OCR — instale docling para PDFs escaneados)'
            self._lbl_status.configure(text=msg)
        else:
            self._lbl_status.configure(
                text='⚠  Nenhum motor instalado. Execute: pip install pymupdf4llm')

        self._mostrar_distribuicao()

    def _mostrar_distribuicao(self):
        for w in self._frame_dist.winfo_children():
            w.destroy()

        if not self._pdf_sizes:
            return

        buckets = [
            ('< 1 MB',  0,    1,    '#2E7D4F'),
            ('1–3 MB',  1,    3,    '#2E7D4F'),
            ('3–7 MB',  3,    7,    '#B45309'),
            ('7–15 MB', 7,   15,    '#B91C1C'),
            ('> 15 MB', 15, 9999,   '#B91C1C'),
        ]
        sizes_mb = [s / 1_000_000 for s in self._pdf_sizes.values()]
        counts   = [(lbl, sum(1 for s in sizes_mb if lo <= s < hi), cor)
                    for lbl, lo, hi, cor in buckets]
        max_c    = max(c for _, c, _ in counts) or 1

        inner = ctk.CTkFrame(self._frame_dist, fg_color='transparent')
        inner.pack(fill='x', padx=10, pady=(8, 8))

        hdr = ctk.CTkFrame(inner, fg_color='transparent')
        hdr.pack(fill='x', pady=(0, 6))
        ctk.CTkLabel(hdr, text='Distribuição de tamanhos', font=_font(12, 'bold'),
                     text_color=TEXT_PRI, anchor='w').pack(side='left')
        ctk.CTkLabel(hdr, text=f'{len(sizes_mb)} arquivo(s)',
                     font=_font(11), text_color=TEXT_SEC).pack(side='right')

        for lbl, count, cor in counts:
            r = ctk.CTkFrame(inner, fg_color='transparent')
            r.pack(fill='x', pady=1)
            ctk.CTkLabel(r, text=lbl, font=_font(11), text_color=TEXT_SEC,
                        width=56, anchor='e').pack(side='left', padx=(0, 6))
            bar = ctk.CTkProgressBar(r, height=12, corner_radius=3,
                                     fg_color=DIVIDER, progress_color=cor)
            bar.set(count / max_c)
            bar.pack(side='left', fill='x', expand=True)
            ctk.CTkLabel(r, text=str(count), font=_font(11), text_color=TEXT_SEC,
                        width=22, anchor='w').pack(side='left', padx=(6, 0))

        n_grandes = sum(1 for s in sizes_mb if s >= 7)
        if n_grandes:
            ctk.CTkLabel(inner,
                         text=f'⚠  {n_grandes} arquivo(s) ≥ 7 MB — possivelmente livros ou protocolos',
                         font=_font(11), text_color='#B45309', anchor='w',
                         wraplength=270).pack(fill='x', pady=(6, 0))

        _sep(inner, pady=(8, 6))

        row_f = ctk.CTkFrame(inner, fg_color='transparent')
        row_f.pack(fill='x', pady=(0, 4))
        ctk.CTkLabel(row_f, text='Filtrar arquivos maiores que',
                     font=_font(12), text_color=TEXT_PRI).pack(side='left')
        self._entry_filtro_mb = ctk.CTkEntry(
            row_f, width=80, height=34, font=_font(14, 'bold'), justify='center')
        self._entry_filtro_mb.insert(0, '10')
        self._entry_filtro_mb.pack(side='left', padx=(8, 4))
        ctk.CTkLabel(row_f, text='MB', font=_font(13),
                     text_color=TEXT_SEC).pack(side='left')
        ctk.CTkButton(row_f, text='Filtrar', width=110, height=34,
                     fg_color=ACCENT, hover_color=ACCENT_HOV,
                     font=_font(14, 'bold'), command=self._aplicar_filtro
                     ).pack(side='right')

        row_pre = ctk.CTkFrame(inner, fg_color='transparent')
        row_pre.pack(fill='x', pady=(0, 2))
        ctk.CTkLabel(row_pre, text='Rápido:', font=_font(11),
                     text_color=TEXT_SEC, width=46).pack(side='left')
        for v in (3, 5, 10, 20):
            ctk.CTkButton(row_pre, text=f'{v} MB', width=52, height=22,
                         fg_color=BG_PANEL, hover_color=ACCENT_LT,
                         text_color=TEXT_PRI, font=_font(11),
                         border_width=1, border_color=GRAY_BORD,
                         command=lambda x=v: self._set_filtro_mb(x)
                         ).pack(side='left', padx=(0, 4))

        self._frame_dist.grid(row=5, column=0, sticky='ew', pady=(8, 0))

    def _set_filtro_mb(self, val):
        self._entry_filtro_mb.delete(0, 'end')
        self._entry_filtro_mb.insert(0, str(val))

    def _aplicar_filtro(self):
        try:
            mb = float(self._entry_filtro_mb.get().replace(',', '.'))
            if mb <= 0:
                raise ValueError
        except ValueError:
            self._lbl_status.configure(text='⚠  Informe um número válido em MB (ex: 10)')
            return
        n_alt = 0
        for nome, var in self._checks.items():
            if self._pdf_sizes.get(nome, 0) / 1_000_000 > mb:
                var.set(False)
                n_alt += 1
        self._atualizar_contagem()
        msg = (f'✓  {n_alt} arquivo(s) desmarcado(s) por excederem {mb:.0f} MB'
               if n_alt else f'Nenhum arquivo excede {mb:.0f} MB')
        self._lbl_status.configure(text=msg)

    def _marcar_todos(self, valor):
        for var in self._checks.values():
            var.set(valor)
        self._atualizar_contagem()

    def _checar_duplicatas(self):
        self._btn_check_dup1.configure(state='disabled')
        self._lbl_status.configure(text='Verificando duplicatas…')
        threading.Thread(target=self._checar_duplicatas_worker, daemon=True).start()

    def _checar_duplicatas_worker(self):
        # Nível 1: hash de conteúdo (calculado durante extração)
        grupos_hash = {k: v for k, v in self._hashes_texto.items() if len(v) > 1}
        extras_hash = {nome for grupo in grupos_hash.values() for nome in grupo[1:]}

        # Nível 2: fuzzy de título entre arquivos não marcados como exatos
        nomes_validos = [n for n in self._checks if n not in extras_hash]
        titulos = self._titulos_extraidos
        pares_fuzzy = []
        for i, n1 in enumerate(nomes_validos):
            t1 = titulos.get(n1, '').strip()
            if len(t1) < 10:
                continue
            for n2 in nomes_validos[i + 1:]:
                t2 = titulos.get(n2, '').strip()
                if len(t2) < 10:
                    continue
                ratio = difflib.SequenceMatcher(None, t1.lower(), t2.lower()).ratio()
                if ratio >= 0.95:
                    pares_fuzzy.append((n1, n2, ratio))

        self.after(0, lambda: self._aplicar_duplicatas(grupos_hash, extras_hash, pares_fuzzy))

    def _aplicar_duplicatas(self, grupos_hash, extras_hash, pares_fuzzy):
        for nome, chk in self._checkbox_widgets.items():
            if nome in extras_hash:
                chk.configure(text_color=C_WARN)
                self._checks[nome].set(False)
            else:
                chk.configure(text_color=TEXT_PRI)
        self._atualizar_contagem()

        msgs = []
        if grupos_hash:
            n_ext = len(extras_hash)
            msgs.append(f'{len(grupos_hash)} grupo(s) de texto idêntico — '
                        f'{n_ext} arquivo(s) desmarcado(s)')

        if pares_fuzzy:
            self._processar_pares_fuzzy(pares_fuzzy, msgs)
        else:
            status = ('✓  Nenhuma duplicata encontrada' if not msgs
                      else '⚠  ' + ' | '.join(msgs))
            self._lbl_status.configure(text=status)
            self._btn_check_dup1.configure(state='normal')

    def _processar_pares_fuzzy(self, pares, msgs):
        if not pares:
            status = ('✓  Nenhuma duplicata encontrada' if not msgs
                      else '⚠  ' + ' | '.join(msgs))
            self._lbl_status.configure(text=status)
            self._btn_check_dup1.configure(state='normal')
            return

        n1, n2, ratio = pares[0]
        restantes = pares[1:]
        t1 = self._titulos_extraidos.get(n1, n1)
        t2 = self._titulos_extraidos.get(n2, n2)

        win = ctk.CTkToplevel(self)
        win.title('Provável duplicata')
        win.resizable(False, False)
        win.withdraw()

        frame = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True)

        ctk.CTkLabel(frame, text=f'Provável duplicata  ·  similaridade {ratio:.0%}',
                     font=_font(13, 'bold'), text_color=C_WARN_SOFT
                     ).pack(padx=20, pady=(14, 8))

        for txt in (t1, t2):
            ctk.CTkLabel(frame, text=txt[:130] + ('…' if len(txt) > 130 else ''),
                         font=_font(12), text_color=TEXT_PRI,
                         wraplength=400, justify='left').pack(padx=20, pady=(0, 4))

        btn_row = ctk.CTkFrame(frame, fg_color='transparent')
        btn_row.pack(pady=(10, 16))

        escolha = [None]

        def _sim():
            escolha[0] = 'sim'; win.destroy()
        def _nao():
            escolha[0] = 'nao'; win.destroy()
        def _todos():
            escolha[0] = 'todos'; win.destroy()

        ctk.CTkButton(btn_row, text='Sim — são duplicatas', width=168, height=32,
                     fg_color=C_ERR, hover_color='#991818', text_color='white',
                     font=_font(12, 'bold'), command=_sim).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_row, text='Não — manter ambos', width=156, height=32,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(12),
                     border_width=1, border_color=GRAY_BORD,
                     command=_nao).pack(side='left', padx=(0, 8))
        if restantes:
            ctk.CTkButton(btn_row, text='Sim para todos', width=120, height=32,
                         fg_color=C_WARN, hover_color='#8B3A00',
                         text_color='white', font=_font(12, 'bold'),
                         command=_todos).pack(side='left')

        win.update_idletasks()
        win.geometry('460x200')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.grab_set()
        win.wait_window()

        if escolha[0] == 'todos':
            n_marcados = 0
            for nn1, nn2, _ in [(n1, n2, ratio)] + restantes:
                chk = self._checkbox_widgets.get(nn2)
                if chk:
                    chk.configure(text_color=C_WARN_SOFT)
                    n_marcados += 1
            msgs.append(f'{n_marcados} provável(is) duplicata(s) marcada(s) em amarelo')
            self._atualizar_contagem()
            self._lbl_status.configure(text='⚠  ' + ' | '.join(msgs))
            self._btn_check_dup1.configure(state='normal')
        elif escolha[0] == 'sim':
            chk2 = self._checkbox_widgets.get(n2)
            if chk2:
                chk2.configure(text_color=C_WARN_SOFT)
            self._processar_pares_fuzzy(restantes, msgs)
        else:
            self._processar_pares_fuzzy(restantes, msgs)

    def _atualizar_contagem(self):
        if not self._checks:
            self._lbl_contagem.configure(text='')
            return
        total = len(self._checks)
        sel   = sum(1 for v in self._checks.values() if v.get())
        if sel == total:
            texto, cor = f'({total} selecionados)', TEXT_SEC
        elif sel == 0:
            texto, cor = '(nenhum selecionado)', C_ERR
        else:
            texto, cor = f'({sel} de {total} selecionados)', C_WARN
        self._lbl_contagem.configure(text=texto, text_color=cor)

    def _gerar(self):
        selecionados = [os.path.join(self._pasta, n)
                       for n, v in self._checks.items() if v.get()]
        if not selecionados:
            messagebox.showwarning('Nenhum PDF', 'Selecione ao menos um PDF.')
            return
        self._cancelando = False
        self._btn_gerar.configure(
            text='✕  Cancelar', fg_color=C_ERR, hover_color='#991818',
            command=self._cancelar)
        self._btn_salvar.configure(state='disabled')
        self._btn_check_dup1.configure(state='disabled')
        self._lbl_tokens.configure(text='')
        self._prog.set(0)
        self._prog.configure(fg_color=DIVIDER)
        threading.Thread(target=self._gerar_worker, args=(selecionados,), daemon=True).start()

    def _cancelar(self):
        self._cancelando = True
        self._btn_gerar.configure(state='disabled')
        self._lbl_status.configure(text='Cancelando…')

    def _gerar_worker(self, caminhos):
        total          = len(caminhos)
        blocos         = [INSTRUCAO_IA]
        processados    = 0
        hashes_local   = {}
        titulos_local  = {}

        for i, caminho in enumerate(caminhos, 1):
            if self._cancelando:
                break
            nome = os.path.basename(caminho)
            self.after(0, lambda t=f'Processando {i}/{total}:  {nome}',
                                  pct=(i - 1) / total: (
                       self._lbl_status.configure(text=t),
                       self._prog.set(pct)))

            if _e_suplementar_integral(caminho):
                blocos.append(
                    f'\nARQUIVO: {nome}\n'
                    f'TITULO: [Material suplementar — ignorado]\n'
                    f'ABSTRACT: [Este arquivo foi identificado como material suplementar'
                    f' e não foi incluído]\n'
                    '---'
                )
                processados += 1
                self.after(0, lambda pct=processados / total: self._prog.set(pct))
                continue

            try:
                titulo, abstract = extrair_titulo_abstract_pdf(caminho)
                digest = hashlib.md5(
                    (titulo + '\n' + abstract).encode('utf-8')).hexdigest()
            except Exception as exc:
                titulo, abstract = '', f'[ERRO: {exc}]'
                digest = f'__erro_{nome}'
            hashes_local.setdefault(digest, []).append(nome)
            titulos_local[nome] = titulo
            blocos.append(
                f'\nARQUIVO: {nome}\n'
                f'TITULO: {titulo or "(não detectado)"}\n'
                f'ABSTRACT: {abstract}\n'
                '---'
            )
            processados += 1
            self.after(0, lambda pct=processados / total: self._prog.set(pct))

        conteudo  = '\n'.join(blocos)
        tokens    = estimar_tokens(conteudo)
        cor       = cor_tokens(tokens)
        tok_txt   = f'~{tokens:,} tokens'
        cancelado = self._cancelando

        def _done():
            self._prog.configure(fg_color=BG_WINDOW)
            self._btn_gerar.configure(
                text='⚙  Extrair Abstracts', fg_color=ACCENT, hover_color=ACCENT_HOV,
                command=self._gerar, state='normal')
            if cancelado:
                self._lbl_status.configure(
                    text=f'Cancelado — {processados} de {total} arquivo(s) processado(s)')
            else:
                self._lbl_status.configure(text=f'✓  {processados} artigo(s) processado(s)')
            if processados > 0:
                self._hashes_texto      = hashes_local
                self._titulos_extraidos = titulos_local
                self._conteudo = conteudo
                self._lbl_tokens.configure(text=tok_txt, text_color=cor)
                self._textbox.delete('1.0', 'end')
                self._textbox.insert('1.0', conteudo)
                self._textbox.see('1.0')
                self._btn_salvar.configure(state='normal')
                self._btn_check_dup1.configure(state='normal')

        self.after(0, _done)

    def _salvar(self):
        conteudo = self._textbox.get('1.0', 'end-1c').strip()
        if not conteudo:
            return
        path = filedialog.asksaveasfilename(
            title='Salvar lista de abstracts',
            defaultextension='.txt',
            initialfile='lista_abstracts.txt',
            initialdir=self._pasta or os.path.expanduser('~'),
            filetypes=[('Arquivo de texto', '*.txt'), ('Todos', '*.*')]
        )
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as f:
            f.write(conteudo)
        if messagebox.askyesno('Arquivo salvo',
                               f'Salvo em:\n{path}\n\nAbrir a pasta?'):
            abrir_no_sistema(os.path.dirname(path))


# ══════════════════════════════════════ app ══════════════════════════════════════

class Etapa1App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Excerpta — Etapa 1')
        self.geometry('1100x740')
        self.minsize(960, 640)
        self.configure(fg_color=BG_WINDOW)

        frame = Etapa1Frame(self)
        frame.pack(fill='both', expand=True)


if __name__ == '__main__':
    app = Etapa1App()
    app.mainloop()
