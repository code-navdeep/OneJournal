@echo off
title Stop OneJournal

REM Change to script directory
cd /d "%~dp0"

echo Stopping OneJournal...
echo ======================================

REM Stop and remove the container (your data and exports folders are kept)
docker-compose down
if errorlevel 1 (
    echo.
    echo FAILED: OneJournal was not stopped. Is Docker Desktop running?
    echo.
    pause
    exit /b 1
)

echo.
echo OneJournal stopped
echo Port 8090 freed
echo.
pause
