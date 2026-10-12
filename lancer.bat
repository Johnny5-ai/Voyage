@echo off
rem Double-clic : installe tout au premier lancement (Python requis), puis ouvre le chercheur de vols V2
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where python >nul 2>&1
    if errorlevel 1 (
        echo Python n'est pas installe. Telechargez-le sur https://www.python.org/downloads/
        echo et cochez "Add python.exe to PATH" pendant l'installation, puis relancez ce fichier.
        pause
        exit /b 1
    )
    echo Premier lancement : installation en cours, quelques minutes...
    python -m venv .venv || (pause & exit /b 1)
    .venv\Scripts\python -m pip install -r requirements.txt || (pause & exit /b 1)
)

cd v2
..\.venv\Scripts\python -m streamlit run app.py --server.address localhost --server.port 8502
pause
