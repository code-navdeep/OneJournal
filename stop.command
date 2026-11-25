#!/bin/bash

# Change to script directory
cd "$(dirname "$0")"

echo "🛑 Stopping OneJournal..."
echo "======================================"

# Stop and remove the container (your data/ and exports/ folders are kept)
if ! docker-compose down; then
    echo ""
    echo "❌ FAILED: OneJournal was not stopped. Is Docker Desktop running?"
    echo ""
    read -p "Press any key to exit..."
    exit 1
fi

echo ""
echo "✅ OneJournal stopped"
echo "🔒 Port 8090 freed"
echo ""
read -p "Press any key to exit..."
