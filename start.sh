#!/bin/sh
# ==========================================
# Dragon Ball RPG Bot — Startup Script
# ==========================================

# Move to the script's directory
cd "$(dirname "$0")" || exit 1

# 1. Check if .env exists
if [ ! -f .env ]; then
    echo "❌ ERROR: No .env file found!"
    echo "Please run: cp .env.example .env"
    echo "Then edit it with: nano .env"
    exit 1
fi

# 2. Load environment variables safely
set -a
. ./.env
set +a

# 3. Check if BOT_TOKEN is set
if [ -z "$BOT_TOKEN" ]; then
    echo "❌ ERROR: BOT_TOKEN is not set in your .env file!"
    exit 1
fi

# 4. Activate the Python 3.11 virtual environment
if [ -d "venv" ]; then
    . venv/bin/activate
else
    echo "⚠️ WARNING: venv not found. Trying to run with system Python."
    echo "If it crashes, run: python3.11 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
fi

# 5. Start the bot
echo "🚀 Starting Dragon Ball RPG Bot..."
python bot.py
