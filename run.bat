@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python 3.11 ou plus recent est requis. Installez Python puis relancez ce fichier.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creation de l'environnement Python...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo Impossible de creer l'environnement Python.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Python n'a pas cree .venv correctement.
  pause
  exit /b 1
)

echo Verification des dependances...
".venv\Scripts\python.exe" -c "import flask" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Echec de l'installation des dependances. Verifiez votre connexion Internet.
    pause
    exit /b 1
  )
)

if not defined FLASK_SECRET_KEY (
  if not exist "instance\flask_secret_key" (
    if not exist "instance" mkdir "instance"
    ".venv\Scripts\python.exe" -c "import secrets; print(secrets.token_hex(32))" > "instance\flask_secret_key"
    if errorlevel 1 (
      echo Impossible de generer la cle secrete Flask.
      pause
      exit /b 1
    )
  )
  set /p FLASK_SECRET_KEY=<"instance\flask_secret_key"
)

if not defined FLASK_SECRET_KEY (
  echo Cle secrete Flask introuvable. Definissez FLASK_SECRET_KEY puis relancez.
  pause
  exit /b 1
)
if not defined FLASK_DEBUG set "FLASK_DEBUG=1"

echo Initialisation de la base de donnees...
".venv\Scripts\python.exe" -m flask --app app init-db
if errorlevel 1 (
  echo Echec de l'initialisation de la base de donnees.
  pause
  exit /b 1
)

echo CELINA-STORE demarre sur http://127.0.0.1:5000
echo Arretez le serveur avec Ctrl+C.
".venv\Scripts\python.exe" -m flask --app app run
if errorlevel 1 (
  echo Le serveur n'a pas pu demarrer.
  pause
  exit /b 1
)
