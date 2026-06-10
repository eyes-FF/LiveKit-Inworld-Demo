#!/bin/bash
# 单容器双进程:API(uvicorn) + LiveKit Agent Worker
# 任一进程退出则整体退出,交给平台重启
set -e

PORT="${PORT:-8000}"
export BACKEND_URL="http://localhost:${PORT}"

(cd /app/api && exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT") &

# 等 API 就绪再起 agent(agent 每轮注入依赖 /api/context)
for i in $(seq 1 120); do
  curl -sf "http://localhost:${PORT}/health" > /dev/null 2>&1 && break
  [ "$i" = 120 ] && echo "[start] API 未就绪,退出" && exit 1
  sleep 1
done
echo "[start] API ready on :${PORT}"

(cd /app && exec python agent/main.py start) &

# 任一进程退出,容器退出(平台负责重启)
wait -n
exit 1
