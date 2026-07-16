# Excerpta

Ferramenta desktop para extração seletiva de seções de artigos científicos em PDF.

Selecione uma pasta com PDFs, escolha quais seções quer extrair (abstract, métodos, resultados…) e o Excerpta gera um único arquivo de texto com o conteúdo de todos os artigos.

---

## Requisitos

- **Python 3.9 ou superior**
  - Windows: [python.org/downloads](https://python.org/downloads) — marque *"Add Python to PATH"*
  - Linux/Mac: normalmente já vem instalado

---

## Como iniciar

**Windows** — dê duplo clique em `iniciar.bat`

**Linux/Mac** — execute no terminal:
```bash
python3 iniciar_extrator.py
```

Na primeira execução o programa instala automaticamente as dependências obrigatórias (rápido, ~50 MB) e já abre o Excerpta. Não é necessário ser administrador.

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

| Arquivo | Descrição |
|---|---|
| `excerpta.py` | Aplicação principal (interface e lógica de extração) |
| `iniciar_extrator.py` | Launcher: instala dependências e abre a aplicação |
| `iniciar.bat` | Entrada para Windows (chama o launcher) |
| `INSTRUCOES.txt` | Instruções para usuários finais |

Arquivos gerados localmente (não versionados):

| Arquivo | Descrição |
|---|---|
| `.excerpta_settings.json` | Preferências do usuário (`mostrar_resumo`, `perguntar_abrir_pasta`) |
| `.excerpta_recent.json` | Pastas usadas recentemente |
