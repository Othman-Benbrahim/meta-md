@echo off
:: META-MD - version locale
:: @author    Laurent Abbal
:: @copyright 2026 Laurent Abbal
:: @link      https://forge.apps.education.fr/meta-md/meta-md
:: @license   GNU Affero General Public License v3.0 (AGPL-3.0)

setlocal
cd /d "%~dp0"

set PORT=8118
set URL=http://127.0.0.1:%PORT%

:: ----- Le port est-il libre ? -----
:: S'il ne l'est pas, deux cas tres differents : soit c'est notre propre
:: serveur, reste ouvert d'une session precedente, et on le remplace ; soit
:: c'est un programme etranger, et on n'y touche pas. Le depart se fait sur
:: l'executable : seul un python de CE dossier est considere comme le notre.
set BLOCKER_PID=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%PORT% "') do set BLOCKER_PID=%%a

if not defined BLOCKER_PID goto :port_ok

set BLOCKER_EXE=
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%BLOCKER_PID%' -ErrorAction SilentlyContinue).ExecutablePath"`) do set BLOCKER_EXE=%%p

if /i "%BLOCKER_EXE%"=="%~dp0lanceur\python\windows\python.exe" goto :remplacer

echo *** ERREUR : Le port %PORT% est deja utilise par le PID %BLOCKER_PID%. ***
echo.
echo Programme en cours d'ecoute :
echo     %BLOCKER_EXE%
echo.
echo Ce n'est pas le serveur de ce dossier : ce script n'y touche pas.
echo Pour liberer le port :
echo     taskkill /F /PID %BLOCKER_PID%
echo Puis relancez ce script.
pause
exit /b 1

:remplacer
echo Un serveur META-MD tourne deja (PID %BLOCKER_PID%). Fermeture...
taskkill /PID %BLOCKER_PID% /T /F >nul 2>&1
:: Laisser le port retomber avant de le reprendre.
ping -n 2 127.0.0.1 >nul

:port_ok
echo === Demarrage du serveur META-MD ===
echo Interface : %URL%
echo Ctrl+C pour arreter
echo.

:: Ouvre le navigateur apres 2 s (le temps que le server demarre).
start "" /B cmd /c "timeout /t 2 /nobreak >nul && start %URL%"

:: Le lanceur stable choisit la version active et conserve toujours les
:: Les donnees restent dans data\, les versions dans app.
call python.bat lanceur\launch.py

if errorlevel 1 (
    echo.
    echo *** Le serveur s'est arrete avec un code d'erreur. ***
    pause
)

endlocal
