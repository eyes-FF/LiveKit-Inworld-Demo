# LiveKit-Inworld-Demo — 项目级配置

Web 端实时语音 AI demo:Inworld STT/TTS pipeline + LLM 生成前的 RAG 上下文注入。

## 文档地图

- `README.md` — 项目总览、本地开发、部署升级方式
- `HANDOFF.md` — 架构决策(已锁定,勿翻案)+ 全部已知坑(13 条,实现前必读)
- `DEPLOY.md` — Railway + Vercel 部署实录(含 PORT/内存踩坑)

## 锁定的架构决策(不要更改)

- **pipeline(STT→LLM→TTS),不用 RealtimeModel**:注入钩子 `on_user_turn_completed` 只对 pipeline 生效
- **检索本地化**:fastembed(bge-small-zh-v1.5)+ 内存 numpy 索引,不上外部向量库——注入同步阻塞在 LLM 生成前,必须 <50ms(实测 ~3.5ms)
- **few-shot 注入打包成单条 system 消息**,不伪造 user/assistant 轮次(示例与提问相近时 LLM 会以为已答过,实测踩过)
- **`allow_interruptions=True` 不要改**(False 有已知 bug)

## 常用命令

```bash
# 本地三进程(macOS 必须 export SSL_CERT_FILE=$(.venv/bin/python -m certifi))
# 全部代码在 src/ 下
cd src/api && ../../.venv/bin/uvicorn main:app --port 8000   # API
.venv/bin/python src/agent/main.py dev                        # Agent
cd src/frontend && npm run dev                                # 前端(3000 被占会落到 3001)

# 全链路自动化验证(说中文给 agent 听,校验回复与转写)
.venv/bin/python -u src/scripts/talk_probe.py /tmp/probe_say.wav

# 升级线上
git push                              # 后端:Railway 自动构建(~10min)
cd src/frontend && vercel --prod      # 前端:CLI 直推(~30s),不走 GitHub!
```

## 线上环境

- 前端 https://livekit-inworld-demo.vercel.app(Vercel 项目 livekit-inworld-demo,env: NEXT_PUBLIC_BACKEND_URL)
- 后端 https://livekit-inworld-demo-production.up.railway.app(Railway 项目 profound-surprise,单容器 API+Agent,Volume /data)
- Railway 必须显式 `PORT=8000`(否则注入随机 PORT → 502);Trial 1GB 内存需 `TURN_DETECTION=vad`(升 Hobby 后可删恢复神经网络轮次模型)
- 前端域名变更/新增时同步更新 Railway 的 `ALLOWED_ORIGINS`

## 高频坑速查(完整见 HANDOFF.md §8)

- Inworld STT 默认 en-US,中文必须 `language="zh-CN"`;中文默认音色 Mei(Ashley 说中文洋腔)
- 人设(persona→LLM instructions)和音色(voice→Inworld)是两个独立设置,别混;长文本走 `/api/session-config` 不进 token metadata
- 音色描述(英文)→ Inworld Voice Design `design`+`publish` 生成专属 voiceId,每次会在 Inworld workspace 留一个 voice
- 用户转写流的 `lk.transcription_final` 属性不更新,前端用"新条目出现即封口"兜底
- `AudioSource.capture_frame` 不能并发调;远端音轨静音时也持续推帧,判"说完"用能量阈值
- RAG 阈值已按 bge-small-zh 校准(shot 0.46 / knowledge 0.55),调整前先看 retrieval.py 注释

## 验证纪律

改动 agent/api 后用 `src/scripts/talk_probe.py` 跑全链路;改前端用浏览器实测(开始通话 → 开场白字幕 → 设置面板锁定)。生产部署后 curl `/health` + 探针 `BACKEND_URL=<railway域名> src/scripts/probe_client.py`。
