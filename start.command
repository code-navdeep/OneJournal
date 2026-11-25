#!/bin/bash

# Change to script directory
cd "$(dirname "$0")"

echo "🚀 Starting OneJournal..."
echo "======================================"

# Check that Docker is installed
if ! command -v docker > /dev/null 2>&1; then
    echo "❌ Docker is not installed."
    echo "Install Docker Desktop from https://www.docker.com/products/docker-desktop and try again."
    read -p "Press any key to exit..."
    exit 1
fi

# Check that Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker Desktop is not running!"
    echo "Please start Docker Desktop and try again."
    read -p "Press any key to exit..."
    exit 1
fi

# Check the login settings: OneJournal refuses to start without them
if [ ! -f .env ] || ! grep -Eq '^AUTH_USERNAME=.' .env || ! grep -Eq '^AUTH_PASSWORD=.' .env; then
    echo "❌ The login is not set up."
    echo "Copy .env.example to a file named .env in this folder and fill in"
    echo "AUTH_USERNAME and AUTH_PASSWORD - the name and password your browser"
    echo "will ask for. See README.txt, 'Setting up the login'."
    read -p "Press any key to exit..."
    exit 1
fi

echo "✅ Docker is running"
echo "🔨 Building and starting OneJournal..."
echo ""

if ! docker-compose up -d --build; then
    echo ""
    echo "❌ FAILED: OneJournal did not start. See the messages above."
    read -p "Press any key to exit..."
    exit 1
fi

# Wait for the app to be ready
echo "⏳ Waiting for OneJournal to start..."
sleep 3

# Open the browser
if [[ "$OSTYPE" == "darwin"* ]]; then
    open http://localhost:8090
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    xdg-open http://localhost:8090 2>/dev/null || echo "Please open http://localhost:8090 in your browser"
fi

echo ""
echo "✅ OneJournal is running!"
echo "📍 http://localhost:8090"
echo ""
echo "🛑 To stop: run stop.command"
echo "======================================"
echo ""
read -p "Press any key to exit..."
