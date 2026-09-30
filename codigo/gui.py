import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import os
import sys
import re
import subprocess
import shutil
import time
import webbrowser

import extracao
import ollama_bridge
import zotero_bridge
from config import (
    OLLAMA_DOWNLOAD_URL, OLLAMA_INSTALL_CMD, OLLAMA_MODELO_PADRAO,
    OLLAMA_MODELOS_RECOMENDADOS, OLLAMA_TIMEOUT_S, OLLAMA_URL_PADRAO,
    _get_setting, _gravar_recente, _ler_settings,
    _pip_flags, _salvar_settings,
)
from duplicatas import _detectar_grupos_possiveis_duplicatas, _hash_arquivo
from motor_pdf import (
    DOCLING_OK, MOTOR_OK, _TEMPO_DIGITAL_S, _TEMPO_OCR_S,
    _motor_para_tipo, detectar_tipo_pdf,
)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


# ══════════════════════════════════════ constantes ══════════════════════════════


def _nome_arquivo_seguro(nome):
    """Remove caracteres que o SO interpretaria como separador de pasta.

    Usado tanto no nome digitado pelo usuário quanto no nome herdado da
    pasta de destino — este último pode conter '/' se vier, por exemplo, do
    nome de uma coleção aninhada do Zotero ("Pai/Filha", como a API local
    devolve). Sem tratamento, a barra vira um os.path.join com subpasta
    inexistente e o Excerpta falha com "[Errno 2] No such file or directory".
    """
    limpo = re.sub(r'[\\/:*?"<>|]+', ' - ', nome or '').strip()
    return limpo or 'extracao_seletiva'

# ── Tipografia ────────────────────────────────────────────────────────────────
# Pilha por plataforma: vence a primeira família instalada. Sem isso o Tk cai
# calado numa fonte default sofrível quando a família não existe — a antiga
# "Ubuntu Sans" fixa deixava o app com cara de legado em qualquer Windows.
_PILHA_SO = {
    'win32':  ('Segoe UI Variable Text', 'Segoe UI', 'Tahoma'),
    'darwin': ('SF Pro Text', 'Helvetica Neue', 'Lucida Grande'),
}
_PILHA_FIM = ('Ubuntu Sans', 'Inter', 'Cantarell', 'Noto Sans', 'DejaVu Sans')

F = 'TkDefaultFont'


def resolver_fonte():
    """Fixa F na primeira família instalada. Exige um root Tk já criado."""
    global F
    try:
        from tkinter import font as tkfont
        instaladas = {nome.lower() for nome in tkfont.families()}
    except Exception:
        return
    for familia in _PILHA_SO.get(sys.platform, ()) + _PILHA_FIM:
        if familia.lower() in instaladas:
            F = familia
            return


# ── Escala de espaçamento e forma ─────────────────────────────────────────────
# Múltiplos de 4. Padding fora desta escala destoa a olho nu, e era o que
# acontecia com os valores soltos (9, 14, 28…) espalhados pelo arquivo.
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 20, 24

RAIO_CARD = 10          # cartões e a lista
RAIO_CTRL = 8           # botões, campos
RAIO_CHIP = 15          # chips de seção (pílula)
ALT_LINHA = 34          # altura de uma linha da lista de artigos

# ── Paleta Zotero-inspirada (tema claro) ──────────────────────────────────────

BG_WINDOW   = "#F3F4F6"
BG_CARD     = "#FFFFFF"
BG_PANEL    = "#ECEEF1"
BG_ZEBRA    = "#F7F8FA"   # linha ímpar da lista, alterna com BG_CARD

ACCENT       = "#3D6CAE"
ACCENT_HOV   = "#2D5599"
ACCENT_LT    = "#EEF3FA"
ACCENT_LT_HOV = "#E2EBF7"
ACCENT_BORD  = "#B6CCE8"
ACCENT_TXT   = "#2B4F8C"

GREEN       = "#2E7D4F"
GREEN_HOV   = "#1E6040"
GREEN_BORD  = "#A4D4B4"
GREEN_HDR   = "#EAF5EF"
GREEN_TXT   = "#1A5C36"

GRAY_BORD   = "#D4D6DA"
GRAY_TEXT   = "#666C75"

TEXT_PRI    = "#1F2328"
TEXT_SEC    = "#6B7280"
TEXT_OFF    = "#9CA3AF"   # desabilitado: apagado, mas ainda legível
DIVIDER     = "#E2E4E8"

C_OK        = "#2E7D4F"
C_WARN       = "#B45309"
C_WARN_HOV   = "#8B3A00"
C_WARN_LT    = "#FFF7ED"
C_WARN_LT_HOV = "#FCEBD8"
C_WARN_BORD  = "#F0C48A"
C_ERR        = "#B91C1C"
C_ERR_HOV    = "#8F1616"
C_ERR_LT     = "#FEF2F2"
C_ERR_LT_HOV = "#FCE3E3"
C_ERR_BORD   = "#F3B7B7"

_COR_PYMUPDF = ACCENT
_COR_DOCLING = "#7C3AED"
_COR_DOCLING_LT      = "#F5F0FE"
_COR_DOCLING_LT_HOV  = "#EBE1FC"
_COR_DOCLING_BORD    = "#D4C2F5"


# ══════════════════════════════════════ helpers ══════════════════════════════════



def abrir_no_sistema(caminho):
    try:
        if os.name == 'nt':
            os.startfile(caminho)
        else:
            subprocess.Popen(['xdg-open', caminho])
    except Exception:
        pass


def _font(size, weight='normal'):
    return ctk.CTkFont(family=F, size=size, weight=weight)


def _sep(parent, pady=(0, 0)):
    f = ctk.CTkFrame(parent, fg_color=DIVIDER, height=1, corner_radius=0)
    f.pack(fill='x', pady=pady)
    return f


# ── Colunas da lista de artigos ───────────────────────────────────────────────
# Uma definição só, usada pelo cabeçalho e por cada linha — é o que mantém as
# colunas alinhadas. Antes cada linha empilhava labels com pack(side='left'),
# então a posição de cada campo variava conforme o tamanho do nome do arquivo.
COL_LARGURAS = (26, None, 180, 120)      # None = coluna elástica
COL_PADX     = ((S3, 0), (0, S3), (0, 0), (0, S3))   # idêntico nas duas pontas


def _encurtar(nome, limite=64):
    """Encurta pelo meio: o fim do nome carrega ano/autor e não pode sumir."""
    if len(nome) <= limite:
        return nome
    corte = (limite - 1) // 2
    return f'{nome[:corte]}…{nome[-(limite - 1 - corte):]}'


def _montar_colunas(frame):
    for i, largura in enumerate(COL_LARGURAS):
        if largura is None:
            frame.grid_columnconfigure(i, weight=1, minsize=140)
        else:
            frame.grid_columnconfigure(i, weight=0, minsize=largura)
    frame.grid_rowconfigure(0, weight=1)


def _bind_dnd_lista(scrollable_frame, callback):
    """Registra drag-and-drop na lista de artigos (CTkScrollableFrame).

    Aceita texto além de arquivos: arrastar um item pai do Zotero não traz
    PDF nenhum, só os metadados formatados pelo Quick Copy.
    """
    try:
        from tkinterdnd2 import DND_FILES, DND_TEXT
        for w in (scrollable_frame._parent_canvas, scrollable_frame):
            # Só estes dois: com '*' o tkdnd pode negociar o "zotero/item"
            # que o Zotero anuncia primeiro, e que só traz IDs internos
            # inúteis fora dele. O text/plain é o que carrega os metadados.
            w.drop_target_register(DND_FILES, DND_TEXT)
            w.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


def _bind_dnd_rotulo(rotulo, callback):
    """Registra drop num CTkLabel — ele cobre a área da lista quando vazia."""
    try:
        from tkinterdnd2 import DND_FILES, DND_TEXT
        rotulo.drop_target_register(DND_FILES, DND_TEXT)
        rotulo.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


def _bind_dnd_widget(widget, callback):
    """Registra drag-and-drop num CTkFrame individual (via _canvas interno)."""
    try:
        from tkinterdnd2 import DND_FILES, DND_TEXT
        widget._canvas.drop_target_register(DND_FILES, DND_TEXT)
        widget._canvas.dnd_bind('<<Drop>>', callback)
    except Exception:
        pass


def _bind_dnd_recursivo(widget, callback, pular=()):
    """Registra o mesmo drop em `widget` e em todos os seus descendentes.

    O tkdnd só entrega o evento pro widget exato sob o cursor no momento do
    drop — não propaga pros pais como um bind normal do Tk. Sem isso, soltar
    um PDF em qualquer área que não fosse a lista de artigos ou o campo de
    pasta simplesmente não fazia nada. `pular` marca widgets com drop
    específico próprio (ex.: o rótulo da pasta, que interpreta a soltura
    como "usar esta pasta" em vez de "adicionar este PDF").
    """
    try:
        from tkinterdnd2 import DND_FILES, DND_TEXT
    except ImportError:
        return
    if widget not in pular:
        alvo = getattr(widget, '_canvas', widget)
        try:
            alvo.drop_target_register(DND_FILES, DND_TEXT)
            alvo.dnd_bind('<<Drop>>', callback)
        except Exception:
            pass
    for filho in widget.winfo_children():
        _bind_dnd_recursivo(filho, callback, pular)


# ══════════════════════════════════════ cabeçalho compartilhado ═══════════════════

