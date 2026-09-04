@echo off
cd /d "%~dp0"
echo Starting StockLens API on port 8000...
echo.
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
