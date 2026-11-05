#!/usr/bin/env bash
# 快速重启 Web 控制台
#
# 流程：定位旧进程（pid 文件 + 端口占用，双重来源）→ TERM → 等端口释放
#      → 起新进程 → 探活直到 /api/scenes 返回 200。
#
# 用法：
#   ./sim_start.sh                     # 重启 0.0.0.0:8080（默认）
#   ./sim_start.sh --port 8090         # 换端口重启
#   ./sim_start.sh --host 127.0.0.1 --port 8080
#   ./sim_start.sh --status            # 只看状态，不动进程
#   ./sim_start.sh --timeout 40        # 放宽探活等待（默认 25 s）
#
# 与旧版差异：
#   - 去掉 sleep 5000（原来要等 83 min 才起新进程）；
#   - pid 文件失效/缺失时，按端口反查监听进程，不再“删了旧的起不来新的”；
#   - 杀进程前校验命令行确实是 isacsim web，避免 PID 复用误杀；
#   - TERM 不退自动升级 KILL，并显式等待端口释放，规避 address already in use；
#   - 起完必须探活成功才返回 0，失败时打印 web.log 末尾并以非零退出。

set -uo pipefail
cd "$(dirname "$0")" || exit 1

HOST="0.0.0.0"
PORT=8080
STOP_TIMEOUT=8       # 发完 TERM 最多等这么久
READY_TIMEOUT=25     # 新进程最多等这么久出 200
LOG="out/web.log"
PIDFILE="out/web.pid"
PY=".venv/bin/python"
STATUS_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    HOST="${2:?--host 需要参数}"; shift 2 ;;
    --port)    PORT="${2:?--port 需要参数}"; shift 2 ;;
    --timeout) READY_TIMEOUT="${2:?--timeout 需要参数}"; shift 2 ;;
    --status)  STATUS_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*) echo "--port 必须是数字：$PORT" >&2; exit 2 ;;
esac

now_ms() { date +%s%3N; }
probe_host() {
  case "$HOST" in
    0.0.0.0|::|'') echo "127.0.0.1" ;;
    *)            echo "$HOST" ;;
  esac
}
URL="http://$(probe_host):${PORT}"

# ---------------------------------------------------------------- 进程探测
alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null && ! zombie "$1"; }

zombie() {  # 已退出但未被回收：kill -0 仍然成功，必须单独判断
  local st
  [ -r "/proc/$1/stat" ] || return 1
  st=$(awk '{print $3}' "/proc/$1/stat" 2>/dev/null) || return 1
  [ "$st" = "Z" ]
}

is_isacsim_web() {  # 校验 pid 的命令行，避免 PID 复用误杀
  local pid="$1" cmd
  cmd=$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null ) || return 1
  case "$cmd" in
    *isacsim*web*) return 0 ;;
  esac
  return 1
}

port_open() {  # 能连上就说明还有监听者
  (exec 3<>"/dev/tcp/$(probe_host)/${PORT}") >/dev/null 2>&1
}

port_pids() {  # 按端口反查监听 pid（仅本用户可见的进程）
  local pids=""
  if command -v ss >/dev/null 2>&1; then
    pids=$(ss -ltnp 2>/dev/null \
           | awk -v p=":${PORT}\$" '$4 ~ p' \
           | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)
  fi
  if [ -z "$pids" ] && command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | sort -u)
  fi
  printf '%s\n' "$pids" | sed '/^$/d'
}

http_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 2 -o /dev/null "${URL}/api/scenes" >/dev/null 2>&1
  else
    "$PY" - "${URL}/api/scenes" >/dev/null 2>&1 <<'EOF'
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=2) as r:
    r.read(1)
EOF
  fi
}

owns_port() {  # 命令行里写的就是这个端口
  local cmd
  cmd=$( { tr '\0' ' ' < "/proc/$1/cmdline"; } 2>/dev/null ) || return 1
  case "$cmd" in
    *"--port $PORT"*|*"--port=$PORT"*) return 0 ;;
  esac
  return 1
}

old_pids() {  # pid 文件 + 端口占用，去重；只保留“确实是本端口的 isacsim web”
  local file_pid=""
  [ -f "$PIDFILE" ] && file_pid=$(tr -dc '0-9' < "$PIDFILE")
  {
    [ -n "$file_pid" ] && echo "$file_pid"
    port_pids
  } | sort -u | while read -r pid; do
    [ -n "$pid" ] || continue
    alive "$pid" || continue
    is_isacsim_web "$pid" || continue
    # 端口被它占着，或者命令行里写的就是这个端口（换端口重启时不误杀别的实例）
    if owns_port "$pid" || { port_pids | grep -qx "$pid"; }; then
      echo "$pid"
    fi
  done
}

