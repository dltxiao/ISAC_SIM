#!/usr/bin/env bash
# 验收自检：硬件/预检/标定/小批量评估/导出
set -euo pipefail
cd "$(dirname "$0")/.."
./.venv/bin/python -m isacsim info
./.venv/bin/python -m isacsim preflight
./.venv/bin/python -m isacsim bench --seconds 10
rm -rf out/accept
./.venv/bin/python -m isacsim batch --out out/accept --attacks 24 --normal 24 \
  --duration 12 --workers 8
./.venv/bin/python -m isacsim eval --runs out/accept
RUN=$(ls -d out/accept/*/ | head -1)
./.venv/bin/python -m isacsim export nidd -i "$RUN" -o out/accept/nidd
echo "验收自检完成：out/accept/report.md"
