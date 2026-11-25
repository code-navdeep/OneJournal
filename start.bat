@echo off
title OneJournal

REM Change to script directory
cd /d "%~dp0"

echo Starting OneJournal...
echo ======================================

REM Check that Docker is installed
where docker >nul 2>&1
if errorlevel 1 (
    echo Docker is not installed.
    echo Install Docker Desktop from https://www.docker.com/products/docker-desktop and try again.
    pause
    exit /b 1
)

REM Check that Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop is not running!
    echo Please start Docker Desktop and try again.
    pause
    exit /b 1
)

REM Check the login settings: OneJournal refuses to start without them
set LOGIN_OK=1
if not exist ".env" set LOGIN_OK=0
if "%LOGIN_OK%"=="1" (
    findstr /r /c:"^AUTH_USERNAME=." ".env" >nul 2>&1 || set LOGIN_OK=0
    findstr /r /c:"^AUTH_PASSWORD=." ".env" >nul 2>&1 || set LOGIN_OK=0
)
if "%LOGIN_OK%"=="0" (
    echo The login is not set up.
    echo Copy .env.example to a file named .env in this folder and fill in
    echo AUTH_USERNAME and AUTH_PASSWORD - the name and password your browser
    echo will ask for. See README.txt, "Setting up the login".
    pause
    exit /b 1
)

echo Docker is running
echo Building and starting OneJournal...
echo.

docker-compose up -d --build
if errorlevel 1 (
    echo.
    echo FAILED: OneJournal did not start. See the messages above.
    pause
    exit /b 1
)

REM Wait for the app to be ready
echo Waiting for OneJournal to start...
timeout /t 3 /nobreak >nul

REM Open the browser
start http://localhost:8090

echo.
echo OneJournal is running!
echo     http://localhost:8090
echo.
echo To stop: run stop.bat
echo ======================================
echo.
pause
