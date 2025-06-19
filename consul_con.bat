@set /p FERNET_SECRET_KEY=<"E:\k\k"
cd /d "%~dp0"
powershell.exe -NoProfile -Command "python consul.py"
:: pause
