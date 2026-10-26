#!/usr/bin/env bash
# 启动 Web 控制台（默认 127.0.0.1:8080）
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8080}"
exec ./.venv/bin/python -m isacsim web --host 127.0.0.1 --port "$PORT"
