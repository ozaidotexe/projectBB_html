@echo off
:: Configuration
set DB_USER=root
set DB_PASS=
set DB_NAME=bot_p2p_hunting
set MYSQLDUMP_PATH=C:\xampp\mysql\bin\mysqldump.exe
set BACKUP_DIR=C:\Users\BLUE I.T Computer\OneDrive\Dokumen\L\BACKUP

:: Generate Timestamp (Format: YYYY-MM-DD_HHMMSS)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set TIMESTAMP=%datetime:~0,4%-%datetime:~4,2%-%datetime:~6,2%_%datetime:~8,2%%datetime:~10,2%%datetime:~12,2%

set BACKUP_FILE=%BACKUP_DIR%\%DB_NAME%_%TIMESTAMP%.sql

:: Create backup folder if not exists
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

:: Execute mysqldump
"%MYSQLDUMP_PATH%" -u %DB_USER% %DB_NAME% > "%BACKUP_FILE%"

echo Backup finished: %BACKUP_FILE%
