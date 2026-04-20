#!/bin/bash
set -e

echo "=== Super Agent Platform - Quick Start ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

# Check Python
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo "Error: Python 3.10+ not found. Please install Python first."
    exit 1
fi

PY_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo "Python: $PY_VERSION"

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "Error: Node.js 18+ not found. Please install Node.js first."
    exit 1
fi
echo "Node.js: $(node --version)"

# Install backend dependencies
echo ""
echo "[1/4] Installing backend dependencies..."
cd "$BACKEND_DIR"
$PYTHON_CMD -m pip install -r requirements.txt -q

# Install frontend dependencies
echo "[2/4] Installing frontend dependencies..."
cd "$FRONTEND_DIR"
npm install --silent 2>/dev/null

# Set API Key
echo "[3/4] Configuring API Key..."
export OPENAI_API_KEY=ms-9c5a766c-cc4e-41f3-be57-920474a5643b
export OPENAI_BASE_URL=https://api-inference.modelscope.cn/v1

# Start backend
echo "[4/4] Starting services..."
cd "$BACKEND_DIR"
$PYTHON_CMD -m uvicorn app.main:app --host 0.0.0.0 --port 8001 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID) on http://localhost:8001"

sleep 3

# Start frontend
cd "$FRONTEND_DIR"
npx next dev --port 3000 &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID) on http://localhost:3000"

echo ""
echo "=== Super Agent Platform is running! ==="
echo "Open http://localhost:3000 in your browser"
echo ""
echo "Press Ctrl+C to stop both services"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
