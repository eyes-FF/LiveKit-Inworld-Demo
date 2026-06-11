# LiveKit + Inworld 语音 AI Demo

Web 端实时语音 AI 助理,核心验证两件事:

1. **Inworld 语音进出**:Inworld STT + TTS 组成的 pipeline 式语音对话
2. **实时上下文注入(RAG)**:LLM 生成回答之前,根据用户刚说的话动态注入知识片段与 few-shot 风格示例,并可在界面上实时观察注入内容与效果

附带:自然打断、人设(persona)定制、音色定制(含描述生成专属音色)、毫秒级延迟观测。

## 在线地址

| 组件 | 地址 |
|---|---|
| 前端 | https://livekit-inworld-demo.vercel.app |
| 后端 API | https://livekit-inworld-demo-production.up.railway.app |

打开前端点「开始通话」即可(首通 agent 冷启动约 10–20s)。

## 功能

- **通话**:WebRTC 实时语音,字幕双向滚动,每条带出现/结束时间戳(精确到毫秒),AI 回复带响应延迟徽章(用户说完 → AI 开口,绿 <1.5s / 琥珀 <3s / 红 ≥3s)
- **RAG 注入**:本地语义检索(bge-small-zh-v1.5 + 内存向量索引,~3.5ms/查询),知识片段 + 风格示例双注入;「注入记录」窗口逐条显示注入内容与相似度分数;右栏实时统计注入量
- **设置面板**(Figma 式右侧属性栏,可收起,通话中锁定,结束后可改下次生效):
  - 对话语言:中文 / English(联动音色列表、指令与开场白语言)
  - 音色:下拉选 Inworld 内置音色(zh 10 个 / en 121 个),或自定义 voice ID / 英文描述(走 Inworld Voice Design 自动生成专属音色,~15s)
  - 语速 / 语气随机性(temperature)
  - 人设:粘贴 persona / system prompt(≤10000 字),覆盖默认助理人设,开场白按人设即兴生成
  - few-shot 注入开关(现场 A/B 对照)

## 架构

```
浏览器 (Next.js 16 + @livekit/components-react)
    │ ① POST /api/token     ② WebRTC 音频
    ▼
LiveKit Cloud
    │ Job 派发 + 音频流
    ▼
Python Agent Worker (livekit-agents 1.5)
    │ inworld.STT → openai.LLM(gpt-4.1) → inworld.TTS
    │ on_user_turn_completed ← ★ RAG 注入点
    │ publish_data(topic="context") → 前端注入统计/明细
    │ ③ HTTP (同容器 localhost)
    ▼
Demo API (FastAPI)
    /api/token           签发 LiveKit token(+音色描述时调 Inworld Voice Design)
    /api/session-config  会话配置(room → 人设/音色/语言…,长文本不走 token)
    /api/context         RAG 检索(知识 + 风格示例,语义 + 关键词混合)
    /api/voices          Inworld 内置音色列表(按语言,带缓存)
    /api/utterance       转写持久化(SQLite)
```

关键决策(详见 [HANDOFF.md](HANDOFF.md)):pipeline 而非原生多模态(注入钩子只对 pipeline 生效);检索用本地 embedding + 内存索引而非外部向量库(注入同步阻塞在生成前,必须 <50ms)。

## 目录结构

```
src/
├── api/        FastAPI(token/检索/转写) + few_shots.json + knowledge.json + retrieval.py
├── agent/      LiveKit Agent Worker(STT/LLM/TTS pipeline + 注入钩子)
├── frontend/   Next.js 前端(通话 UI + 设置面板 + 注入观测)
└── scripts/    自动化语音探针(无需浏览器验证全链路)
Dockerfile      API + Agent 单容器镜像(模型烘进镜像)
start.sh        容器内双进程启动
DEPLOY.md       部署手册(Railway + Vercel,含实录踩坑)
HANDOFF.md      实现指南与全部已知坑
docs/           产品技术方案
```

## 本地开发

```bash
# 0. 准备 .env(参考 .env.example,需 LiveKit Cloud / Inworld / OpenAI 凭据)
python3 -m venv .venv
.venv/bin/pip install -r src/api/requirements.txt -r src/agent/requirements.txt
.venv/bin/python src/agent/main.py download-files   # 首次:预拉 turn-detector 模型

# 1. API(端口 8000;macOS 必须带 SSL_CERT_FILE,否则连不上外部服务)
export SSL_CERT_FILE=$(.venv/bin/python -m certifi)
cd src/api && ../../.venv/bin/uvicorn main:app --port 8000

# 2. Agent Worker(另开终端)
export SSL_CERT_FILE=$(.venv/bin/python -m certifi)
.venv/bin/python src/agent/main.py dev

# 3. 前端(另开终端)
cd src/frontend && npm install && npm run dev
```

### 自动化验证(无需浏览器)

```bash
# 全链路:say 合成中文语音当麦克风 → STT → LLM(含注入) → TTS → 校验转写
say -v Tingting "今天天气怎么样" -o /tmp/probe_say.aiff
afconvert -f WAVE -d LEI16@48000 -c 1 /tmp/probe_say.aiff /tmp/probe_say.wav
SSL_CERT_FILE=$(.venv/bin/python -m certifi) .venv/bin/python -u src/scripts/talk_probe.py /tmp/probe_say.wav
```

## 部署与升级

| 动作 | 方式 | 耗时 |
|---|---|---|
| 改 agent / api | `git push`(Railway 自动构建部署) | ~10 分钟 |
| 改 frontend | `cd src/frontend && vercel --prod`(CLI,不走 GitHub) | ~30 秒 |

完整部署手册与踩坑见 [DEPLOY.md](DEPLOY.md)。

## 维护知识库

- 加知识:编辑 `src/api/knowledge.json`(`{id, text}`),重启 API 自动重建向量索引
- 加风格示例:编辑 `src/api/few_shots.json`(`{keywords, input, output}`),keywords 是检索加分项