# ---------------------------------------------------------------- 状态查看
if [ "$STATUS_ONLY" -eq 1 ]; then
  file_pid=$(cat "$PIDFILE" 2>/dev/null || true)
  echo "端口    ：${HOST}:${PORT}"
  if [ -z "$file_pid" ]; then
    echo "pid 文件：${PIDFILE} → (不存在)"
  elif alive "$file_pid" && is_isacsim_web "$file_pid"; then
    echo "pid 文件：${PIDFILE} → ${file_pid}（存活）"
  else
    echo "pid 文件：${PIDFILE} → ${file_pid}（已失效）"
  fi
  owners=$(port_pids | tr '\n' ' ')
  if [ -n "${owners// /}" ]; then
    echo "监听进程：${owners}"
  else
    echo "监听进程：(端口未监听，或属于其他用户)"
  fi
  if http_ok; then
    echo "探活    ：OK  ${URL}"
    exit 0
  fi
  echo "探活    ：失败（未起来，或进程在但接口不应答）"
  exit 1
fi

# ---------------------------------------------------------------- 停旧进程
[ -x "$PY" ] || { echo "找不到解释器：$PY（请在项目根目录运行）" >&2; exit 1; }
mkdir -p "$(dirname "$LOG")" "$(dirname "$PIDFILE")"

START_MS=$(now_ms)
PIDS=$(old_pids | tr '\n' ' ')
PIDS=${PIDS% }

if [ -n "$PIDS" ]; then
  echo "→ 停止旧进程：${PIDS}"
  for pid in $PIDS; do
    kill -TERM "$pid" 2>/dev/null || true
  done

  deadline=$(( $(now_ms) + STOP_TIMEOUT * 1000 ))
  while [ "$(now_ms)" -lt "$deadline" ]; do
    left=""
    for pid in $PIDS; do alive "$pid" && left="$left $pid"; done
    [ -z "${left// /}" ] && break
    sleep 0.1
  done

  for pid in $PIDS; do
    if alive "$pid"; then
      echo "→ ${pid} 未响应 TERM，强制结束"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  sleep 0.2
elif port_open; then
  echo "端口 ${PORT} 被占用，但看不到可识别的 isacsim web 进程。" >&2
  echo "可能属于其他用户；请手动处理（例如 sudo ss -ltnp | grep :${PORT}）后重试。" >&2
  exit 1
fi

# 等端口真正释放：TERM 之后内核回收 socket 可能有毫秒级延迟
deadline=$(( $(now_ms) + 3000 ))
while port_open && [ "$(now_ms)" -lt "$deadline" ]; do sleep 0.05; done
if port_open; then
  echo "端口 ${PORT} 仍未释放，放弃启动。" >&2
  exit 1
fi

# ---------------------------------------------------------------- 起新进程
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
  mv -f "$LOG" "${LOG}.1"
fi
printf '\n===== %s  restart %s:%s =====\n' "$(date '+%F %T')" "$HOST" "$PORT" >> "$LOG"
LOG_OFFSET=$(( $(stat -c %s "$LOG" 2>/dev/null || echo 0) + 1 ))

nohup "$PY" -m isacsim web --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"

deadline=$(( $(now_ms) + READY_TIMEOUT * 1000 ))
ready=0
while [ "$(now_ms)" -lt "$deadline" ]; do
  if ! alive "$NEW_PID"; then
    break
  fi
  if http_ok; then
    ready=1
    break
  fi
  sleep 0.15
done

ELAPSED=$(( $(now_ms) - START_MS ))

if [ "$ready" -ne 1 ]; then
  echo "✗ 启动失败（pid ${NEW_PID}，用时 ${ELAPSED} ms）。本次输出：${LOG}" >&2
  tail -c +"${LOG_OFFSET}" "$LOG" 2>/dev/null | tail -n 30 >&2
  [ -f "$PIDFILE" ] && rm -f "$PIDFILE"
  exit 1
fi

echo "✓ Web 已就绪 ${URL}  (pid ${NEW_PID}，用时 ${ELAPSED} ms，日志 ${LOG})"
