# API(FastAPI) + Agent(LiveKit Worker) 单容器镜像,部署到 Railway 等 Docker 主机
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt api/requirements.txt
COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt -r agent/requirements.txt

COPY api/ api/
COPY agent/ agent/
COPY start.sh ./
RUN chmod +x start.sh

# 模型烘进镜像,避免运行时再从 HuggingFace 下载:
# 1) turn-detector(EOU 模型)  2) fastembed 的 bge-small-zh-v1.5
ENV HF_HOME=/app/.hf-cache
RUN python agent/main.py download-files && \
    python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-zh-v1.5')"

# Railway 注入 PORT;SQLite 建议挂 Volume 后设 DB_PATH=/data/utterances.db
ENV PORT=8000
EXPOSE 8000

CMD ["./start.sh"]
