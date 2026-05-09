@echo off
REM Inicia o Claude Usage Tray sem abrir janela de console.
REM Coloque um atalho deste .bat (ou ele mesmo) em:
REM   shell:startup
REM (Win+R -> shell:startup -> Enter) para iniciar com o Windows.

REM Ajuste o caminho do script se necessário
set SCRIPT="%~dp0claude_usage_tray.py"

REM pythonw nao mostra console; se nao existir, cai pro python normal
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" pythonw %SCRIPT%
) else (
    start "" python %SCRIPT%
)
