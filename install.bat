@echo off
setlocal EnableDelayedExpansion
title Claude Usage Tray - Instalador

set "INSTALL_DIR=%LOCALAPPDATA%\ClaudeUsageTray"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_LINK=%STARTUP_DIR%\ClaudeUsageTray.bat"

echo.
echo ============================================
echo  Claude Usage Tray - Instalador
echo ============================================
echo.

REM 1) Achar o Python
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYW=pythonw"
    set "PY=python"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% NEQ 0 (
        echo [ERRO] Python nao encontrado no PATH.
        echo Instale o Python 3 ou ajuste o PATH e rode de novo.
        pause
        exit /b 1
    )
    set "PYW=python"
    set "PY=python"
)

echo [1/4] Instalando dependencias do Python...
%PY% -m pip install --user --upgrade --quiet requests pillow pystray keyring
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] Falha ao instalar dependencias com pip.
    pause
    exit /b 1
)
echo       OK
echo.

echo [2/4] Copiando arquivos para %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%~dp0claude_usage_tray.py"  "%INSTALL_DIR%\claude_usage_tray.py"  >nul
copy /Y "%~dp0claude_usage_tray.bat" "%INSTALL_DIR%\claude_usage_tray.bat" >nul
echo       OK
echo.

echo [3/4] Adicionando a pasta de inicializacao do Windows...
copy /Y "%INSTALL_DIR%\claude_usage_tray.bat" "%STARTUP_LINK%" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [AVISO] Nao consegui copiar para a Startup. Tente manualmente:
    echo   shell:startup
    echo.
) else (
    echo       OK -^> %STARTUP_LINK%
)
echo.

echo [4/4] Iniciando o widget agora...
REM matar instancia anterior se existir
taskkill /FI "WINDOWTITLE eq claude_usage_tray*" /F >nul 2>nul
start "" %PYW% "%INSTALL_DIR%\claude_usage_tray.py"
echo       OK
echo.

echo ============================================
echo  Pronto! Olhe a bandeja do sistema (perto do
echo  relogio) - deve aparecer um circulo colorido
echo  com a porcentagem de uso da sua sessao.
echo.
echo  O widget vai iniciar automaticamente com o
echo  Windows daqui pra frente.
echo ============================================
echo.
echo Esta janela fecha em 8 segundos...
timeout /t 8 >nul
endlocal
