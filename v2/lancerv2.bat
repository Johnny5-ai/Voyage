@echo off
cd /d "%~dp0"
..\.venv\Scripts\python -m streamlit run app.py --server.address localhost --server.port 8502