# ══════════════════════════════════════ configurações ════════════════════════════

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('Configurações')
        self.withdraw()
        self.protocol('WM_DELETE_WINDOW', self._fechar)

        # Labels de texto corrido (wraplength) registrados aqui se reajustam
        # ao redimensionar a janela, em vez de ficar cortados ou com uma
        # faixa de espaço em branco fixa quando a janela cresce.
        self._wrap_labels = []

        cfg = _ler_settings()

        frame = ctk.CTkFrame(self, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True)

        ctk.CTkLabel(frame, text='Configurações', font=_font(15, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 4), anchor='w')
        _sep(frame, pady=(0, 10))

        tabs = ctk.CTkTabview(
            frame, fg_color=BG_CARD,
            segmented_button_fg_color=BG_PANEL,
            segmented_button_selected_color=GREEN,
            segmented_button_selected_hover_color=GREEN_HOV,
            text_color=TEXT_PRI)
        tabs.pack(fill='both', expand=True, padx=20, pady=(0, 12))
        aba_geral = tabs.add('Geral')
        aba_ia    = tabs.add('IA local (Ollama)')

        self._build_aba_geral(aba_geral, cfg)
        self._build_aba_ia(aba_ia, cfg)

        _sep(frame, pady=(0, 12))

        ctk.CTkButton(frame, text='Fechar', width=100, height=32,
                     fg_color=GREEN, hover_color=GREEN_HOV,
                     text_color='white', font=_font(13, 'bold'),
                     command=self._fechar).pack(pady=(0, 16))

        self.update_idletasks()
        self.minsize(480, 460)
        self.geometry('560x560')
        self.bind('<Configure>', self._on_resize)
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(50, self.grab_set)

    def _texto_corrido(self, parent, text, margin=76, **kw):
        """CTkLabel de texto corrido que reajusta o wraplength com a janela."""
        largura = max(200, self.winfo_width() - margin)
        lbl = ctk.CTkLabel(parent, text=text, font=_font(11), text_color=TEXT_SEC,
                           wraplength=largura, justify='left', anchor='w', **kw)
        self._wrap_labels.append((lbl, margin))
        return lbl

    def _on_resize(self, _evt=None):
        largura_janela = self.winfo_width()
        for lbl, margin in self._wrap_labels:
            lbl.configure(wraplength=max(200, largura_janela - margin))

    # ── aba Geral ───────────────────────────────────────────────────────────

    def _build_aba_geral(self, tab, cfg):
        inner = ctk.CTkScrollableFrame(tab, fg_color='transparent')
        inner.pack(fill='both', expand=True)

        # ── Motores de extração ───────────────────────────────────────────────
        row_ocr_hdr = ctk.CTkFrame(inner, fg_color='transparent')
        row_ocr_hdr.pack(fill='x', pady=(6, 6))
        ctk.CTkLabel(row_ocr_hdr, text='Motores de extração',
                     font=_font(12, 'bold'), text_color=TEXT_SEC,
                     anchor='w').pack(side='left')

        row_ocr = ctk.CTkFrame(inner, fg_color='transparent')
        row_ocr.pack(fill='x', pady=(0, 4))
        self._var_ocr = ctk.BooleanVar(value=cfg.get('usar_ocr', True))
        ctk.CTkCheckBox(row_ocr,
                        text='Processar PDFs escaneados com OCR',
                        variable=self._var_ocr,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(side='left')
        ctk.CTkButton(row_ocr, text='?', width=26, height=26,
                      fg_color=BG_PANEL, hover_color=ACCENT_LT,
                      text_color=ACCENT, font=_font(12, 'bold'),
                      border_width=1, border_color=ACCENT_BORD,
                      command=self._ajuda_ocr
                      ).pack(side='left', padx=(8, 0))

        self._texto_corrido(
            inner, 'PDF escaneado = imagem sem texto; requer docling.'
            ).pack(pady=(0, 8), anchor='w', fill='x')

        if not DOCLING_OK:
            row_docling = ctk.CTkFrame(inner, fg_color='transparent')
            row_docling.pack(fill='x', pady=(0, 4))
            ctk.CTkLabel(row_docling, text='✗ docling não instalado',
                         font=_font(12), text_color=C_ERR).pack(side='left')
            ctk.CTkButton(row_docling, text='Instalar suporte a OCR',
                         width=170, height=28,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=self._confirmar_instalar_docling
                         ).pack(side='left', padx=(10, 0))
            ctk.CTkLabel(inner, text='≈2 GB, 10-20 min. Baixado uma única vez.',
                         font=_font(11), text_color=TEXT_SEC
                         ).pack(pady=(4, 12), anchor='w')
        else:
            ctk.CTkLabel(inner, text='✓ docling instalado',
                         font=_font(12), text_color=C_OK
                         ).pack(pady=(0, 12), anchor='w')

        _sep(inner, pady=(0, 10))

        # ── Ao concluir ───────────────────────────────────────────────────────
        ctk.CTkLabel(inner, text='Ao concluir a extração',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(6, 6), anchor='w')

        self._var_resumo = ctk.BooleanVar(value=cfg.get('mostrar_resumo', True))
        ctk.CTkCheckBox(inner,
                        text='Mostrar resumo de resultados ao terminar',
                        variable=self._var_resumo,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 4))

        self._var_pasta = ctk.BooleanVar(value=cfg.get('perguntar_abrir_pasta', True))
        ctk.CTkCheckBox(inner,
                        text='Perguntar se deseja abrir a pasta ao terminar',
                        variable=self._var_pasta,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 4))

        self._var_log = ctk.BooleanVar(value=cfg.get('log_diagnostico', True))
        ctk.CTkCheckBox(inner,
                        text='Gravar log técnico de diagnóstico',
                        variable=self._var_log,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 2))
        self._texto_corrido(
            inner, 'Gera excerpta_debug.log — envie-o se der problema.'
            ).pack(pady=(0, 8), anchor='w', fill='x')

    # ── aba IA local ────────────────────────────────────────────────────────

    def _build_aba_ia(self, tab, cfg):
        inner = ctk.CTkScrollableFrame(tab, fg_color='transparent')
        inner.pack(fill='both', expand=True)

        # ── 1. Status do Ollama (instalação vem primeiro: sem ela, o resto
        # da aba não serve pra nada) ────────────────────────────────────────
        ollama_instalado = bool(shutil.which('ollama'))
        row_status_ollama = ctk.CTkFrame(inner, fg_color='transparent')
        row_status_ollama.pack(fill='x', pady=(0, 4))
        if ollama_instalado:
            ctk.CTkLabel(row_status_ollama, text='✓ Ollama instalado',
                         font=_font(12, 'bold'), text_color=C_OK
                         ).pack(side='left')
        else:
            ctk.CTkLabel(row_status_ollama, text='✗ Ollama não instalado',
                         font=_font(12, 'bold'), text_color=C_ERR
                         ).pack(side='left')
        self._lbl_status_ollama = ctk.CTkLabel(row_status_ollama, text='',
                                                font=_font(12), text_color=TEXT_SEC)
        self._lbl_status_ollama.pack(side='left', padx=(10, 0))

        if not ollama_instalado:
            if sys.platform.startswith('linux'):
                self._texto_corrido(
                    inner,
                    'Roda o instalador oficial (ollama.com/install.sh) com log '
                    'ao vivo, direto por aqui. Pode pedir senha de '
                    'administrador no processo.'
                    ).pack(pady=(4, 8), anchor='w', fill='x')
                ctk.CTkButton(inner, text='Instalar Ollama', width=150, height=30,
                             fg_color=GREEN, hover_color=GREEN_HOV,
                             text_color='white', font=_font(12, 'bold'),
                             command=self._confirmar_instalar_ollama
                             ).pack(anchor='w', pady=(0, 4))
            else:
                self._texto_corrido(
                    inner, 'Baixe o instalador oficial pro seu sistema em ollama.com.'
                    ).pack(pady=(4, 8), anchor='w', fill='x')
                ctk.CTkButton(inner, text='Abrir página de download', width=180, height=30,
                             fg_color=GREEN, hover_color=GREEN_HOV,
                             text_color='white', font=_font(12, 'bold'),
                             command=lambda: webbrowser.open(OLLAMA_DOWNLOAD_URL)
                             ).pack(anchor='w', pady=(0, 4))
            self._texto_corrido(
                inner, 'O resto da aba já fica configurado — só ative depois de instalar.'
                ).pack(pady=(6, 4), anchor='w', fill='x')

        _sep(inner, pady=(10, 10))

        # ── 2. Ativar ────────────────────────────────────────────────────
        self._var_ia_local = ctk.BooleanVar(value=cfg.get('usar_ia_local', False))
        ctk.CTkCheckBox(inner,
                        text='Usar IA local (Ollama) quando seções não são encontradas',
                        variable=self._var_ia_local,
                        command=self._on_ia_local_toggle,
                        font=_font(13), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD
                        ).pack(anchor='w', pady=(0, 4))
        self._texto_corrido(
            inner, 'Desligado por padrão; se o Ollama não responder, usa o fallback normal.'
            ).pack(pady=(0, 12), anchor='w', fill='x')

        # ── 3. Configuração do modelo ───────────────────────────────────────
        ctk.CTkLabel(inner, text='Configuração',
                     font=_font(12, 'bold'), text_color=TEXT_SEC
                     ).pack(pady=(0, 8), anchor='w')

        self._row_ollama_fields = ctk.CTkFrame(inner, fg_color='transparent')
        self._row_ollama_fields.pack(fill='x', pady=(0, 12))

        row_url = ctk.CTkFrame(self._row_ollama_fields, fg_color='transparent')
        row_url.pack(fill='x', pady=(0, 6))
        ctk.CTkLabel(row_url, text='Servidor:', font=_font(12),
                     text_color=TEXT_PRI, width=80, anchor='w').pack(side='left')
        self._entry_ollama_url = ctk.CTkEntry(row_url, width=190, height=30,
                                               font=_font(12))
        self._entry_ollama_url.insert(0, cfg.get('ollama_url', OLLAMA_URL_PADRAO))
        self._entry_ollama_url.pack(side='left')
        ctk.CTkLabel(row_url, text='Timeout:', font=_font(12),
                     text_color=TEXT_PRI).pack(side='left', padx=(14, 6))
        self._entry_ollama_timeout = ctk.CTkEntry(row_url, width=50, height=30,
                                                   font=_font(12))
        self._entry_ollama_timeout.insert(
            0, str(cfg.get('ollama_timeout_s', OLLAMA_TIMEOUT_S)))
        self._entry_ollama_timeout.pack(side='left')
        ctk.CTkLabel(row_url, text='s/artigo',
                     font=_font(11), text_color=TEXT_SEC
                     ).pack(side='left', padx=(4, 0))

        row_modelo = ctk.CTkFrame(self._row_ollama_fields, fg_color='transparent')
        row_modelo.pack(fill='x', pady=(0, 6))
        ctk.CTkLabel(row_modelo, text='Modelo:', font=_font(12),
                     text_color=TEXT_PRI, width=80, anchor='w').pack(side='left')
        # Combobox editável: lista os modelos já instalados (via /api/tags),
        # mas aceita digitar qualquer nome do catálogo do Ollama pra baixar.
        self._entry_ollama_modelo = ctk.CTkComboBox(row_modelo, width=220, height=30,
                                                     font=_font(12), values=[])
        self._entry_ollama_modelo.set(cfg.get('ollama_modelo', OLLAMA_MODELO_PADRAO))
        self._entry_ollama_modelo.pack(side='left')
        self._btn_atualizar_modelos = ctk.CTkButton(
            row_modelo, text='↻', width=30, height=30,
            fg_color=BG_PANEL, hover_color=GRAY_BORD,
            text_color=TEXT_PRI, font=_font(13),
            border_width=1, border_color=GRAY_BORD,
            command=self._atualizar_modelos_ollama)
        self._btn_atualizar_modelos.pack(side='left', padx=(6, 0))

        self._texto_corrido(
            inner, 'PC sem GPU: ~2 min/artigo num modelo 7B — timeout curto demais ignora a IA.'
            ).pack(pady=(2, 12), anchor='w', fill='x')

        # ── 4. Modelo: status e ações ────────────────────────────────────
        card_modelo = ctk.CTkFrame(inner, fg_color=BG_PANEL, corner_radius=RAIO_CARD)
        card_modelo.pack(fill='x', pady=(0, 12))
        card_modelo_in = ctk.CTkFrame(card_modelo, fg_color='transparent')
        card_modelo_in.pack(fill='x', padx=14, pady=12)

        ctk.CTkLabel(card_modelo_in, text='Modelo local',
                     font=_font(12, 'bold'), text_color=TEXT_PRI
                     ).pack(anchor='w', pady=(0, 8))

        # Pré-carregar e baixar lado a lado: são as duas ações desse card,
        # os próprios rótulos já dizem o que fazem — a legenda separada de
        # antes só repetia isso com mais palavras.
        row_acoes = ctk.CTkFrame(card_modelo_in, fg_color='transparent')
        row_acoes.pack(fill='x')
        self._btn_carregar_modelo = ctk.CTkButton(
            row_acoes, text='Pré-carregar', width=120, height=28,
            fg_color=BG_CARD, hover_color=GRAY_BORD,
            text_color=TEXT_PRI, font=_font(12),
            border_width=1, border_color=GRAY_BORD,
            command=self._carregar_modelo_ollama)
        self._btn_carregar_modelo.pack(side='left')
        self._btn_pull = ctk.CTkButton(row_acoes, text='Baixar modelo', width=130,
                                        height=28, fg_color=GREEN,
                                        hover_color=GREEN_HOV, text_color='white',
                                        font=_font(12, 'bold'),
                                        command=self._baixar_modelo_ollama)
        self._btn_pull.pack(side='left', padx=(8, 0))
        row_pull_status = ctk.CTkFrame(card_modelo_in, fg_color='transparent')
        row_pull_status.pack(fill='x')
        self._lbl_pull = ctk.CTkLabel(row_pull_status, text='', font=_font(11),
                                       text_color=TEXT_SEC)
        self._lbl_pull.pack(side='left', pady=(4, 0))
        self._pb_pull = ctk.CTkProgressBar(card_modelo_in, height=8, progress_color=GREEN)
        self._pb_pull.set(0)
        # barra só aparece durante um download (pack no _baixar_modelo_ollama)
        self._pull_cancelado = False
        self._pull_ativo = False

        self._on_ia_local_toggle()
        self._atualizar_modelos_ollama()
        if ollama_instalado:
            self._verificar_ollama()

    def _on_ia_local_toggle(self):
        estado = 'normal' if self._var_ia_local.get() else 'disabled'
        self._entry_ollama_url.configure(state=estado)
        self._entry_ollama_modelo.configure(state=estado)
        self._entry_ollama_timeout.configure(state=estado)
        self._btn_atualizar_modelos.configure(state=estado)
        self._btn_carregar_modelo.configure(state=estado)
        self._btn_pull.configure(state=estado)

    def _url_ollama(self):
        return self._entry_ollama_url.get().strip() or OLLAMA_URL_PADRAO

    def _modelo_ollama(self):
        return self._entry_ollama_modelo.get().strip() or OLLAMA_MODELO_PADRAO

    def _timeout_ollama(self):
        try:
            return max(5, int(float(self._entry_ollama_timeout.get().strip())))
        except ValueError:
            return OLLAMA_TIMEOUT_S

    def _atualizar_modelos_ollama(self):
        """Preenche o dropdown com os modelos instalados + recomendados,
        sem travar a GUI. Recomendados que já estão instalados não se repetem."""
        url = self._url_ollama()

        def _buscar():
            try:
                instalados = ollama_bridge.listar_modelos(url)
            except ollama_bridge.OllamaErro:
                instalados = []
            recomendados = [m for m in OLLAMA_MODELOS_RECOMENDADOS
                             if m not in instalados]
            modelos = instalados + recomendados
            def _aplicar():
                atual = self._entry_ollama_modelo.get()
                self._entry_ollama_modelo.configure(values=modelos)
                if atual.strip():
                    self._entry_ollama_modelo.set(atual)
            self.after(0, _aplicar)

        threading.Thread(target=_buscar, daemon=True).start()

    def _verificar_ollama(self):
        self._lbl_status_ollama.configure(text='verificando…', text_color=TEXT_SEC)
        url, modelo = self._url_ollama(), self._modelo_ollama()

        def _checar():
            try:
                st = ollama_bridge.status_modelo(modelo, url)
            except ollama_bridge.OllamaErro:
                self.after(0, lambda: self._lbl_status_ollama.configure(
                    text='✗ Ollama não está respondendo', text_color=C_ERR))
                return
            texto, cor = {
                ollama_bridge.CARREGADO: ('● pronto pra usar', C_OK),
                ollama_bridge.INSTALADO: ('● pronto pra usar', C_OK),
                ollama_bridge.AUSENTE:   ('● modelo não baixado', C_ERR),
            }[st]
            self.after(0, lambda: self._lbl_status_ollama.configure(
                text=texto, text_color=cor))

        threading.Thread(target=_checar, daemon=True).start()

    def _carregar_modelo_ollama(self):
        """Força o load do modelo agora, pra não pagar esse custo na extração."""
        url, modelo = self._url_ollama(), self._modelo_ollama()
        self._lbl_status_ollama.configure(text='carregando modelo… (pode demorar)',
                                          text_color=TEXT_SEC)
        self._btn_carregar_modelo.configure(state='disabled')

        def _carregar():
            try:
                ollama_bridge.pre_carregar(modelo, url)
                texto, cor = '● carregado — pronto pra usar', C_OK
            except ollama_bridge.OllamaErro as exc:
                texto, cor = f'✗ falha ao carregar: {exc}', C_ERR
            def _fim():
                self._lbl_status_ollama.configure(text=texto, text_color=cor)
                self._btn_carregar_modelo.configure(state='normal')
            self.after(0, _fim)

        threading.Thread(target=_carregar, daemon=True).start()

    def _baixar_modelo_ollama(self):
        if self._pull_ativo:            # segundo clique = cancelar
            self._pull_cancelado = True
            return
        url, modelo = self._url_ollama(), self._modelo_ollama()
        self._pull_ativo, self._pull_cancelado = True, False
        self._btn_pull.configure(text='Cancelar', fg_color=C_ERR,
                                 hover_color=C_ERR_HOV)
        self._lbl_pull.configure(text=f'baixando {modelo}…', text_color=TEXT_SEC)
        self._pb_pull.set(0)
        self._pb_pull.pack(fill='x', pady=(6, 0))

        def _progresso(status, baixado, total):
            if total:
                frac = baixado / total
                txt = (f'{status} — {baixado / 1e9:.1f} / {total / 1e9:.1f} GB '
                       f'({frac * 100:.0f}%)')
            else:
                frac, txt = 0, status
            self.after(0, lambda: (self._pb_pull.set(frac),
                                   self._lbl_pull.configure(text=txt)))

        def _baixar():
            try:
                ok = ollama_bridge.puxar_modelo(
                    modelo, url, on_progresso=_progresso,
                    deve_cancelar=lambda: self._pull_cancelado)
                if ok:
                    texto, cor = f'✓ {modelo} baixado', C_OK
                else:
                    texto, cor = 'download cancelado (retoma de onde parou)', TEXT_SEC
            except ollama_bridge.OllamaErro as exc:
                texto, cor = f'✗ falha: {exc}', C_ERR

            def _fim():
                self._pull_ativo = False
                self._btn_pull.configure(text='Baixar modelo', fg_color=GREEN,
                                         hover_color=GREEN_HOV)
                self._pb_pull.pack_forget()
                self._lbl_pull.configure(text=texto, text_color=cor)
                self._atualizar_modelos_ollama()
            self.after(0, _fim)

        threading.Thread(target=_baixar, daemon=True).start()

    def _confirmar_instalar_ollama(self):
        if not messagebox.askyesno(
                'Instalar Ollama',
                'Isso vai rodar o instalador oficial num terminal embutido:\n\n'
                f'  {OLLAMA_INSTALL_CMD}\n\n'
                'O script pode pedir a senha de administrador (sudo) — se travar '
                'pedindo senha, cancele e rode o comando acima manualmente num '
                'terminal.\n\nContinuar?'):
            return
        self._instalar_ollama_gui()

    def _instalar_ollama_gui(self):
        win = ctk.CTkToplevel(self)
        win.title('Instalando Ollama…')
        win.resizable(False, False)
        win.withdraw()
        win.transient(self)

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='Instalando Ollama…', font=_font(13, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 8), anchor='w')

        log = ctk.CTkTextbox(fr, width=440, height=220, font=('TkFixedFont', 10))
        log.pack(padx=20, pady=(0, 12))
        log.configure(state='disabled')

        def _log(txt):
            log.configure(state='normal')
            log.insert('end', txt + '\n')
            log.see('end')
            log.configure(state='disabled')

        btn_fechar = ctk.CTkButton(fr, text='Fechar', width=90, height=30,
                                    fg_color=BG_PANEL, hover_color=GRAY_BORD,
                                    text_color=TEXT_PRI, font=_font(12),
                                    state='disabled', command=win.destroy)
        btn_fechar.pack(pady=(0, 16))

        def _run():
            inicio = time.time()
            code = 1
            try:
                proc = subprocess.Popen(
                    ['sh', '-c', OLLAMA_INSTALL_CMD],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for linha in proc.stdout:
                    win.after(0, lambda l=linha: _log(l.rstrip('\n')))
                    if time.time() - inicio > 600:
                        proc.kill()
                        win.after(0, lambda: _log(
                            '\n✗ Tempo limite excedido (10 min). Abortado.'))
                        break
                code = proc.wait()
            except Exception as exc:
                win.after(0, lambda e=exc: _log(f'Erro: {e}'))

            def _fim():
                if code == 0:
                    _log('\n✓ Instalação concluída. Use "Verificar conexão" na '
                         'aba anterior (pode levar alguns segundos pro serviço subir).')
                else:
                    _log('\n✗ Falha na instalação automática.')
                    _log('Rode manualmente num terminal:')
                    _log(f'  {OLLAMA_INSTALL_CMD}')
                btn_fechar.configure(state='normal')
            win.after(0, _fim)

        threading.Thread(target=_run, daemon=True).start()

        win.update_idletasks()
        win.geometry('480x330')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _confirmar_instalar_docling(self):
        if not messagebox.askyesno(
                'Instalar suporte a OCR',
                'Isso vai instalar o pacote "docling" via pip (≈2 GB, pode '
                'levar 10-20 min dependendo da conexão).\n\nContinuar?'):
            return
        self._instalar_docling_gui()

    def _instalar_docling_gui(self):
        win = ctk.CTkToplevel(self)
        win.title('Instalando suporte a OCR…')
        win.resizable(False, False)
        win.withdraw()
        win.transient(self)

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='Instalando docling (OCR)…', font=_font(13, 'bold'),
                     text_color=TEXT_PRI).pack(padx=20, pady=(16, 8), anchor='w')

        log = ctk.CTkTextbox(fr, width=440, height=220, font=('TkFixedFont', 10))
        log.pack(padx=20, pady=(0, 12))
        log.configure(state='disabled')

        def _log(txt):
            log.configure(state='normal')
            log.insert('end', txt + '\n')
            log.see('end')
            log.configure(state='disabled')

        btn_fechar = ctk.CTkButton(fr, text='Fechar', width=90, height=30,
                                    fg_color=BG_PANEL, hover_color=GRAY_BORD,
                                    text_color=TEXT_PRI, font=_font(12),
                                    state='disabled', command=win.destroy)
        btn_fechar.pack(pady=(0, 16))

        def _run():
            _log('> pip install docling')
            res = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', 'docling']
                + _pip_flags(),
                capture_output=True, text=True
            )
            ok = res.returncode == 0
            if not ok:
                res2 = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '--upgrade', 'docling'],
                    capture_output=True, text=True
                )
                ok = res2.returncode == 0
                if not ok:
                    win.after(0, lambda: _log(
                        f'✗ Falha: {(res.stderr or res2.stderr).strip()[:400]}'))

            def _fim():
                if ok:
                    _log('\n✓ docling instalado. Feche e reabra o Excerpta para '
                         'usar o OCR.')
                else:
                    _log('\nRode manualmente num terminal:')
                    _log('  pip install docling' +
                         ('' if sys.platform == 'win32' else ' --break-system-packages'))
                btn_fechar.configure(state='normal')
            win.after(0, _fim)

        threading.Thread(target=_run, daemon=True).start()

        win.update_idletasks()
        win.geometry('480x330')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _ajuda_ocr(self):
        win = ctk.CTkToplevel(self)
        win.title('O que é OCR?')
        win.resizable(False, False)
        win.withdraw()

        fr = ctk.CTkFrame(win, fg_color=BG_WINDOW)
        fr.pack(fill='both', expand=True)

        ctk.CTkLabel(fr, text='OCR — Reconhecimento Óptico de Caracteres',
                     font=_font(13, 'bold'), text_color=TEXT_PRI
                     ).pack(padx=20, pady=(16, 8), anchor='w')

        texto = (
            'PDFs podem ser de dois tipos:\n\n'
            '• Digital: o texto já está embutido no arquivo — '
            'rápido de extrair (~2s por artigo).\n\n'
            '• Escaneado: o PDF é uma foto de uma página impressa, '
            'sem texto legível por software. O OCR "lê" a imagem e '
            'converte em texto — mais lento (~45s por artigo) e requer '
            'o pacote docling instalado.\n\n'
            'Se desativado, os PDFs escaneados são simplesmente '
            'ignorados durante a extração.'
        )
        ctk.CTkLabel(fr, text=texto, font=_font(12), text_color=TEXT_PRI,
                     justify='left', wraplength=360, anchor='w'
                     ).pack(padx=20, pady=(0, 12), anchor='w')

        ctk.CTkButton(fr, text='Entendi', width=90, height=30,
                      fg_color=GREEN, hover_color=GREEN_HOV,
                      text_color='white', font=_font(12, 'bold'),
                      command=win.destroy).pack(pady=(0, 16))

        win.update_idletasks()
        h = win.winfo_reqheight() + 20
        win.geometry(f'400x{max(h, 200)}')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _fechar(self):
        _salvar_settings({
            **_ler_settings(),
            'usar_ocr': self._var_ocr.get(),
            'mostrar_resumo': self._var_resumo.get(),
            'perguntar_abrir_pasta': self._var_pasta.get(),
            'log_diagnostico': self._var_log.get(),
            'usar_ia_local': self._var_ia_local.get(),
            'ollama_url': self._entry_ollama_url.get().strip() or OLLAMA_URL_PADRAO,
            'ollama_modelo': self._entry_ollama_modelo.get().strip() or OLLAMA_MODELO_PADRAO,
            'ollama_timeout_s': self._timeout_ollama(),
        })
        self.destroy()


