#!/usr/bin/env bash
# 生成数据集并评估：./scripts/gen_dataset.sh [输出目录] [攻击数] [正常数] [时长] [进程数]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-out/ds}"
A="${2:-300}"
N="${3:-300}"
D="${4:-15}"
W="${5:-16}"
./.venv/bin/python -m isacsim batch --out "$OUT" --attacks "$A" --normal "$N" \
  --duration "$D" --workers "$W" --no-streams
./.venv/bin/python -m isacsim eval --runs "$OUT"
