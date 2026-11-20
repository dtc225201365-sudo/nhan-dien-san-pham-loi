@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] Dang tao anh mau kiem tra...
python sample_generator.py
if errorlevel 1 (
    echo Loi tao anh mau. Kiem tra Python va OpenCV.
    pause
    exit /b 1
)
echo [2/3] Mo trinh duyet tai http://127.0.0.1:8050 ...
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8050"
echo [3/3] Dang khoi dong may chu FastAPI...
python -m uvicorn app:app --host 127.0.0.1 --port 8050
