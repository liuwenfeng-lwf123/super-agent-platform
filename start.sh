#!/bin/bash
set -e

echo "=== 天工流 TianGongFlow - Quick Start ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
STARTED_PIDS=()

is_port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

port_owner() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1 " PID " $2}'
}

cleanup() {
  if [ ${#STARTED_PIDS[@]} -gt 0 ]; then
    kill "${STARTED_PIDS[@]}" 2>/dev/null || true
  fi
  exit
}

# Check Python
PYTHON_CMD=""
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo "错误：未找到 Python 3.10+，请先安装 Python。"
    exit 1
fi

PY_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
echo "Python: $PY_VERSION"

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "错误：未找到 Node.js 18+，请先安装 Node.js。"
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

# Load API Key from .env
echo "[3/4] Configuring API Key..."
if [ -f "$BACKEND_DIR/.env" ]; then
  set -a
  source "$BACKEND_DIR/.env"
  set +a
  echo "  Loaded .env from backend/"
else
  echo "  提示：未找到 backend/.env。"
  echo "  你可以复制 backend/.env.example 为 backend/.env 并填写 API Key。"
  echo "  示例：OPENAI_API_KEY=your-key-here"
fi

# Start backend
echo "[4/4] Starting services..."
if is_port_in_use 8001; then
  echo "后端端口 8001 已被占用：$(port_owner 8001)。将复用现有后端，避免重复启动。"
else
  cd "$BACKEND_DIR"
  $PYTHON_CMD -m uvicorn app.main:app --host 0.0.0.0 --port 8001 &
  BACKEND_PID=$!
  STARTED_PIDS+=("$BACKEND_PID")
  echo "后端已启动（PID: $BACKEND_PID）：http://localhost:8001"
fi

sleep 3

# Start frontend
if is_port_in_use 3001; then
  echo "前端端口 3001 已被占用：$(port_owner 3001)。将复用现有前端，避免重复启动。"
else
  cd "$FRONTEND_DIR"
  npx next dev --port 3001 &
  FRONTEND_PID=$!
  STARTED_PIDS+=("$FRONTEND_PID")
  echo "前端已启动（PID: $FRONTEND_PID）：http://localhost:3001"
fi

echo ""
echo "=== 天工流已启动！ ==="
echo "打开浏览器访问 http://localhost:3001"
echo ""
echo "本地模式：在另一个终端运行"
echo "  cd $SCRIPT_DIR && python local_client.py"
echo ""
echo "如果需要重新启动前端，可先释放端口："
echo "  lsof -nP -iTCP:3001 -sTCP:LISTEN"
echo ""
if [ ${#STARTED_PIDS[@]} -eq 0 ]; then
  echo "后端和前端端口都已被占用，本次没有启动新进程。"
  exit 0
fi
echo "按 Ctrl+C 停止本脚本启动的服务"

trap cleanup INT TERM
wait "${STARTED_PIDS[@]}"
