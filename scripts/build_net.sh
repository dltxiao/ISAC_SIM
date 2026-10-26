#!/usr/bin/env bash
# 由节点/边 XML 生成 SUMO 路网（幂等）。
set -euo pipefail
cd "$(dirname "$0")/.."
SUMO_BIN=$(./.venv/bin/python - <<'PY'
from isacsim import sumo_env
print(sumo_env.ensure().binary("netconvert"))
PY
)
"$SUMO_BIN" \
  --node-files assets/nets/arterial/nodes.nod.xml \
  --edge-files assets/nets/arterial/edges.edg.xml \
  --output-file assets/nets/arterial/net.net.xml \
  --no-turnarounds true \
  --offset.disable-normalization true \
  --junctions.corner-detail 0 \
  --tls.default-type static \
  --tls.cycle.time 60 \
  --no-warnings true
echo "生成完成：assets/nets/arterial/net.net.xml"
