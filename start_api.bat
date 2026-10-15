@echo off
cd /d "%~dp0"
set PORT=8008

rem 认证密钥只允许来自环境变量；未设置时生成本次进程专用的随机密钥（仅限本机演示）。
if not defined TEMPERATURE_AUTH_SECRET (
    powershell -NoProfile -Command "$bytes = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes); [Environment]::SetEnvironmentVariable('TEMPERATURE_AUTH_SECRET', ($bytes | ForEach-Object { $_.ToString('x2') }) -join '', 'Process')"
)

python -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
    python -m pip install -r requirements.txt
)
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %ERRORLEVEL%==0 (
    echo Service is already running on http://127.0.0.1:%PORT%
    start "" "http://127.0.0.1:%PORT%/frontend/login.html"
    pause
    exit /b 0
)
python scripts\seed_data.py
start "" "http://127.0.0.1:%PORT%/frontend/login.html"
python -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT% --reload
pause
