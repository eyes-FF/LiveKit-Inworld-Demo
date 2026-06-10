# 部署:Vercel(前端) + Railway(API + Agent 单容器)

## 架构

```
浏览器 → Vercel(frontend, Next.js)
            │ NEXT_PUBLIC_BACKEND_URL
            ▼
        Railway(单容器: uvicorn API :$PORT + LiveKit Agent Worker)
            │                                │
            ▼                                ▼
        SQLite(/data, Railway Volume)    LiveKit Cloud ←─ WebRTC ─ 浏览器
```

镜像构建时已把 turn-detector 和 bge-small-zh-v1.5 模型烘进去,冷启动不再下载。

## Railway(API + Agent)

1. 仓库推到 GitHub(eyes-FF 组织),Railway 新建项目 → Deploy from GitHub repo,
   或用 CLI:`railway up`(自动识别根目录 Dockerfile)
2. 环境变量(Service → Variables):
   ```
   LIVEKIT_URL=wss://xxx.livekit.cloud
   LIVEKIT_API_KEY=...
   LIVEKIT_API_SECRET=...
   INWORLD_API_KEY=...
   OPENAI_API_KEY=...
   ALLOWED_ORIGINS=https://<你的vercel域名>.vercel.app
   DB_PATH=/data/utterances.db
   ```
3. 挂 Volume:Service → Volume,Mount Path 填 `/data`(转写持久化)
4. Settings → Networking → Generate Domain,得到公网 URL(下一步要用)
5. 验证:`curl https://<railway域名>/health` 返回 `{"ok":true,...}`;
   Logs 里看到 `registered worker` 说明 agent 已接上 LiveKit Cloud

## Vercel(前端)

1. Root Directory 设为 `frontend/`
2. 环境变量:
   ```
   NEXT_PUBLIC_BACKEND_URL=https://<railway域名>
   ```
3. 部署方式二选一:
   - CLI:`cd frontend && vercel --prod`(eyes-FF 组织未启用 Vercel GitHub App,CLI 最直接)
   - 或 Vercel 控制台 Import 仓库

## 部署后自检

1. 打开 Vercel 域名 → 点「开始通话」→ 听到开场白
2. 说「FF 91 多少钱」→ 注入记录窗口出现知识条目、AI 答 30.9 万美元
3. Railway Logs 能看到 `[utterance]` 转写打印

## 已知注意点

- **冷启动**:容器重启后 agent 重新注册(秒级);首通电话 agent 进程初始化 ~10-20s,前端状态条会显示「助理启动中」
- **单实例即可**:demo 不做横向扩缩容(HANDOFF 非目标);Railway 免费/Hobby 档单实例跑得动(镜像 ~2GB,运行内存 ~1-1.5GB)
- **CORS**:换 Vercel 域名(含 preview 域名)记得同步更新 `ALLOWED_ORIGINS`,多个用逗号分隔
- 本地开发不受影响:`.env` + 三个进程的跑法不变(见 HANDOFF.md)
