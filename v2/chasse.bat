@echo off
rem Balayage planifiable (Planificateur de taches Windows) : le journal s'ajoute a data\chasse.log
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
..\.venv\Scripts\python chasse.py >> data\chasse.log 2>&1
