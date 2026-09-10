# Excerpta

Ferramenta desktop para extração seletiva de seções de artigos científicos em PDF.

Selecione uma pasta com PDFs — ou importe direto do Zotero — escolha quais seções quer extrair (abstract, métodos, resultados…) e o Excerpta gera um único arquivo de texto com o conteúdo de todos os artigos.

---

## Requisitos

- **Python 3.9 ou superior**
  - Windows: [python.org/downloads](https://python.org/downloads) — marque *"Add Python to PATH"*
  - Linux/Mac: normalmente já vem instalado

---

## Como iniciar

**Para abrir o Excerpta, em qualquer sistema** — `iniciar.py`:

```bash
python3 iniciar.py
```

No Windows, duplo clique no `iniciar.py` já basta.

O `iniciar.py` também instala o que estiver faltando, então no Linux/Mac ele resolve tudo sozinho na primeira execução (~50 MB). Não é necessário ser administrador.

No Linux, essa primeira execução cria um atalho "Excerpta" no menu do sistema — use-o nas próximas vezes em vez de dar duplo clique no `.py` direto, que faz o gerenciador de arquivos perguntar "Executar ou exibir?" a cada abertura.

---

## Dependências

| Pacote | Tamanho | Finalidade |
|---|---|---|
| `pymupdf4llm` | ~50 MB | Extração de PDFs digitais (rápido) |
| `customtkinter` | pequeno | Interface gráfica |
| `tkinterdnd2` | pequeno | Drag-and-drop de arquivos |
| `docling` *(opcional)* | ~2 GB | OCR para PDFs escaneados |

O `docling` só é necessário para PDFs sem texto nativo (escaneados). Não é instalado pelo launcher — instale quando precisar em **Configurações → Geral**, dentro do próprio Excerpta (botão "Instalar suporte a OCR"). O mesmo vale para a IA local opcional (Ollama), disponível em **Configurações → IA local (Ollama)**.

---

## Integração com o Zotero

Com o Zotero aberto, o Excerpta acompanha a coleção selecionada nele e importa os PDFs com um clique. Para levar só alguns artigos, selecione-os no Zotero e arraste para a lista do Excerpta — o item pai não carrega o arquivo, então o Excerpta reencontra cada artigo na biblioteca por autor, ano e título.

Requer, no Zotero, **Editar → Configurações → Avançado → "Allow other applications on this computer to communicate with Zotero"**. A comunicação é pela API HTTP local (`127.0.0.1:23119`), **somente leitura** — o Excerpta nunca escreve na biblioteca. Não exige nenhuma dependência adicional.

Itens sem PDF anexado, ou com PDF ainda não baixado para a máquina, são relatados ao fim da importação. Quando um item tem mais de um PDF, é importado o anexo principal.

---

## Seções reconhecidas

O Excerpta identifica automaticamente as seções pelo cabeçalho, em português e inglês:

- Abstract / Resumo
- Introdução / Introduction / Background
- Métodos / Methods / Materials and Methods
- Resultados / Results / Findings
- Discussão / Discussion
- Conclusão / Conclusions
- Referências / References / Bibliography
- Material Suplementar / Supplementary Material / Appendix

Cabeçalhos compostos como *"Results and Discussion"* ou *"Discussion and Conclusions"* também são reconhecidos.

Artigos em que uma seção não é encontrada aparecem no resultado como **complementados automaticamente** — o texto restante do artigo é incluído no lugar.

---

## Motores de extração

| Motor | Quando é usado |
|---|---|
| `pymupdf4llm` | PDFs com texto nativo (padrão, rápido) |
| `docling` | PDFs escaneados, quando pymupdf4llm não extrai texto útil |

---

## Arquivos do projeto

Na raiz fica só o que o usuário precisa para instalar e abrir o programa:

| Arquivo | Descrição |
|---|---|
| `iniciar.py` | Abre a aplicação (e instala o que faltar). É a entrada em todos os sistemas |
| `INSTRUCOES.txt` | Instruções para usuários finais |
| `README.md` | Este arquivo |

O código da aplicação fica em `codigo/`:

| Arquivo | Descrição |
|---|---|
| `excerpta.py` | Ponto de entrada (abre a janela) |
| `gui.py` | Interface: janela principal, configurações, resumo pós-extração |
| `extracao.py` | Pipeline de extração e gravação dos arquivos de saída |
| `secoes.py` | Identificação das seções: cabeçalhos, fuzzy, fallbacks, IA local |
| `motor_pdf.py` | Motores de extração e detecção do tipo de PDF |
| `duplicatas.py` | Detecção de duplicatas por hash e por nome truncado |
| `zotero_bridge.py` | Leitura da biblioteca do Zotero pela API HTTP local |
| `config.py` | Preferências e histórico de pastas recentes |
| `log.py` | Log técnico de diagnóstico (`excerpta_debug.log`) |

Arquivos gerados localmente em `codigo/` (não versionados):

| Arquivo | Descrição |
|---|---|
| `.excerpta_settings.json` | Preferências do usuário (`mostrar_resumo`, `perguntar_abrir_pasta`) |
| `.excerpta_recent.json` | Pastas usadas recentemente |
