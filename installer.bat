@echo off
echo ===================================
echo  Instalador - Analizador Futbol
echo ===================================
echo.
echo Instalando dependencias...
pip install -r requirements.txt
playwright install chromium
echo.
echo Necesitas dos API keys gratuitas:
echo  1. Groq: https://console.groq.com
echo  2. API-Football: https://www.api-football.com
echo.
set /p GROQ_KEY=Pega tu Groq API Key: 
set /p FOOTBALL_KEY=Pega tu API-Football Key: 
echo GROQ_API_KEY=%GROQ_KEY% > .env
echo API_FOOTBALL_KEY=%FOOTBALL_KEY% >> .env
echo.
echo Todo listo! Iniciando la app...
python interfaz.py
pause