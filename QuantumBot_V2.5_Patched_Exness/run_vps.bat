@echo off
title QuantumBot Pro V2.5 - Exness 24/7 Watchdog
color 0A

echo ===================================================
echo     QUANTUM BOT PRO V2.5 - VPS EXNESS WATCHDOG
echo ===================================================
echo Memastikan dependensi terinstal...
python -m pip install -r requirements.txt

:restart_loop
cls
echo [%date% %time%] Memulai QuantumBot Pro V2.5...
python main.py
echo [%date% %time%] [PERINGATAN] Bot terhenti dengan exit code %errorlevel%!
echo Merestart bot secara otomatis dalam 5 detik...
timeout /t 5 /nobreak
goto restart_loop
