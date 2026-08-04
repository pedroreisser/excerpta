@echo off
setlocal enabledelayedexpansion
title Excerpta - Instalar dependencias
cd /d "%~dp0"

echo.
echo  ================================================
echo  Excerpta - Instalador de dependencias (Windows)
echo  ================================================
echo.
echo  Este arquivo apenas prepara o computador.
echo  Depois dele, o programa e aberto pelo iniciar.py.
echo.
echo  Nao e necessario ser administrador.
echo.

:: ── 1. Procurar Python instalado ─────────────────────────────────────────────
set PYTHON=

:: Tentar "python"
python --version >nul 2>&1
if !errorlevel! equ 0 (
    set PYTHON=python
    goto :PYTHON_ENCONTRADO
)

:: Tentar "py" (launcher do Windows)
py --version >nul 2>&1
if !errorlevel! equ 0 (
    set PYTHON=py
    goto :PYTHON_ENCONTRADO
)

:: Tentar "python3"
python3 --version >nul 2>&1
if !errorlevel! equ 0 (
    set PYTHON=python3
    goto :PYTHON_ENCONTRADO
)

:: Pode existir no disco sem estar no PATH desta sessao
call :PROCURAR_NO_DISCO
if defined PYTHON goto :PYTHON_ENCONTRADO

:: ── 2. Python nao encontrado: instalar via winget (sem admin) ────────────────
echo  Python nao encontrado. Instalando automaticamente...
echo  (O Windows pode pedir confirmacao, mas NAO e necessario admin)
echo.

:: winget esta disponivel no Windows 10/11
winget --version >nul 2>&1
if !errorlevel! equ 0 (
    echo  Instalando Python via winget (usuario atual, sem admin)...
    winget install --id Python.Python.3.12 -e --source winget ^
        --scope user ^
        --accept-package-agreements ^
        --accept-source-agreements ^
        --silent
    if !errorlevel! equ 0 (
        echo  Python instalado com sucesso!
        echo.
        REM O PATH desta sessao ainda nao conhece o Python recem-instalado, e
        REM nao ha como recarrega-lo aqui. Em vez de exigir reinicio, achamos
        REM o executavel direto no disco, onde o winget --scope user coloca.
        call :PROCURAR_NO_DISCO
        if defined PYTHON goto :PYTHON_ENCONTRADO
        echo.
        echo  Python instalado, mas nao localizado nesta sessao.
        echo  Feche esta janela e execute este arquivo novamente.
        pause
        endlocal
        exit /b 0
    )
)

:: winget nao disponivel ou falhou — opcoes manuais
echo.
echo  Nao foi possivel instalar automaticamente.
echo  Escolha uma opcao:
echo.
echo    [1] Abrir Microsoft Store (mais facil, sem admin)
echo    [2] Abrir site python.org para download manual
echo    [3] Sair
echo.
set /p OPCAO=" Digite o numero e pressione Enter: "

if "%OPCAO%"=="1" (
    echo  Abrindo Microsoft Store...
    start ms-windows-store://pdp/?ProductId=9NCVDN91XZQP
    echo  Instale o Python e execute este arquivo novamente.
    pause
    endlocal
    exit /b 0
)
if "%OPCAO%"=="2" (
    echo  Abrindo python.org...
    start https://www.python.org/downloads/
    echo  IMPORTANTE: marque "Add Python to PATH" durante a instalacao.
    echo  Depois execute este arquivo novamente.
    pause
    endlocal
    exit /b 0
)
echo  Saindo.
endlocal
exit /b 0

:: ── 3. Python encontrado: verificar versao minima ────────────────────────────
:PYTHON_ENCONTRADO
:: Quem responde a versao e o proprio Python. Parsear a saida com for /f exigia
:: aspas dentro de aspas, que o cmd quebra quando o caminho tem espaco -- e ele
:: passa a ter, agora que o Python pode vir de %LOCALAPPDATA%.
"!PYTHON!" -c "import sys;print('.'.join(map(str,sys.version_info[:3])),end='')" > "%TEMP%\excerpta_pyver.txt" 2>nul
set PYVER=
if exist "%TEMP%\excerpta_pyver.txt" set /p PYVER=<"%TEMP%\excerpta_pyver.txt"
del "%TEMP%\excerpta_pyver.txt" >nul 2>&1
if defined PYVER echo  Python !PYVER! encontrado.

"!PYTHON!" -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if !errorlevel! neq 0 goto :PYTHON_VELHO
goto :PYTHON_OK

:PYTHON_VELHO
echo.
echo  Versao muito antiga (requer 3.9+).
echo  Instale a versao mais recente em: https://python.org/downloads
pause
endlocal
exit /b 1

:PYTHON_OK

:: ── 4. Atualizar pip silenciosamente ─────────────────────────────────────────
"!PYTHON!" -m pip install --upgrade pip --user --quiet 2>nul

:: ── 5. Instalar as dependencias do Excerpta ──────────────────────────────────
echo.
echo  Instalando as dependencias do Excerpta...
echo.
"!PYTHON!" iniciar.py --apenas-instalar
if !errorlevel! neq 0 (
    echo.
    echo  Nao foi possivel instalar todas as dependencias.
    echo  Tente manualmente no terminal:
    echo    python iniciar.py --apenas-instalar
    echo.
    pause
    endlocal
    exit /b 1
)

echo.
echo  ================================================
echo   Pronto. O computador esta preparado.
echo.
echo   Para abrir o Excerpta, use o arquivo:
echo       iniciar.py
echo.
echo   Este instalador so precisa rodar uma vez.
echo  ================================================
echo.
pause
endlocal
exit /b 0

:: ── Subrotina: procurar o python.exe no disco ────────────────────────────────
:: O winget --scope user instala em %LOCALAPPDATA%\Programs\Python\Python3xx\,
:: que nao entra no PATH da sessao ja aberta. O for /d percorre em ordem, entao
:: sobra a versao mais alta.
:PROCURAR_NO_DISCO
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%d\python.exe" set "PYTHON=%%d\python.exe"
)
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
    set "PYTHON=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
)
goto :eof
