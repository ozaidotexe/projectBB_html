@echo off
title Bot P2P Hunting Server
:start
echo ===================================================
echo   MENJALANKAN BOT TELEGRAM DAN WEB API (main.py)
echo   Waktu Mulai: %date% %time%
echo ===================================================
echo.

:: Masuk ke folder lokasi script
cd /d "C:\Users\BLUE I.T Computer\OneDrive\Dokumen\L\BACKUP\BugBox Project"

:: Jalankan script Python
python main.py

echo.
echo [WARNING] Script main.py terhenti atau mengalami error!
echo Melakukan restart ulang dalam 5 detik...
echo Tekan CTRL + C di jendela ini jika ingin menghentikan bot secara permanen.
echo ===================================================
timeout /t 5 /nobreak > nul

goto start
