@echo off
:: META-MD - version locale
:: @author    Laurent Abbal
:: @copyright 2026 Laurent Abbal
:: @link      https://forge.apps.education.fr/meta-md/meta-md
:: @license   GNU Affero General Public License v3.0 (AGPL-3.0)

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set PY_VERSION=3.12.13
set PY_TAG=20260610
set URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PY_TAG%/cpython-%PY_VERSION%+%PY_TAG%-x86_64-pc-windows-msvc-install_only_stripped.tar.gz
set ARCHIVE=lanceur_python_windows_dl.tar.gz
set TARGET=lanceur\python\windows

echo ============================================================
echo  META-MD - Installation Windows (portable et local)
echo  Python %PY_VERSION% + dependances (voir app\requirements.txt)
echo ============================================================
echo.

:: ----- Verifications prerequis -----
where curl >nul 2>&1
if errorlevel 1 (
    echo *** ERREUR : curl introuvable.
    pause
    exit /b 1
)
where tar >nul 2>&1
if errorlevel 1 (
    echo *** ERREUR : tar introuvable. Necessite Windows 10 1803+.
    pause
    exit /b 1
)

:: ----- Etat du dossier %TARGET% -----
:: Considere comme "vide" : dossier inexistant OU contenant uniquement
:: instructions.txt (placeholder du repo). Tout autre contenu = deja installe.
if exist "%TARGET%" (
    set HAS_NON_DOC=0
    for /f "delims=" %%a in ('dir /b "%TARGET%" 2^>nul') do (
        if /i not "%%a"=="instructions.txt" set HAS_NON_DOC=1
    )
    if "!HAS_NON_DOC!"=="1" (
        echo Le dossier %TARGET% contient deja une installation.
        echo Pour reinstaller, supprimez-le : rmdir /s /q %TARGET%
        pause
        endlocal
        exit /b 1
    )
)

:: ----- Installation -----
if not exist "lanceur\python" mkdir "lanceur\python"
echo === 1/3 : telechargement de Python (~30 MB) ===
curl -L --progress-bar -o "%ARCHIVE%" "%URL%"
if errorlevel 1 goto :fail_download

echo.
echo === 2/3 : extraction ===
tar -xzf "%ARCHIVE%"
if errorlevel 1 goto :fail_extract
if not exist "python" goto :fail_structure

:: Preserve l'instructions.txt eventuel avant de remplacer le dossier
if exist "%TARGET%\instructions.txt" (
    move /y "%TARGET%\instructions.txt" "_install_keep.txt" >nul
)
if exist "%TARGET%" rmdir /s /q "%TARGET%"
move python "%TARGET%" >nul
if exist "_install_keep.txt" move /y "_install_keep.txt" "%TARGET%\instructions.txt" >nul
del "%ARCHIVE%"

echo.
echo === 3/3 : installation des dependances Python ===
"%TARGET%\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail_pip
"%TARGET%\python.exe" -m pip install -r app\requirements.txt
if errorlevel 1 goto :fail_pip

echo.
echo ============================================================
echo  TERMINE.
echo  python.bat utilisera desormais %TARGET%.
echo.
echo  Etape suivante : META-MD-win-2-ouvrir.bat
echo  (le serveur ecoute sur http://localhost:8118/)
echo ============================================================
pause
endlocal
exit /b 0

:fail_download
echo *** Echec du telechargement ***
pause
endlocal
exit /b 1

:fail_extract
echo *** Echec de l'extraction (tar) ***
pause
endlocal
exit /b 1

:fail_structure
echo *** L'archive n'a pas cree le dossier 'python' attendu ***
pause
endlocal
exit /b 1

:fail_pip
echo *** Echec de l'installation des paquets ***
pause
endlocal
exit /b 1