# ══════════════════════════════════════ resumo pós-extração ══════════════════════

class StatsWindow(ctk.CTkToplevel):
    def __init__(self, parent, stats, path_saida,
                 fragmentado=False, n_frags=0, kb_frags=None, debug_log_path=None):
        super().__init__(parent)
        self.title('Extração concluída')
        self.resizable(False, False)
        self.withdraw()

        total     = stats.get('total', 0)
        completos = stats.get('completos', 0)
        adaptados = stats.get('adaptados', 0)
        reviews   = stats.get('reviews', 0)
        ignorados = stats.get('ignorados', 0)
        erros     = stats.get('erros', 0)

        extraidos     = completos + adaptados + reviews
        nao_extraidos = ignorados + erros

        frame = ctk.CTkFrame(self, fg_color=BG_WINDOW)
        frame.pack(fill='both', expand=True)

        # Cabeçalho — diz de cara COMO foi (quantos saíram com texto), não só
        # "processados". Ex.: "39 de 42 artigos extraídos".
        hdr_f = ctk.CTkFrame(frame, fg_color=GREEN_HDR, corner_radius=0, height=52)
        hdr_f.pack(fill='x')
        hdr_f.pack_propagate(False)
        if nao_extraidos == 0:
            hdr_txt = f'✓  Todos os {total} artigos foram extraídos'
        else:
            hdr_txt = f'✓  {extraidos} de {total} artigos extraídos'
        ctk.CTkLabel(hdr_f, text=hdr_txt, font=_font(14, 'bold'),
                     text_color=GREEN_TXT).pack(side='left', padx=16, pady=12)
        ctk.CTkFrame(frame, fg_color=DIVIDER, height=1, corner_radius=0).pack(fill='x')

        # Frase-resumo em linguagem simples, logo abaixo do cabeçalho.
        if nao_extraidos == 0:
            resumo_txt = 'Todos os artigos saíram com texto.'
        elif extraidos == 0:
            resumo_txt = 'Nenhum artigo pôde ser extraído — veja abaixo o motivo.'
        else:
            resumo_txt = (f'{extraidos} artigos saíram com texto e {nao_extraidos} não — '
                          f'veja abaixo o que aconteceu.')
        ctk.CTkLabel(frame, text=resumo_txt, font=_font(12), text_color=TEXT_SEC,
                     wraplength=410, justify='left', anchor='w').pack(
                         fill='x', padx=20, pady=(12, 0))

        body = ctk.CTkFrame(frame, fg_color='transparent')
        body.pack(fill='x', padx=20, pady=(8, 6))

        # Rótulos em linguagem direta. Mantém "Complementados automaticamente"
        # (termo já adotado, explicado na nota abaixo).
        rows = []
        if completos > 0:
            rows.append((C_OK,    '✓', str(completos), 'Seções pedidas encontradas'))
        if adaptados > 0:
            rows.append((C_WARN,  '≈', str(adaptados), 'Complementados automaticamente'))
        if reviews > 0:
            rows.append((ACCENT,  '▤', str(reviews),   'Artigos de revisão (texto inteiro)'))
        if ignorados > 0:
            rows.append((GRAY_TEXT,'○', str(ignorados), 'Pulados (suplementar ou PDF sem texto)'))
        if erros > 0:
            rows.append((C_ERR,   '✗', str(erros),     'Falharam (não foi possível ler)'))

        for cor, icone, count, desc in rows:
            r = ctk.CTkFrame(body, fg_color='transparent')
            r.pack(fill='x', pady=3)
            ctk.CTkLabel(r, text=icone, font=_font(13, 'bold'), text_color=cor,
                         width=20, anchor='center').pack(side='left')
            ctk.CTkLabel(r, text=count, font=_font(13, 'bold'), text_color=cor,
                         width=32, anchor='center').pack(side='left', padx=(4, 10))
            ctk.CTkLabel(r, text=desc, font=_font(13), text_color=TEXT_PRI,
                         anchor='w').pack(side='left')

        if adaptados > 0:
            nota = ctk.CTkFrame(frame, fg_color=ACCENT_LT, border_color=ACCENT_BORD,
                                border_width=1, corner_radius=6)
            nota.pack(fill='x', padx=20, pady=(6, 4))
            ctk.CTkLabel(nota,
                         text='"Complementados automaticamente": algumas seções solicitadas\n'
                              'não foram encontradas. O restante do artigo foi incluído\n'
                              'como complemento para não perder conteúdo.',
                         font=_font(11), text_color=ACCENT_TXT,
                         justify='left').pack(padx=12, pady=8, anchor='w')

        if fragmentado and kb_frags:
            resumo = '  ·  '.join(f'parte {i+1}: ~{kb:.0f} KB'
                                   for i, kb in enumerate(kb_frags))
            ctk.CTkLabel(frame, text=resumo, font=_font(11), text_color=TEXT_SEC,
                         wraplength=400, anchor='w').pack(padx=20, pady=(4, 0), fill='x')

        _sep(frame, pady=(10, 0))

        btn_row = ctk.CTkFrame(frame, fg_color='transparent')
        btn_row.pack(pady=12)

        if _get_setting('perguntar_abrir_pasta', True):
            pasta_final = path_saida if fragmentado else os.path.dirname(path_saida)
            ctk.CTkButton(btn_row, text='Abrir pasta de saída', width=164, height=32,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(13, 'bold'),
                         command=lambda: [abrir_no_sistema(pasta_final), self.destroy()]
                         ).pack(side='left', padx=(0, 8))

        if debug_log_path:
            ctk.CTkButton(btn_row, text='Log técnico', width=110, height=32,
                         fg_color=BG_PANEL, hover_color=GRAY_BORD,
                         text_color=TEXT_PRI, font=_font(13),
                         border_width=1, border_color=GRAY_BORD,
                         command=lambda: abrir_no_sistema(debug_log_path)
                         ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(btn_row, text='Fechar', width=90, height=32,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(13),
                     border_width=1, border_color=GRAY_BORD,
                     command=self.destroy).pack(side='left')

        self.update_idletasks()
        h = self.winfo_reqheight() + 20
        self.geometry(f'450x{max(h, 220)}')
        self.deiconify()
        self.lift()
        self.focus_force()
        self.after(50, self.grab_set)


# ══════════════════════════════════════ etapa 3 ══════════════════════════════════

class Etapa3Frame(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=BG_WINDOW)
        self._app          = app
        self._pasta        = ''
        # Nome da coleção do Zotero de onde veio a lista (vazio = pasta do
        # computador); vira o nome padrão da pasta de saída.
        self._nome_zotero  = ''
        self._selecao      = []
        self._selecao_raw  = []
        self._var_tudo       = ctk.BooleanVar(value=True)
        self._var_fallback   = ctk.BooleanVar(value=True)
        self._var_fragmentar = ctk.BooleanVar(value=False)
        self._sec_vars       = {}
        self._sec_btns       = {}
        self._btn_tudo_chip  = None
        self._engine_labels  = {}
        self._tipos_pdf      = {}
        self._hash_cache     = {}
        self._duplicatas_atuais = set()
        self._grupos_possiveis_duplicatas = []
        self._possiveis_duplicatas = set()
        self._cancelando     = False
        self._n_digital      = 0
        self._n_ocr          = 0
        self._lbl_tempo_analise = None
        # Zotero: monitor da coleção aberta + itens arrastados (metadados)
        self._zot_importando  = False
        self._zot_ultimo_drop = ('?', '')
        self._zot_estado      = None
        self._zot_ocupado     = False
        self._zot_after_id    = None
        self._build()
        self._zot_tick()

    def _build(self):
        main = ctk.CTkFrame(self, fg_color='transparent')
        main.pack(fill='both', expand=True, padx=20, pady=(16, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(4, weight=1)   # lista expande (row 4)

        # ── Linha 0: botão Zotero + status (esquerda), importar pasta / configurações (direita) ──
        # Tudo numa linha só, compacto — cabe em telas menores sem esconder o
        # essencial. "Excerpta" já aparece no título da janela, não precisa
        # repetir aqui. Zotero é o fluxo mais usado hoje, por isso fica à
        # esquerda com mais espaço; pasta e configurações são secundários.
        cfg = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                           border_width=1, corner_radius=RAIO_CARD)
        cfg.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        rz = ctk.CTkFrame(cfg, fg_color='transparent')
        rz.pack(fill='x', padx=16, pady=12)

        ctk.CTkButton(rz, text='⚙  Configurações', width=130, height=34,
                      fg_color=BG_PANEL, hover_color=GRAY_BORD,
                      text_color=TEXT_PRI, font=_font(12, 'bold'),
                      border_width=1, border_color=GRAY_BORD,
                      command=self._abrir_settings).pack(side='right')
        self._btn_pasta3 = ctk.CTkButton(
            rz, text='Importar pasta', height=34,
            fg_color=GREEN, hover_color=GREEN_HOV,
            text_color='white', font=_font(13, 'bold'),
            border_width=1, border_color=GREEN_BORD,
            command=self._sel_pasta3)
        self._btn_pasta3.pack(side='right', padx=(0, 8))
        _bind_dnd_widget(self._btn_pasta3, self._on_dnd_pasta3)

        # Botão fica sempre visível (desabilitado sem coleção) para não
        # mudar de lugar quando o Zotero conecta/desconecta.
        self._btn_zot_importar = ctk.CTkButton(
            rz, text='Importar Zotero', width=140, height=34,
            fg_color=ACCENT, hover_color=ACCENT_HOV,
            text_color='white', font=_font(14, 'bold'),
            state='disabled', command=self._zot_importar)
        self._btn_zot_importar.pack(side='left')
        self._lbl_zot = ctk.CTkLabel(rz, text='verificando…', font=_font(14),
                                     text_color=TEXT_SEC, anchor='w')
        self._lbl_zot.pack(side='left', fill='x', expand=True, padx=(10, 10))

        # ── Linha 1: seções a extrair ──────────────────────────────────────────
        sec_card = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                                border_width=1, corner_radius=RAIO_CARD)
        sec_card.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        inner3 = ctk.CTkFrame(sec_card, fg_color='transparent')
        inner3.pack(fill='x', padx=14, pady=(10, 12))

        row_hdr = ctk.CTkFrame(inner3, fg_color='transparent')
        row_hdr.pack(fill='x', pady=(0, 8))
        # No lugar do rótulo "Seções a extrair": o botão já deixa claro do
        # que se trata, e ganha o espaço todo à esquerda.
        self._btn_tudo_chip = ctk.CTkButton(
            row_hdr, text='Artigo completo', width=140, height=32,
            corner_radius=RAIO_CHIP,
            fg_color=GREEN, hover_color=GREEN_HOV, text_color='white',
            font=_font(12, 'bold'), border_width=1, border_color=GREEN_BORD,
            command=self._on_tudo_toggle)
        self._btn_tudo_chip.pack(side='left')
        ctk.CTkCheckBox(row_hdr, text='Se não encontrar, usa completo',
                        variable=self._var_fallback,
                        font=_font(11), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD).pack(side='left', padx=(14, 0))

        sec_labels = [
            ('abstract',   'Abstract'),
            ('introducao', 'Introdução'),
            ('metodos',    'Métodos'),
            ('resultados', 'Resultados'),
            ('discussao',  'Discussão'),
            ('conclusao',  'Conclusão'),
            ('referencias','Referências'),
            ('suplementar','Suplementar'),
        ]

        r1 = ctk.CTkFrame(inner3, fg_color='transparent')
        r1.pack(fill='x', pady=(0, 6))
        r2 = ctk.CTkFrame(inner3, fg_color='transparent')
        r2.pack(fill='x')

        for i, (key, label) in enumerate(sec_labels):
            var = ctk.BooleanVar(value=False)
            self._sec_vars[key] = var
            row = r1 if i < 4 else r2
            b = ctk.CTkButton(row, text=label, height=32, corner_radius=RAIO_CHIP,
                              fg_color=BG_PANEL, text_color=TEXT_OFF,
                              border_color=DIVIDER, hover_color=ACCENT_LT,
                              font=_font(12), border_width=1, state='disabled',
                              command=lambda k=key: self._on_sec_chip_click(k))
            b.pack(side='left', padx=(0, S2))
            self._sec_btns[key] = b

        # ── Linha 2: painel de análise de motores ──────────────────────────────
        self._frame_analise = ctk.CTkFrame(main, fg_color=BG_CARD,
                                            border_color=DIVIDER, border_width=1,
                                            corner_radius=RAIO_CARD)
        self._frame_analise.grid(row=2, column=0, sticky='ew', pady=(0, 4))
        self._frame_analise.grid_remove()

        # ── Linha 3: cabeçalho da lista ────────────────────────────────────────
        row_lhdr = ctk.CTkFrame(main, fg_color='transparent')
        row_lhdr.grid(row=3, column=0, sticky='ew', pady=(0, 4))
        self._lbl_resumo = ctk.CTkLabel(row_lhdr, text='', font=_font(13),
                                         text_color=TEXT_SEC)
        self._lbl_resumo.pack(side='left')
        # Contornados de propósito: são ações auxiliares e, preenchidos, roubavam
        # a atenção do "Extrair artigos", que é a ação principal da tela.
        self._btn_rem_dup3 = ctk.CTkButton(
            row_lhdr, text='Remover duplicatas', width=148, height=30,
            corner_radius=RAIO_CTRL,
            fg_color=C_WARN_LT, hover_color=C_WARN_LT_HOV,
            text_color=C_WARN, font=_font(12, 'bold'),
            border_width=1, border_color=C_WARN_BORD,
            command=self._remover_duplicatas_e3)
        self._btn_revisar_poss3 = ctk.CTkButton(
            row_lhdr, text='Revisar possíveis duplicatas', width=190, height=30,
            corner_radius=RAIO_CTRL,
            fg_color=ACCENT_LT, hover_color=ACCENT_LT_HOV,
            text_color=ACCENT_TXT, font=_font(12, 'bold'),
            border_width=1, border_color=ACCENT_BORD,
            command=self._abrir_revisao_possiveis_duplicatas)
        self._btn_limpar = ctk.CTkButton(
            row_lhdr, text='Limpar lista', width=104, height=30,
            corner_radius=RAIO_CTRL,
            fg_color=C_ERR_LT, hover_color=C_ERR_LT_HOV,
            text_color=C_ERR, font=_font(12, 'bold'),
            border_width=1, border_color=C_ERR_BORD,
            command=self._limpar_lista)
        self._btn_limpar_ocr = ctk.CTkButton(
            row_lhdr, text='Limpar OCR', width=104, height=30,
            corner_radius=RAIO_CTRL,
            fg_color=_COR_DOCLING_LT, hover_color=_COR_DOCLING_LT_HOV,
            text_color=_COR_DOCLING, font=_font(12, 'bold'),
            border_width=1, border_color=_COR_DOCLING_BORD,
            command=self._limpar_ocr)

        # ── Linha 4: lista de artigos (expande) ────────────────────────────────
        # Cartão externo dá borda e cantos; dentro dele vão o cabeçalho fixo de
        # colunas e a área rolável. Ambos usam BG_CARD, então os cantos retos
        # dos filhos não aparecem contra o arredondado do cartão.
        lista_wrap = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                                  border_width=1, corner_radius=RAIO_CARD)
        lista_wrap.grid(row=4, column=0, sticky='nsew')
        lista_wrap.grid_columnconfigure(0, weight=1)
        lista_wrap.grid_rowconfigure(2, weight=1)

        # Mesmo padx e mesmas colunas das linhas: é o que impede o cabeçalho de
        # sair do prumo. O recuo lateral vem de COL_PADX, nunca do frame.
        self._hdr_cols = ctk.CTkFrame(lista_wrap, fg_color='transparent',
                                      height=26)
        self._hdr_cols.grid(row=0, column=0, sticky='ew', padx=1, pady=(S2, S1))
        self._hdr_cols.grid_propagate(False)
        _montar_colunas(self._hdr_cols)
        for col, texto in enumerate(('', 'Arquivo', 'Situação', 'Motor')):
            ctk.CTkLabel(self._hdr_cols, text=texto, font=_font(11, 'bold'),
                         text_color=TEXT_SEC,
                         anchor='e' if col == 3 else 'w'
                         ).grid(row=0, column=col, sticky='ew', padx=COL_PADX[col])

        self._div_cols = ctk.CTkFrame(lista_wrap, fg_color=DIVIDER, height=1,
                                      corner_radius=0)
        self._div_cols.grid(row=1, column=0, sticky='ew')

        self._frame_arts = ctk.CTkScrollableFrame(
            lista_wrap, fg_color=BG_CARD, corner_radius=0, border_width=0)
        self._frame_arts.grid(row=2, column=0, sticky='nsew', padx=1, pady=(0, 1))
        self._frame_arts.bind('<Configure>',
                              lambda _: self._sincronizar_cabecalho())
        self._sobra_barra = None

        _bind_dnd_lista(self._frame_arts, self._on_dnd_lista)
        self._render_vazio()

        # ── Linha 5: fragmentação ──────────────────────────────────────────────
        frag_card = ctk.CTkFrame(main, fg_color=BG_CARD, border_color=DIVIDER,
                                  border_width=1, corner_radius=RAIO_CARD)
        frag_card.grid(row=5, column=0, sticky='ew', pady=(4, 0))
        frag_inner = ctk.CTkFrame(frag_card, fg_color='transparent')
        frag_inner.pack(fill='x', padx=14, pady=(6, 4))

        # Tudo numa linha só: checkbox, campo de tamanho e ajuda. A dica longa
        # de antes ("Divide em vários arquivos...") virou só o tooltip do "?"
        # — o nome do checkbox já entrega o essencial.
        row_fhdr = ctk.CTkFrame(frag_inner, fg_color='transparent')
        row_fhdr.pack(fill='x')
        ctk.CTkCheckBox(row_fhdr, text='Fragmentar arquivo de saída',
                        variable=self._var_fragmentar,
                        command=self._on_fragmentar_toggle,
                        font=_font(13, 'bold'), checkmark_color='white',
                        fg_color=GREEN, hover_color=GREEN_HOV,
                        border_color=GRAY_BORD).pack(side='left')

        self._row_frag_fields = ctk.CTkFrame(row_fhdr, fg_color='transparent')
        self._row_frag_fields.pack(side='left', padx=(16, 0))

        ctk.CTkLabel(self._row_frag_fields, text='máx.',
                     font=_font(12), text_color=TEXT_SEC, anchor='w'
                     ).pack(side='left', padx=(0, 6))
        self._entry_frag_kb = ctk.CTkEntry(
            self._row_frag_fields, width=64, height=28, font=_font(13),
            justify='center')
        self._entry_frag_kb.insert(0, '500')
        self._entry_frag_kb.configure(state='disabled')
        self._entry_frag_kb.pack(side='left')
        ctk.CTkLabel(self._row_frag_fields, text='KB', font=_font(12),
                     text_color=TEXT_SEC).pack(side='left', padx=(6, 0))
        # Texto em vez de ícone: diz exatamente o que o botão abre (tamanhos
        # recomendados por modelo de IA), sem depender de o usuário adivinhar
        # o símbolo.
        ctk.CTkButton(self._row_frag_fields, text='Recomendação', height=22,
                     corner_radius=11,
                     fg_color=ACCENT_LT, hover_color=ACCENT_LT_HOV,
                     text_color=ACCENT, font=_font(11, 'bold'),
                     border_width=0,
                     command=self._mostrar_ajuda_fragmentar
                     ).pack(side='left', padx=(10, 0))

        # "Extrair artigos" na mesma linha e mesma altura do checkbox de
        # fragmentar — é a ação final, faz sentido fechar essa linha em vez
        # de abrir mais uma só pra ele lá embaixo.
        self._btn_extrair = ctk.CTkButton(
            row_fhdr, text='↓  Extrair artigos', width=160,
            fg_color=GREEN, hover_color=GREEN_HOV, height=30,
            font=_font(14, 'bold'), state='disabled', command=self._extrair)
        self._btn_extrair.pack(side='right')

        # Nome de saída: morava em Configurações, mudou pra cá porque é usado
        # toda hora, junto do resto da fragmentação.
        row_nome_saida = ctk.CTkFrame(frag_inner, fg_color='transparent')
        row_nome_saida.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(row_nome_saida, text='Nome de saída:', font=_font(12),
                     text_color=TEXT_SEC).pack(side='left')
        self._entry_nome_saida = ctk.CTkEntry(
            row_nome_saida, width=170, height=26, font=_font(12),
            placeholder_text='ex: minha_extracao')
        self._entry_nome_saida.insert(0, _get_setting('nome_base', ''))
        self._entry_nome_saida.pack(side='left', padx=(8, 0))
        ctk.CTkLabel(row_nome_saida, text='vazio = nome da coleção do Zotero ou extracao_excerpta',
                     font=_font(11), text_color=TEXT_SEC
                     ).pack(side='left', padx=(8, 0))

        self._entry_frag_kb.bind('<KeyRelease>',
                                  lambda _: self._atualizar_preview_fragmentos())

        self._lbl_preview_frags = ctk.CTkLabel(
            frag_inner, text='', font=_font(12), text_color=TEXT_SEC,
            anchor='w', justify='left')
        self._lbl_preview_frags.pack(fill='x', pady=(4, 0))

        # ── Linha 6: separador + rodapé ────────────────────────────────────────
        ctk.CTkFrame(main, fg_color=DIVIDER, height=1, corner_radius=0
                     ).grid(row=6, column=0, sticky='ew', pady=(6, 4))
        foot = ctk.CTkFrame(main, fg_color='transparent')
        foot.grid(row=7, column=0, sticky='ew')
        self._lbl_status3 = ctk.CTkLabel(foot, text='', font=_font(13),
                                          text_color=TEXT_SEC,
                                          anchor='w')
        self._lbl_status3.pack(side='left')
        self._prog3 = ctk.CTkProgressBar(foot, mode='determinate', width=200,
                                          height=6, progress_color=GREEN, fg_color=DIVIDER)

        # Qualquer área da tela aceita soltar PDFs — não só a lista de
        # artigos. O rótulo da pasta fica de fora porque ele já interpreta a
        # soltura de outro jeito (define a pasta, não adiciona à lista).
        _bind_dnd_recursivo(self, self._on_dnd_lista, pular={self._btn_pasta3})

    # ── lógica ──────────────────────────────────────────────────────────────────

    def _sincronizar_cabecalho(self):
        """Desconta do cabeçalho a largura que a barra de rolagem tira da área
        rolável. Sem isso a coluna elástica mede diferente nos dois e as
        colunas da direita saem do prumo."""
        barra = getattr(self._frame_arts, '_scrollbar', None)
        try:
            sobra = barra.winfo_width() if barra.winfo_ismapped() else 0
        except Exception:
            sobra = 0
        if sobra != self._sobra_barra:          # evita relayout a cada evento
            self._sobra_barra = sobra
            self._hdr_cols.grid_configure(padx=(1, 1 + sobra))

    def _render_vazio(self):
        """Estado vazio da lista. Um lugar só — o drop no rótulo precisa ser
        religado toda vez que ele é recriado, o que o _limpar_lista esquecia."""
        self._mostrar_colunas(False)
        self._lbl_sem_ia = ctk.CTkLabel(
            self._frame_arts,
            text='Nenhum artigo ainda\n\nSelecione uma pasta, arraste PDFs aqui\n'
                 'ou importe uma coleção do Zotero.',
            text_color=TEXT_SEC, font=_font(13), justify='center')
        self._lbl_sem_ia.pack(pady=S6 * 2)
        _bind_dnd_rotulo(self._lbl_sem_ia, self._on_dnd_lista)

    def _mostrar_colunas(self, visivel):
        """O cabeçalho de colunas só faz sentido com a lista preenchida."""
        for w in (self._hdr_cols, self._div_cols):
            if visivel:
                w.grid()
            else:
                w.grid_remove()

    def _on_tudo_toggle(self):
        self._var_tudo.set(not self._var_tudo.get())
        tudo_on = self._var_tudo.get()
        if tudo_on:
            self._btn_tudo_chip.configure(
                fg_color=GREEN, text_color='white', border_color=GREEN_BORD)
            for key in self._sec_btns:
                self._sec_vars[key].set(False)
                self._sec_btns[key].configure(
                    state='disabled', fg_color=BG_PANEL,
                    text_color=TEXT_OFF, border_color=DIVIDER)
        else:
            self._btn_tudo_chip.configure(
                fg_color=BG_CARD, text_color=TEXT_SEC, border_color=GRAY_BORD)
            for key in self._sec_btns:
                self._sec_btns[key].configure(state='normal')
                self._render_sec_chip(key)

    def _render_sec_chip(self, key):
        on = self._sec_vars[key].get()
        self._sec_btns[key].configure(
            fg_color=GREEN     if on else BG_CARD,
            text_color='white' if on else TEXT_SEC,
            border_color=GREEN_BORD if on else GRAY_BORD,
            hover_color=GREEN_HOV   if on else ACCENT_LT)

    def _on_sec_chip_click(self, key):
        self._sec_vars[key].set(not self._sec_vars[key].get())
        self._render_sec_chip(key)

    def _on_fragmentar_toggle(self):
        estado = 'normal' if self._var_fragmentar.get() else 'disabled'
        self._entry_frag_kb.configure(state=estado)
        self._atualizar_preview_fragmentos()

    def _atualizar_preview_fragmentos(self):
        prontos = [a for a in self._selecao if a['encontrado']]
        n = len(prontos)

        if n == 0:
            self._lbl_preview_frags.configure(text='')
            return

        if not self._var_fragmentar.get():
            self._lbl_preview_frags.configure(
                text=f'→ 1 arquivo de saída com {n} artigo(s)',
                text_color=GREEN_TXT)
            return

        try:
            max_kb = max(10.0, float(
                self._entry_frag_kb.get().strip().replace(',', '.')))
        except ValueError:
            self._lbl_preview_frags.configure(
                text='⚠  Valor de KB inválido', text_color=C_WARN)
            return

        # Texto/markdown extraído de um PDF científico costuma ficar entre
        # 3% e 8% do tamanho do PDF original; 0.008 (0.8%) subestimava tanto
        # que o número de fragmentos praticamente nunca mudava ao editar a
        # lista, só o KB total exibido.
        total_est_kb = 0.0
        for art in prontos:
            try:
                total_est_kb += os.path.getsize(art['caminho']) / 1024 * 0.05
            except OSError:
                total_est_kb += 25.0

        import math
        n_frags = max(1, math.ceil(total_est_kb / max_kb))
        arts_por_frag = math.ceil(n / n_frags)

        self._lbl_preview_frags.configure(
            text=f'→ estimativa: {n_frags} arquivo(s) '
                 f'· ~{arts_por_frag} artigo(s) cada '
                 f'· total estimado ~{total_est_kb:.0f} KB',
            text_color=GREEN_TXT if n_frags <= 5 else C_WARN)

    def _get_secoes_config(self):
        if self._var_tudo.get():
            return ['tudo']
        selecionadas = [k for k, v in self._sec_vars.items() if v.get()]
        return selecionadas if selecionadas else ['tudo']

    def _sel_pasta3(self):
        pasta = filedialog.askdirectory(
            title='Selecionar pasta com PDFs originais',
            initialdir=self._pasta or os.path.expanduser('~'))
        if pasta:
            self._aplicar_pasta3(pasta)

    def _limpar_pasta3(self):
        """Desfaz a seleção de pasta — clicar de novo no botão volta a abrir
        o diálogo em vez de reabrir a mesma pasta."""
        self._pasta = ''
        self._btn_pasta3.configure(text='Importar pasta', command=self._sel_pasta3)

    def _aplicar_pasta3(self, pasta):
        if not pasta:
            return
        if not os.path.isdir(pasta):
            if os.path.exists(pasta):
                return
            if not messagebox.askyesno(
                    'Pasta não existe',
                    f'A pasta abaixo ainda não existe:\n\n{pasta}\n\nCriar agora?'):
                return
            try:
                os.makedirs(pasta, exist_ok=True)
            except OSError as exc:
                messagebox.showerror('Erro', f'Não foi possível criar a pasta:\n{exc}')
                return
        self._pasta = pasta
        self._nome_zotero = ''
        self._btn_pasta3.configure(
            text=os.path.basename(pasta.rstrip(os.sep)) or pasta,
            command=self._limpar_pasta3)
        _gravar_recente('pastas', pasta)
        # Carrega automaticamente todos os PDFs da pasta
        pdfs = sorted(f for f in os.listdir(pasta) if f.lower().endswith('.pdf'))
        if not pdfs:
            self._selecao_raw = []
            self._rebuild_artigos()
            self._lbl_status3.configure(text='Nenhum PDF encontrado na pasta.')
            return
        self._selecao_raw = [{'arquivo': nome, 'secoes': ['tudo']} for nome in pdfs]
        self._rebuild_artigos()
        self._lbl_status3.configure(text=f'{len(pdfs)} PDF(s) carregado(s) da pasta')

    def _on_dnd_pasta3(self, event):
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        if not paths:
            return
        path = paths[0]
        pasta = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.isdir(pasta):
            self._aplicar_pasta3(pasta)

    def _on_dnd_lista(self, event):
        self._zot_ultimo_drop = (getattr(event, 'type', '?'), str(event.data)[:600])
        try:
            paths = self.winfo_toplevel().tk.splitlist(event.data)
        except Exception:
            paths = []      # texto de citação não é lista Tcl válida
        pdfs = [p for p in paths if p.lower().endswith('.pdf') and os.path.isfile(p)]
        if not pdfs:
            if any(os.path.exists(p) for p in paths):
                return          # arquivo/pasta que não é PDF: ignora, como antes
            self._zot_soltar_texto(event.data)
            return
        for p in pdfs:
            self._selecao_raw.append({
                'arquivo': os.path.basename(p),
                'secoes': ['tudo'],
                'caminho_completo': p,
            })
        self._rebuild_artigos()
        self._lbl_status3.configure(text=f'{len(pdfs)} PDF(s) adicionado(s)')

    # ── Zotero ──────────────────────────────────────────────────────────────

    def _zot_agendar(self, fn):
        """after(0) tolerante à janela ter sido fechada no meio da consulta.

        O monitor roda a cada 2,5 s, então fechar o Excerpta com uma consulta
        em voo é comum — sem isso a thread morre cuspindo TclError no terminal.
        """
        try:
            self.after(0, fn)
        except Exception:
            pass

    def _zot_tick(self):
        """Poll da coleção aberta no Zotero; rede em thread, UI pela fila do Tk."""
        if self._zot_ocupado or self._zot_importando:
            self._zot_reagendar()
            return
        self._zot_ocupado = True

        def _rodar():
            estado = None
            try:
                estado = zotero_bridge.colecao_selecionada()
            except Exception:
                pass
            self._zot_agendar(lambda e=estado: self._zot_aplicar(e))

        threading.Thread(target=_rodar, daemon=True).start()

    def _zot_aplicar(self, estado):
        self._zot_ocupado = False
        if not self._zot_importando:
            self._zot_estado = estado if estado and estado.get('key') else None
            if not estado:
                # Neutro, não vermelho: Zotero fechado é o estado normal de
                # quem não usa a integração, não um erro que o usuário causou.
                self._lbl_zot.configure(text='○  Zotero não conectado',
                                        text_color=TEXT_SEC)
                self._btn_zot_importar.configure(state='disabled')
            elif not estado.get('key'):
                self._lbl_zot.configure(
                    text=f'●  {estado["nome"]} — selecione uma coleção no Zotero',
                    text_color=TEXT_SEC)
                self._btn_zot_importar.configure(state='disabled')
            else:
                n = estado.get('n_itens')
                self._lbl_zot.configure(
                    text=f'●  {estado["nome"]}' + (f'  ·  {n} artigo(s)' if n else ''),
                    text_color=ACCENT_TXT)
                self._btn_zot_importar.configure(state='normal')
        self._zot_reagendar()

    def _zot_reagendar(self, ms=2500):
        """Um único timer: reagendar sem cancelar duplicaria a cadeia de polls."""
        if self._zot_after_id is not None:
            try:
                self.after_cancel(self._zot_after_id)
            except Exception:
                pass
        self._zot_after_id = self.after(ms, self._zot_tick)

    def _zot_importar(self):
        estado = self._zot_estado
        if not estado or self._zot_importando:
            return
        self._zot_importando = True
        self._btn_zot_importar.configure(state='disabled', text='Importando…')
        key, nome = estado['key'], estado['nome']

        def _rodar():
            try:
                itens = zotero_bridge.listar_itens(
                    key, on_progresso=lambda c, t: self._zot_agendar(
                        lambda c=c, t=t: self._lbl_status3.configure(
                            text=f'Zotero: lendo {c}/{t} itens…')))
                prontos, problemas = zotero_bridge.resolver_pdfs(itens)
                r = {'prontos': prontos, 'problemas': problemas, 'nome': nome}
            except Exception as exc:
                r = {'erro': str(exc)}
            self._zot_agendar(lambda r=r: self._zot_fim_importacao(r))

        threading.Thread(target=_rodar, daemon=True).start()

    def _zot_soltar_texto(self, texto):
        """Recebe itens arrastados do Zotero e os casa com a biblioteca.

        Arrastar um item **pai** não entrega arquivo nenhum: o Zotero só põe o
        PDF no arrastar quando o que se arrasta é o próprio anexo
        (utilities_internal.js filtra por isAttachment). Do item pai vem só o
        texto do Quick Copy — citação ou JSON —, que aqui é reidentificado na
        biblioteca por sobrenome + ano, desempatando pelo título.
        """
        if not texto or not texto.strip() or self._zot_importando:
            return
        self._zot_importando = True
        self._lbl_status3.configure(text='Zotero: identificando os artigos…')

        def _rodar():
            try:
                itens, nao_casadas = zotero_bridge.itens_de_texto_arrastado(
                    texto,
                    on_progresso=lambda c, t: self._zot_agendar(
                        lambda c=c, t=t: self._lbl_status3.configure(
                            text=f'Zotero: identificando {c}/{t}…')))
                prontos, problemas = zotero_bridge.resolver_pdfs(itens)
                resultado = {'prontos': prontos, 'problemas': problemas,
                             'nome': 'Zotero', 'nao_casadas': nao_casadas}
            except Exception as exc:
                resultado = {'erro': str(exc)}
            self._zot_agendar(lambda r=resultado: self._zot_fim_importacao(r))

        threading.Thread(target=_rodar, daemon=True).start()

    def _zot_fim_importacao(self, resultado):
        self._zot_importando = False
        self._btn_zot_importar.configure(state='normal', text='Importar Zotero')

        if resultado.get('erro'):
            self._lbl_status3.configure(text='')
            messagebox.showerror('Zotero', resultado['erro'])
            return

        prontos   = resultado['prontos']
        problemas = resultado['problemas']
        nome      = resultado['nome']

        ja_na_lista = {item.get('caminho_completo') for item in self._selecao_raw}
        novos     = [p for p in prontos if p['caminho_completo'] not in ja_na_lista]
        repetidos = len(prontos) - len(novos)

        if novos:
            self._selecao_raw.extend(novos)
            self._rebuild_artigos()
            # Arrastar do Zotero não diz de qual coleção veio ('Zotero').
            if nome != 'Zotero':
                self._nome_zotero = nome

        self._lbl_status3.configure(
            text=f'✓  {len(novos)} PDF(s) importado(s) do Zotero — "{nome}"')

        if not novos and not prontos:
            tipo, bruto = getattr(self, '_zot_ultimo_drop', ('?', ''))
            messagebox.showinfo(
                'Arrastar do Zotero',
                'Não consegui identificar nenhum artigo no que foi arrastado.'
                '\n\n'
                'Ao arrastar um item do Zotero vêm apenas os metadados, no '
                'formato configurado em Zotero → Configurações → Exportar → '
                'Formato do item. O Excerpta usa autor e ano para reencontrar '
                'o artigo na sua biblioteca.\n\n'
                'Use o campo "Etiqueta do Zotero" como alternativa: etiquete '
                'os artigos no Zotero e importe por ali.\n\n'
                '─────────────────────────────\n'
                f'Diagnóstico — tipo recebido: {tipo}\n'
                f'Conteúdo recebido:\n{bruto[:400] or "(vazio)"}')
            return

        linhas = []
        nao_casadas = resultado.get('nao_casadas') or []
        if nao_casadas:
            linhas.append(
                f'{len(nao_casadas)} referência(s) não foram identificadas na '
                'biblioteca do Zotero e ficaram de fora.')
        if repetidos:
            linhas.append(f'{repetidos} já estava(m) na lista e não '
                          'foram adicionados de novo.')
        if problemas['sem_pdf']:
            linhas.append(f'{len(problemas["sem_pdf"])} item(ns) sem PDF anexado '
                          'no Zotero.')
        if problemas['sem_arquivo']:
            linhas.append(
                f'{len(problemas["sem_arquivo"])} item(ns) têm PDF registrado no '
                'Zotero, mas o arquivo não está neste computador — sincronize '
                'os anexos no Zotero e importe de novo.')
        if problemas['multiplos']:
            linhas.append(
                f'{len(problemas["multiplos"])} item(ns) tinham mais de um PDF; '
                'foi importado o anexo principal de cada um.')
        if linhas:
            messagebox.showinfo(
                'Importação do Zotero',
                f'{len(novos)} PDF(s) importado(s) de "{nome}".\n\n'
                + '\n\n'.join(f'• {l}' for l in linhas))

    def _rebuild_artigos(self):
        if not self._selecao_raw:
            self._selecao = []
            self._engine_labels.clear()
            self._tipos_pdf = {}
            for w in self._frame_arts.winfo_children():
                w.destroy()
            self._render_vazio()
            self._lbl_resumo.configure(text='')
            self._frame_analise.grid_remove()
            self._btn_rem_dup3.pack_forget()
            self._btn_revisar_poss3.pack_forget()
            self._btn_limpar.pack_forget()
            self._btn_limpar_ocr.pack_forget()
            self._btn_extrair.configure(state='disabled')
            return
        self._selecao = []
        self._engine_labels.clear()
        self._tipos_pdf = {}
        for item in self._selecao_raw:
            caminho    = item.get('caminho_completo') or (
                os.path.join(self._pasta, item['arquivo']) if self._pasta else '')
            encontrado = os.path.isfile(caminho) if caminho else False
            self._selecao.append({
                'arquivo': item['arquivo'], 'secoes': item['secoes'],
                'caminho': caminho, 'encontrado': encontrado,
            })

        nomes_vistos  = set()
        hashes_vistos = set()
        duplicatas    = set()
        for art in self._selecao:
            nome = art['arquivo']
            e_dup = nome in nomes_vistos
            if not e_dup and art['encontrado']:
                h = _hash_arquivo(art['caminho'], self._hash_cache)
                if h:
                    if h in hashes_vistos:
                        e_dup = True
                    else:
                        hashes_vistos.add(h)
            if e_dup:
                duplicatas.add(nome)
            nomes_vistos.add(nome)
        self._duplicatas_atuais = duplicatas
        self._grupos_possiveis_duplicatas = _detectar_grupos_possiveis_duplicatas(
            self._selecao, self._hash_cache, duplicatas)
        self._possiveis_duplicatas = {
            nome for grupo in self._grupos_possiveis_duplicatas for nome in grupo}

        for w in self._frame_arts.winfo_children():
            w.destroy()

        n_ok  = sum(1 for a in self._selecao if a['encontrado'])
        n_err = len(self._selecao) - n_ok

        self._mostrar_colunas(bool(self._selecao))

        for i, art in enumerate(self._selecao):
            e_dup      = art['arquivo'] in duplicatas
            e_possivel = art['arquivo'] in self._possiveis_duplicatas
            row_bg = (C_WARN_LT if e_dup else
                      ACCENT_LT if e_possivel else
                      (BG_CARD if i % 2 == 0 else BG_ZEBRA))
            linha  = ctk.CTkFrame(self._frame_arts, fg_color=row_bg,
                                  corner_radius=0, height=ALT_LINHA)
            linha.pack(fill='x', padx=0)
            linha.pack_propagate(False)
            _montar_colunas(linha)
            _bind_dnd_widget(linha, self._on_dnd_lista)

            icone = '✓' if art['encontrado'] else '✗'
            icor  = C_OK if art['encontrado'] else C_ERR
            ctk.CTkLabel(linha, text=icone, text_color=icor,
                         font=_font(13, 'bold'), anchor='w'
                         ).grid(row=0, column=0, sticky='ew', padx=COL_PADX[0])

            # Elide no meio: o fim do nome costuma ter ano/autor, que é o que
            # distingue dois arquivos com o mesmo começo.
            cor_nome = C_WARN if e_dup else (ACCENT_TXT if e_possivel else TEXT_PRI)
            ctk.CTkLabel(linha, text=_encurtar(art['arquivo']),
                         font=_font(13), text_color=cor_nome,
                         anchor='w').grid(row=0, column=1, sticky='ew',
                                          padx=COL_PADX[1])

            if e_dup:
                situacao, cor_sit = 'duplicata', C_WARN
            elif e_possivel:
                situacao, cor_sit = 'possível duplicata — revisar', ACCENT_TXT
            elif not art['encontrado']:
                situacao = ('pasta não definida' if not art['caminho']
                            else 'arquivo não encontrado')
                cor_sit = C_ERR
            else:
                situacao, cor_sit = '', TEXT_SEC
            ctk.CTkLabel(linha, text=situacao, text_color=cor_sit,
                         font=_font(11), anchor='w'
                         ).grid(row=0, column=2, sticky='ew', padx=COL_PADX[2])

            lbl_engine = ctk.CTkLabel(linha, text='…' if art['encontrado'] else '',
                                      font=_font(11), text_color=TEXT_SEC,
                                      anchor='e')
            lbl_engine.grid(row=0, column=3, sticky='ew', padx=COL_PADX[3])
            if art['encontrado']:
                self._engine_labels[art['arquivo']] = lbl_engine

        resumo = f'{len(self._selecao)} artigo(s)'
        if n_err:
            resumo += f'  ·  ⚠ {n_err} não encontrado(s)'
        if duplicatas:
            resumo += f'  ·  ⚠ {len(duplicatas)} duplicata(s)'
        if self._possiveis_duplicatas:
            resumo += (f'  ·  {len(self._possiveis_duplicatas)} '
                       'possível(is) duplicata(s) — revisar')
        self._lbl_resumo.configure(text=resumo)

        self._btn_extrair.configure(
            state='normal' if n_ok > 0 and MOTOR_OK else 'disabled')
        if not MOTOR_OK:
            self._lbl_status3.configure(
                text='⚠  Nenhum motor instalado. Execute: pip install pymupdf4llm')

        if duplicatas:
            self._btn_rem_dup3.pack(side='right', padx=(0, 6))
        else:
            self._btn_rem_dup3.pack_forget()
        if self._possiveis_duplicatas:
            self._btn_revisar_poss3.configure(
                text=f'Revisar possíveis duplicatas ({len(self._grupos_possiveis_duplicatas)})')
            self._btn_revisar_poss3.pack(side='right', padx=(0, 6))
        else:
            self._btn_revisar_poss3.pack_forget()
        self._btn_limpar_ocr.pack_forget()
        self._btn_limpar.pack(side='right')
        self._atualizar_preview_fragmentos()

        # Iniciar análise de motores em background
        prontos = [a for a in self._selecao if a['encontrado']]
        if prontos:
            threading.Thread(
                target=self._analise_motores_bg,
                args=(prontos,), daemon=True).start()

    def _analise_motores_bg(self, artigos):
        """Thread de background: detecta tipo de cada PDF e atualiza UI."""
        resultados = {}
        for art in artigos:
            tipo = detectar_tipo_pdf(art['caminho'])
            motor = _motor_para_tipo(tipo)
            resultados[art['arquivo']] = (tipo, motor)

        def _aplicar():
            self._tipos_pdf = {arq: tipo for arq, (tipo, _) in resultados.items()}
            n_digital  = sum(1 for t, _ in resultados.values() if t == 'digital')
            n_ocr      = sum(1 for t, _ in resultados.values() if t == 'escaneado')
            _usar_ocr  = _get_setting('usar_ocr', True)
            t_est      = n_digital * _TEMPO_DIGITAL_S + (n_ocr * _TEMPO_OCR_S if _usar_ocr else 0)

            for arquivo, (tipo, motor) in resultados.items():
                lbl = self._engine_labels.get(arquivo)
                if lbl:
                    cor = _COR_PYMUPDF if motor == 'pymupdf4llm' else _COR_DOCLING
                    lbl.configure(text=motor, text_color=cor)

            self._atualizar_frame_analise(n_digital, n_ocr, t_est)

        self.after(0, _aplicar)

    def _atualizar_frame_analise(self, n_digital, n_ocr, t_est):
        for w in self._frame_analise.winfo_children():
            w.destroy()

        if n_ocr > 0:
            self._btn_limpar_ocr.pack(side='right', padx=(0, 6))
        else:
            self._btn_limpar_ocr.pack_forget()

        if n_digital == 0 and n_ocr == 0:
            self._frame_analise.grid_remove()
            return

        self._n_digital = n_digital
        self._n_ocr     = n_ocr

        inner = ctk.CTkFrame(self._frame_analise, fg_color='transparent')
        inner.pack(fill='x', padx=12, pady=(6, 7))

        hdr = ctk.CTkFrame(inner, fg_color='transparent')
        hdr.pack(fill='x')
        ctk.CTkLabel(hdr, text='Motores de extração', font=_font(12, 'bold'),
                     text_color=TEXT_PRI, anchor='w').pack(side='left')

        t_str = f'~{t_est:.0f}s' if t_est < 60 else f'~{t_est / 60:.1f}min'
        self._lbl_tempo_analise = ctk.CTkLabel(
            hdr, text=f'⏱ {t_str} estimados', font=_font(11), text_color=TEXT_SEC)
        self._lbl_tempo_analise.pack(side='right')

        rows_data = []
        if n_digital > 0:
            rows_data.append((f'pymupdf4llm  ·  digital  ·  {n_digital} PDF(s)', _COR_PYMUPDF))
        if n_ocr > 0:
            rows_data.append((f'docling OCR  ·  escaneado  ·  {n_ocr} PDF(s)', _COR_DOCLING))

        for txt, cor in rows_data:
            r = ctk.CTkFrame(inner, fg_color='transparent')
            r.pack(fill='x', pady=(2, 0))
            ctk.CTkLabel(r, text='●', font=_font(10), text_color=cor,
                         width=14).pack(side='left')
            ctk.CTkLabel(r, text=txt, font=_font(11), text_color=TEXT_SEC,
                         anchor='w').pack(side='left', padx=(2, 0))

        if n_ocr > 0 and not _get_setting('usar_ocr', True):
            r_ocr = ctk.CTkFrame(inner, fg_color='transparent')
            r_ocr.pack(fill='x', pady=(6, 0))
            ctk.CTkLabel(r_ocr, text='○', font=_font(10), text_color=GRAY_TEXT,
                         width=14).pack(side='left')
            ctk.CTkLabel(r_ocr,
                         text=f'OCR desativado — {n_ocr} escaneado(s) serão ignorados'
                              '  ·  ativar em Configurações',
                         font=_font(11), text_color=GRAY_TEXT,
                         anchor='w').pack(side='left', padx=(2, 0))

        self._frame_analise.grid()

    def _remover_duplicatas_e3(self):
        self._rebuild_artigos()  # garante self._duplicatas_atuais em dia
        duplicatas = self._duplicatas_atuais
        nova_selecao_raw = [item for item in self._selecao_raw
                             if item['arquivo'] not in duplicatas]
        removidos = len(self._selecao_raw) - len(nova_selecao_raw)
        self._selecao_raw = nova_selecao_raw
        self._rebuild_artigos()
        self._lbl_status3.configure(
            text=f'✓  {removidos} entrada(s) duplicada(s) removida(s)')

    def _abrir_revisao_possiveis_duplicatas(self):
        self._rebuild_artigos()  # garante que os grupos estão em dia
        grupos = [g for g in self._grupos_possiveis_duplicatas if len(g) > 1]
        if not grupos:
            return

        by_nome = {a['arquivo']: a for a in self._selecao}

        def tam_kb(nome):
            try:
                return os.path.getsize(by_nome[nome]['caminho']) / 1024
            except OSError:
                return 0.0

        win = ctk.CTkToplevel(self)
        win.title('Revisar possíveis duplicatas')
        win.resizable(False, False)
        win.withdraw()

        ctk.CTkLabel(win, text=f'{len(grupos)} grupo(s) de possível duplicata',
                     font=_font(15, 'bold'), text_color=TEXT_PRI
                     ).pack(padx=20, pady=(16, 2), anchor='w')
        ctk.CTkLabel(win,
                     text='Mesmo título, provavelmente truncado em pontos diferentes\n'
                          '— o conteúdo não é idêntico byte a byte, por isso não foi\n'
                          'removido automaticamente. O maior arquivo de cada grupo já\n'
                          'fica desmarcado (mantido); os demais vêm marcados para\n'
                          'remoção — revise antes de confirmar.',
                     font=_font(11), text_color=TEXT_SEC, anchor='w', justify='left'
                     ).pack(padx=20, pady=(0, 10), anchor='w')

        scroll = ctk.CTkScrollableFrame(win, fg_color=BG_WINDOW, width=560, height=340)
        scroll.pack(fill='both', expand=True, padx=20, pady=(0, 10))

        checkboxes = {}  # arquivo -> BooleanVar
        for gi, grupo in enumerate(grupos):
            card = ctk.CTkFrame(scroll, fg_color=BG_CARD, border_color=DIVIDER,
                                 border_width=1, corner_radius=RAIO_CARD)
            card.pack(fill='x', pady=(0, 10))
            ctk.CTkLabel(card, text=f'Grupo {gi + 1}', font=_font(11, 'bold'),
                         text_color=ACCENT_TXT, anchor='w'
                         ).pack(fill='x', padx=12, pady=(8, 2))

            nomes_ordenados = sorted(grupo, key=tam_kb, reverse=True)
            for idx, nome in enumerate(nomes_ordenados):
                # primeiro (maior arquivo) fica mantido por padrão; os demais já
                # vêm marcados pra remoção, pra facilitar — o usuário só revisa.
                var = ctk.BooleanVar(value=(idx > 0))
                checkboxes[nome] = var
                row = ctk.CTkFrame(card, fg_color='transparent')
                row.pack(fill='x', padx=12, pady=2)
                ctk.CTkCheckBox(row, text='', variable=var, width=20,
                                checkmark_color='white', fg_color=C_WARN,
                                hover_color=C_WARN_HOV, border_color=GRAY_BORD
                                ).pack(side='left')
                nome_exib = nome if len(nome) <= 46 else nome[:43] + '…'
                ctk.CTkLabel(row, text=nome_exib, font=_font(12),
                            text_color=TEXT_PRI, anchor='w'
                            ).pack(side='left', padx=(4, 8))
                ctk.CTkButton(row, text='Abrir', width=56, height=24,
                             fg_color=BG_PANEL, hover_color=ACCENT_LT,
                             text_color=ACCENT, font=_font(11),
                             border_width=1, border_color=ACCENT_BORD,
                             command=lambda c=by_nome[nome]['caminho']: abrir_no_sistema(c)
                             ).pack(side='right')
                ctk.CTkLabel(row, text=f'{tam_kb(nome):,.0f} KB', font=_font(11),
                            text_color=TEXT_SEC, width=70, anchor='e'
                            ).pack(side='right', padx=(0, 6))
            ctk.CTkFrame(card, fg_color='transparent', height=4).pack()

        def _aplicar():
            selecionados = {nome for nome, var in checkboxes.items() if var.get()}
            if selecionados:
                self._selecao_raw = [item for item in self._selecao_raw
                                     if item['arquivo'] not in selecionados]
                self._rebuild_artigos()
                self._lbl_status3.configure(
                    text=f'✓  {len(selecionados)} possível(is) duplicata(s) removida(s)')
            win.destroy()

        btns = ctk.CTkFrame(win, fg_color='transparent')
        btns.pack(fill='x', padx=20, pady=(0, 16))
        ctk.CTkButton(btns, text='Fechar', width=90, height=32,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(13),
                     command=win.destroy).pack(side='left')
        ctk.CTkButton(btns, text='Remover selecionados', width=180, height=32,
                     fg_color=C_WARN, hover_color=C_WARN_HOV,
                     text_color='white', font=_font(13, 'bold'),
                     command=_aplicar).pack(side='right')

        win.update_idletasks()
        win.geometry('620x630')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _limpar_lista(self):
        self._selecao_raw = []
        self._selecao     = []
        self._pasta       = ''
        self._nome_zotero = ''
        self._engine_labels.clear()
        self._limpar_pasta3()
        self._frame_analise.grid_remove()
        for w in self._frame_arts.winfo_children():
            w.destroy()
        self._render_vazio()
        self._lbl_resumo.configure(text='')
        self._lbl_status3.configure(text='')
        self._lbl_preview_frags.configure(text='')
        self._btn_rem_dup3.pack_forget()
        self._btn_revisar_poss3.pack_forget()
        self._btn_limpar.pack_forget()
        self._btn_limpar_ocr.pack_forget()

    def _limpar_ocr(self):
        escaneados = {arq for arq, tipo in self._tipos_pdf.items()
                      if tipo == 'escaneado'}
        if not escaneados:
            return
        self._selecao_raw = [item for item in self._selecao_raw
                              if item['arquivo'] not in escaneados]
        self._rebuild_artigos()
        self._lbl_status3.configure(
            text=f'✓  {len(escaneados)} entrada(s) escaneada(s) removida(s)')

    def _extrair(self):
        prontos = [a for a in self._selecao if a['encontrado']]
        if not prontos:
            return

        secoes_cfg   = self._get_secoes_config()
        fallback_cfg = self._var_fallback.get()
        fragmentar   = self._var_fragmentar.get()
        fmt          = 'md'
        # Nome digitado pelo usuário tem prioridade; sem ele, o padrão é o
        # nome da coleção do Zotero (se a lista veio de lá) ou
        # 'extracao_excerpta'.
        nome_usuario = self._entry_nome_saida.get().strip()
        _salvar_settings({**_ler_settings(), 'nome_base': nome_usuario})
        try:
            frag_kb = max(10, float(self._entry_frag_kb.get().strip().replace(',', '.')))
        except ValueError:
            frag_kb = 500.0

        var_supl   = self._sec_vars.get('suplementar')
        incluir_supl = self._var_tudo.get() or (var_supl.get() if var_supl else False)

        # Toda extração ganha sua própria subpasta em vez de cair solta na
        # pasta escolhida — junto de dezenas de PDFs, um .md a mais (ou vários,
        # no caso fragmentado) se perdia no meio dos artigos originais.
        nome_saida = _nome_arquivo_seguro(
            nome_usuario or self._nome_zotero or 'extracao_excerpta')
        if self._pasta:
            pai = self._pasta
        else:
            pai = filedialog.askdirectory(
                title='Escolher onde salvar a extração',
                initialdir=os.path.expanduser('~'))
            if not pai:
                return

        pasta_saida = os.path.join(pai, nome_saida)
        i = 2
        while os.path.isdir(pasta_saida) and os.listdir(pasta_saida):
            pasta_saida = os.path.join(pai, f'{nome_saida} ({i})')
            i += 1
        os.makedirs(pasta_saida, exist_ok=True)

        if fragmentar:
            path = pasta_saida
        else:
            path = os.path.join(pasta_saida, f'{nome_saida}.{fmt}')

        frag_nome = nome_saida

        self._cancelando = False
        self._btn_extrair.configure(
            text='✕  Cancelar', fg_color=C_ERR, hover_color=C_ERR_HOV,
            command=self._cancelar_extracao)
        self._prog3.set(0)
        self._prog3.pack(side='left', padx=(10, 0))
        usar_ocr = _get_setting('usar_ocr', True)
        usar_ia_local = _get_setting('usar_ia_local', False)
        log_diagnostico = _get_setting('log_diagnostico', True)
        threading.Thread(
            target=self._extrair_worker,
            args=(prontos, path, secoes_cfg, fallback_cfg, incluir_supl,
                  usar_ocr, fragmentar, frag_nome, frag_kb, fmt,
                  dict(getattr(self, '_tipos_pdf', {})), usar_ia_local, log_diagnostico),
            daemon=True).start()

    def _cancelar_extracao(self):
        self._cancelando = True
        self._btn_extrair.configure(state='disabled')
        self._lbl_status3.configure(text='Cancelando…')

    def _extrair_worker(self, artigos, path_saida, secoes_cfg, fallback_cfg,
                        incluir_supl, usar_ocr, fragmentar, frag_nome, frag_kb, fmt='txt',
                        tipos_pdf=None, usar_ia_local=False, log_diagnostico=True):
        """Adapta os callbacks de extracao.executar para a Tk main thread."""

        def _on_status(texto, pct):
            self.after(0, lambda t=texto, p=pct: (
                self._lbl_status3.configure(text=t),
                self._prog3.set(p)
            ))

        def _on_progresso(pct):
            self.after(0, lambda p=pct: self._prog3.set(p))

        def _on_concluido(path, n, stats, log_path):
            self.after(0, lambda p=path, t=n, s=stats, dl=log_path:
                       self._extracao_concluida(p, t, s, dl))

        def _on_concluido_frag(pasta, n, n_frags, kb_frags, stats, log_path):
            self.after(0, lambda p=pasta, t=n, nf=n_frags, kb=kb_frags,
                       s=stats, dl=log_path:
                       self._extracao_concluida_frag(p, t, nf, kb, s, dl))

        def _on_erro_salvar(exc):
            self.after(0, lambda e=exc: messagebox.showerror('Erro ao salvar', str(e)))

        def _on_fim(cancelado, processados, total):
            def _done():
                self._prog3.pack_forget()
                self._btn_extrair.configure(
                    text='↓  Extrair artigos', fg_color=GREEN, hover_color=GREEN_HOV,
                    command=self._extrair, state='normal')
                if cancelado:
                    self._lbl_status3.configure(
                        text=f'Cancelado — {processados} de {total} artigo(s) processado(s)')

            self.after(0, _done)

        extracao.executar(
            artigos, path_saida, secoes_cfg, fallback_cfg, incluir_supl,
            usar_ocr, fragmentar, frag_nome, frag_kb, fmt,
            tipos_pdf, usar_ia_local, log_diagnostico,
            deve_cancelar=lambda: self._cancelando,
            on_status=_on_status,
            on_progresso=_on_progresso,
            on_concluido=_on_concluido,
            on_concluido_frag=_on_concluido_frag,
            on_erro_salvar=_on_erro_salvar,
            on_fim=_on_fim,
        )

    def _mostrar_ajuda_fragmentar(self):
        MODELOS = [
            ('Claude Sonnet / Opus',   2000, '1M tokens de contexto'),
            ('Claude Haiku',           500,  '200K tokens de contexto'),
            ('Gemini 1.5 / 2.0 Flash', 2500, '1M tokens de contexto'),
            ('GPT-4o / GPT-4.1',       300,  '128K tokens de contexto'),
            ('DeepSeek-V3 / R1',       300,  '128K tokens de contexto'),
        ]

        win = ctk.CTkToplevel(self)
        win.title('Tamanho recomendado por modelo')
        win.resizable(False, False)
        win.withdraw()
        win.configure(fg_color=BG_WINDOW)

        # Faixa de cabeçalho, igual à da janela de resumo pós-extração.
        hdr = ctk.CTkFrame(win, fg_color=GREEN_HDR, corner_radius=0, height=62)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text='Tamanho recomendado por modelo',
                     font=_font(14, 'bold'), text_color=GREEN_TXT, anchor='w'
                     ).pack(fill='x', padx=20, pady=(12, 0))
        ctk.CTkLabel(hdr,
                     text='Cada valor deixa margem para o prompt e a resposta da IA.',
                     font=_font(11), text_color=TEXT_SEC, anchor='w'
                     ).pack(fill='x', padx=20)

        corpo = ctk.CTkFrame(win, fg_color='transparent')
        corpo.pack(fill='both', expand=True, padx=16, pady=(12, 0))

        for modelo, kb, ctx in MODELOS:
            # Um card por modelo: nome e contexto empilhados à esquerda, valor
            # e ação à direita. Antes eram quatro rótulos de largura fixa numa
            # linha só, que desalinhavam assim que o número mudava de dígitos.
            card = ctk.CTkFrame(corpo, fg_color=BG_CARD, border_color=DIVIDER,
                                border_width=1, corner_radius=RAIO_CARD)
            card.pack(fill='x', pady=3)
            row = ctk.CTkFrame(card, fg_color='transparent')
            row.pack(fill='x', padx=12, pady=8)

            texto = ctk.CTkFrame(row, fg_color='transparent')
            texto.pack(side='left', fill='x', expand=True)
            ctk.CTkLabel(texto, text=modelo, font=_font(13, 'bold'),
                         text_color=TEXT_PRI, anchor='w').pack(fill='x')
            ctk.CTkLabel(texto, text=ctx, font=_font(11),
                         text_color=TEXT_SEC, anchor='w').pack(fill='x')

            ctk.CTkLabel(row, text=f'{kb} KB', font=_font(13, 'bold'),
                         text_color=ACCENT, width=78, anchor='e'
                         ).pack(side='left', padx=(8, 12))

            def _usar(k=kb):
                self._entry_frag_kb.configure(state='normal')
                self._entry_frag_kb.delete(0, 'end')
                self._entry_frag_kb.insert(0, str(k))
                if not self._var_fragmentar.get():
                    self._var_fragmentar.set(True)
                    self._on_fragmentar_toggle()
                else:
                    self._atualizar_preview_fragmentos()
                win.destroy()

            ctk.CTkButton(row, text='Usar', width=58, height=28,
                         fg_color=GREEN, hover_color=GREEN_HOV,
                         text_color='white', font=_font(12, 'bold'),
                         command=_usar).pack(side='left')

        ctk.CTkButton(win, text='Fechar', width=90, height=30,
                     fg_color=BG_PANEL, hover_color=GRAY_BORD,
                     text_color=TEXT_PRI, font=_font(13),
                     command=win.destroy).pack(pady=(14, 16))

        # Altura calculada: a fixa de 480x270 cortava o conteúdo assim que a
        # lista de modelos crescia.
        win.update_idletasks()
        win.geometry(f'520x{win.winfo_reqheight()}')
        win.deiconify()
        win.lift()
        win.focus_force()
        win.after(50, win.grab_set)

    def _abrir_settings(self):
        SettingsDialog(self)

    def _extracao_concluida(self, path_saida, total, stats, debug_log_path=None):
        self._lbl_status3.configure(text=f'✓  {total} artigo(s) extraídos')
        if _get_setting('mostrar_resumo', True):
            StatsWindow(self, stats, path_saida, fragmentado=False,
                        debug_log_path=debug_log_path)
        elif _get_setting('perguntar_abrir_pasta', True):
            if messagebox.askyesno('Concluído',
                                   f'Extração salva em:\n{path_saida}\n\nAbrir a pasta?'):
                abrir_no_sistema(os.path.dirname(path_saida))

    def _extracao_concluida_frag(self, pasta, total_arts, n_frags, kb_frags, stats,
                                  debug_log_path=None):
        self._lbl_status3.configure(
            text=f'✓  {total_arts} artigo(s) → {n_frags} arquivo(s)')
        if _get_setting('mostrar_resumo', True):
            StatsWindow(self, stats, pasta,
                        fragmentado=True, n_frags=n_frags, kb_frags=kb_frags,
                        debug_log_path=debug_log_path)
        elif _get_setting('perguntar_abrir_pasta', True):
            resumo = '  |  '.join(f'parte {i+1}: ~{kb:.0f} KB'
                                   for i, kb in enumerate(kb_frags))
            if messagebox.askyesno(
                    'Concluído',
                    f'{total_arts} artigo(s) divididos em {n_frags} arquivo(s)\n\n'
                    f'{resumo}\n\n'
                    f'Pasta: {pasta}\n\nAbrir a pasta?'):
                abrir_no_sistema(pasta)


