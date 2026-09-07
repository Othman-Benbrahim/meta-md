@echo off
:: META-MD - version locale
:: @author    Laurent Abbal
:: @copyright 2026 Laurent Abbal
:: @link      https://forge.apps.education.fr/meta-md/meta-md
:: @link      https://laurentabbal.forge.apps.education.fr
:: @license   GNU Affero General Public License v3.0 (AGPL-3.0)

setlocal

:: ---------------------------------------------------------------
:: Priorite 1 : runtime Python stable dans lanceur\python\windows
:: Installe via META-MD-win-1-installer.bat
:: ---------------------------------------------------------------
set LOCAL_PY=%~dp0lanceur\python\windows\python.exe
if exist "%LOCAL_PY%" (
    "%LOCAL_PY%" %*
    goto :end
)

:: ---------------------------------------------------------------
:: Fallback : Python dans le PATH systeme
:: ---------------------------------------------------------------
where python >nul 2>&1
if %errorlevel%==0 (
    python %*
    goto :end
)

echo ERREUR : Python introuvable.
echo Lancez META-MD-win-1-installer.bat pour installer le runtime Python
echo ou installez Python 3.11+ et ajoutez-le au PATH.
exit /b 1

:end
endlocal