# ══════════════════════════════════════ app ══════════════════════════════════════

class ExcerptaApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        try:
            from tkinterdnd2 import TkinterDnD
            TkinterDnD.require(self)
        except Exception:
            pass
        resolver_fonte()          # antes de construir qualquer widget
        self.title('Excerpta')
        # A lista de artigos (_frame_arts) é rolável — o que não pode faltar
        # é o resto da coluna (cabeçalho, botões, rodapé), por isso o mínimo
        # de altura é bem menor que a altura inicial. Sem isso, em notebooks
        # com tela menor (ex. 1366x768, que sobra ~700px de altura útil
        # depois da barra de tarefas) a janela abria maior que a tela e não
        # dava pra reduzir o bastante para ver o botão "Extrair artigos".
        self.minsize(760, 440)
        # Abre mais estreita e mais baixa por padrão — quem quiser mais
        # espaço aumenta na mão pelo próprio gerenciador de janelas do sistema.
        # 620, não 660: com a lista vazia sobrava ~50px de cinza no rodapé
        # (linha de status/progresso, só usada durante uma extração).
        largura = min(860, self.winfo_screenwidth() - 80)
        altura = min(615, self.winfo_screenheight() - 100)
        self.geometry(f'{largura}x{altura}')
        self.configure(fg_color=BG_WINDOW)

        frame = Etapa3Frame(self, self)
        frame.pack(fill='both', expand=True)
