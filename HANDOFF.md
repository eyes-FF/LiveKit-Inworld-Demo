# 项目接手文档:LiveKit + Inworld 语音 AI Web Demo

> 给 Claude Code 的实现指南。目标读者:接手实现的 AI 编程助手。
> 日期:2026-06-10。本文档中的 API 用法已对照 LiveKit Agents 1.5.x 文档核实。

---

## 1. 项目目标

构建一个 **Web 端实时语音 AI 助理 demo**,核心验证两件事:

1. **Inworld 语音进出**:用 Inworld 的 STT + TTS(pipeline 式 S2S 体验)
2. **实时上下文注入**:在 LLM 生成回答之前,根据用户刚说的话动态注入 few-shot 示例,并验证注入确实改变了回答风格

附带要求:支持自然打断(用户开口时 AI 立即停止播放)。

**非目标**(不要做):用户系统、权限、生产级持久化、横向扩缩容、自托管 LiveKit Server。

---

## 2. 架构(4 个组件)

```
浏览器前端 (Next.js + @livekit/components-react)
    │ ① GET /api/token        ② WebRTC 音频
    ▼
LiveKit Cloud(免费档,不自托管)
    │ Job 派发 + 音频流
    ▼
Python Agent Worker (livekit-agents 1.5.x)
    │ inworld.STT → openai.LLM → inworld.TTS
    │ on_user_turn_completed 钩子 ← ★ few-shot 注入点
    │ ③ HTTP
    ▼
Demo API (FastAPI)
    /api/token      签发 LiveKit 房间 token
    /api/shots      按用户文本返回 few-shot 示例(本地 JSON + 关键词匹配)
    /api/utterance  接收转写文本,打印/存 SQLite 即可
```

### 关键架构决策(已定,不要更改)

| 决策 | 选择 | 理由 |
|---|---|---|
| S2S 实现方式 | **拆分 pipeline**(STT→LLM→TTS),不用原生多模态 RealtimeModel | `on_user_turn_completed` 注入钩子只对 pipeline 生效;原生多模态模型(audio→audio)中间没有可拦截的文本轮次。Inworld 的 "S2S" 本身就是 pipeline 式的,正好匹配 |
| 不用 Inworld 自己的 Realtime WebSocket 黑盒 | 在 LiveKit 侧用 `inworld.STT()` / `inworld.TTS()` 插件自己拼 | 用黑盒则 LLM 在 Inworld 侧编排,LiveKit 注入钩子够不着 |
| few-shot 检索 | ~~本地 JSON + 关键词匹配~~ → **2026-06-10 升级为本地语义 RAG**:fastembed(ONNX, bge-small-zh-v1.5) + 内存 numpy 向量索引 + 关键词混合计分,新增 knowledge.json 知识库,`/api/context` 同时返回风格示例与知识片段。**仍不上外部向量数据库** | 注入同步阻塞在 LLM 生成之前,检索必须 <50ms;本地 embedding 实测 ~3.5ms/查询,约束未破。语义检索解决了关键词的同义改写盲区("把钱还给我"→退款示例) |
| LiveKit Server | LiveKit Cloud | 免去自托管 + 自签证书的坑;demo 不需要 |
| 打断 | 框架内置,只配参数 | LiveKit 1.5+ 自带 Adaptive Interruption Handling |

---

## 3. 仓库结构(2026-06-11 起代码统一在 src/ 下)

```
/
├── src/
│   ├── frontend/      # Next.js(通话 UI + 设置面板 + 注入观测)
│   ├── agent/         # Agent Worker(main.py + requirements.txt)
│   ├── api/           # FastAPI(main.py / retrieval.py / few_shots.json / knowledge.json)
│   └── scripts/       # 自动化语音探针
├── Dockerfile         # API+Agent 单容器(容器内仍铺平为 /app/api、/app/agent)
├── start.sh
├── .env.example
├── README.md / DEPLOY.md / CLAUDE.md / docs/
└── HANDOFF.md         # 本文档
```

---

## 4. Agent Worker 核心代码(骨架,API 已核实)

```python
# agent/main.py
import asyncio
import aiohttp
from livekit import agents
from livekit.agents import (
    Agent, AgentSession, ChatContext, ChatMessage,
    JobContext, JobProcess, WorkerOptions, cli, AutoSubscribe,
)
from livekit.plugins import inworld, silero, openai
from livekit.plugins.turn_detector.multilingual import MultilingualModel

BACKEND_URL = "http://localhost:8000"


def prewarm(proc: JobProcess):
    # Worker 进程启动时预加载 VAD,避免首通话卡顿
    proc.userdata["vad"] = silero.VAD.load()


class DemoAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="你是一个友好的中文语音助理。回答口语化、简短。",
        )

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        """★ 核心:用户说完一轮、LLM 生成之前被调用。在这里注入 few-shot。"""
        user_text = new_message.text_content
        if not user_text:
            return

        # 1. 检索 few-shot(必须快,同步阻塞在生成前)
        shots = await fetch_shots(user_text)

        # 2. 注入本轮上下文
        for ex in shots:
            turn_ctx.add_message(role="user", content=ex["input"])
            turn_ctx.add_message(role="assistant", content=ex["output"])
        if shots:
            turn_ctx.add_message(
                role="system",
                content="参考以上示例的风格与格式回答用户接下来的话。",
            )


async def fetch_shots(text: str) -> list[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{BACKEND_URL}/api/shots",
                json={"text": text},
                timeout=aiohttp.ClientTimeout(total=0.3),  # 300ms 硬上限
            ) as resp:
                if resp.status == 200:
                    return (await resp.json())["shots"]
    except Exception:
        pass  # 检索失败不阻塞对话,降级为无注入
    return []


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        stt=inworld.STT(),
        llm=openai.LLM(model="gpt-4.1"),
        tts=inworld.TTS(),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        allow_interruptions=True,
        min_interruption_duration=0.5,
        min_interruption_words=2,
    )

    # 转写持久化:每条消息(用户和 AI)异步推给后端
    @session.on("conversation_item_added")
    def on_item(ev):
        item = ev.item
        if getattr(item, "text_content", None):
            asyncio.create_task(
                push_utterance(ctx.room.name, item.role, item.text_content)
            )

    await session.start(agent=DemoAgent(), room=ctx.room)
    await session.say("你好,我是 AI 助理,可以开始说话了。")


async def push_utterance(room: str, role: str, content: str):
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"{BACKEND_URL}/api/utterance",
                json={"room": room, "role": role, "content": content},
                timeout=aiohttp.ClientTimeout(total=2),
            )
    except Exception:
        pass


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
```

```
# agent/requirements.txt
livekit-agents>=1.5
livekit-plugins-inworld
livekit-plugins-silero
livekit-plugins-openai
livekit-plugins-turn-detector
aiohttp
```

> 注意:插件包名以 PyPI 实际为准,先 `pip index versions livekit-plugins-inworld` 确认。
> turn-detector 模型首次运行需要下载,运行 `python main.py download-files` 预拉。

---

## 5. Demo API(FastAPI)

三个接口:

```python
# api/main.py 要点
# POST /api/shots    {"text": "..."} → {"shots": [{"input":..., "output":...}, ...]}
#   实现:加载 few_shots.json,每条带 keywords 字段,对 text 做关键词命中计分,返回 top 2-3
# GET  /api/token    ?room=xxx&identity=yyy → {"token": "..."}
#   实现:用 livekit-api 包的 AccessToken 签发,grants 至少 room_join + 指定 room
# POST /api/utterance  打印到控制台 + 追加写 SQLite(demo 够用)
```

`few_shots.json` 示例结构:

```json
[
  {
    "keywords": ["退款", "退货", "退钱"],
    "input": "我想退款",
    "output": "好的,我来帮您处理退款。请问订单号是多少呢?"
  }
]
```

---

## 6. 前端要点

- `@livekit/components-react` + `livekit-client`
- 页面流程:点「开始通话」→ fetch `/api/token` → `<LiveKitRoom serverUrl={LIVEKIT_URL} token={token} audio>` → 内放 `<RoomAudioRenderer />`
- 字幕:监听 LiveKit transcription 事件(`RoomEvent.TranscriptionReceived`)直接渲染,不经过后端
- 不需要视频、不需要美化,一个按钮 + 字幕滚动区即可

---

## 7. 环境变量(.env.example)

```bash
# LiveKit Cloud(在 cloud.livekit.io 创建项目获取)
LIVEKIT_URL=wss://xxx.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# Inworld(在 Inworld 控制台获取)
INWORLD_API_KEY=

# LLM
OPENAI_API_KEY=

BACKEND_URL=http://localhost:8000
```

---

## 8. 已知坑(本次调研中确认,实现时注意)

1. **不要换成 RealtimeModel/多模态方案**。有人在 Gemini Live 上动态注入 instructions 导致 LLM 沉默、音频流被重置;pipeline 模式无此问题。这是本项目选 pipeline 的根本原因之一。
2. **preemptive generation(预生成)与注入冲突**。若开启 `preemptive_generation`,在 `on_user_turn_completed` 里改 chat context 会触发警告且生成不会自动重启。**demo 阶段保持预生成关闭(默认)**。
3. **few-shot 检索超时要降级**。代码里已设 300ms 超时 + 失败返回空列表,保持这个模式,检索故障不能让对话卡死。
4. **打断参数需实测调校**。默认 `min_interruption_duration=0.5` 起步;如果 AI 频繁被用户的"嗯/对"打断,提高到 1.0 并把 `min_interruption_words` 提到 3。LiveKit 1.5+ 默认启用 Adaptive Interruption Handling(部署在 LiveKit Cloud 时自动生效),会过滤大部分附和声误触发。
5. **`allow_interruptions=False` 有已知 bug**(某些 STT 在句尾仍触发打断并抛 RuntimeError)。本项目始终保持 `True`,不要踩这个。
6. **VAD 必须 prewarm**。已在代码骨架中,不要删。
7. **转写推送的顺序/幂等**:demo 阶段忽略,但 `/api/utterance` 入库时带上服务器时间戳,方便后续排序。

### 实现过程中新发现的坑(2026-06-10)

8. **macOS Python SSL**:python.org 的 Python 连 LiveKit Cloud 报 `CERTIFICATE_VERIFY_FAILED`,启动 agent 前必须 `export SSL_CERT_FILE=$(.venv/bin/python -m certifi)`。
9. **Inworld STT 默认 en-US**:中文场景必须 `inworld.STT(language="zh-CN")`,否则中文语音被转成英文文本。
10. **few-shot 不能注入成伪 user/assistant 轮次**:若示例 input 与用户实际提问相同/相近,LLM 会误以为已经回答过,答非所问(实测)。正确做法:打包成单条 system 消息("严格模仿以下示例的语气、风格和长度:示例N 用户/助理:...")。原骨架代码已按此修正。
11. **agent 冷启动 ~20s**:dev 模式每个 job 新起进程 + 房间连接重试,客户端入房后要等 agent 的开场白先播完再说话。`scripts/talk_probe.py` 用音频能量检测处理了这个时序。
12. **AudioSource.capture_frame 不能并发调用**:两个 task 同时推帧报 `InvalidState`,要用单一推流循环。
13. **音频轨道无声时也持续推静音帧**:不能用"帧停"判断对方说完,要用能量阈值。
14. **RAG 阈值需按模型校准**(bge-small-zh-v1.5 实测):同义改写相似度 ~0.47-0.55,无关 ~0.42 以下,shot 阈值 0.46 / knowledge 阈值 0.55;知识取 top3 宁多勿漏(注入指令带"不相关则忽略"兜底)。检索器加载失败自动降级关键词匹配(api/main.py lifespan)。首次启动 fastembed 会从 HuggingFace 下载 ~100MB ONNX 模型。

---

## 9. 实现里程碑(建议顺序)

- [x] **M1**:`api/` 三个接口跑通,curl 可测(2026-06-10 完成,shots 检索 ~1.3ms)
- [x] **M2**:`agent/` 接 LiveKit Cloud,跑通基本对话(2026-06-10 完成;用 `scripts/talk_probe.py` 自动验证,无需 Playground)
- [x] **M3**:`inworld.STT()` / `inworld.TTS()` 验证通过(2026-06-10;STT 必须配 `language="zh-CN"`,默认 en-US 会把中文识别成英文)
- [x] **M4**:few-shot 注入验证通过(2026-06-10;天气→极简风"多云，26 度。"/ 退款→客服腔"请问订单号"/ 无关键词→默认闲聊,三组风格差异明显)← **核心验收点 ✅**
- [x] **M5**:自建 `frontend/` 完成(2026-06-10;Next.js 16 + Tailwind v4,`useTranscriptions`/`useVoiceAssistant` hooks 做字幕和状态;浏览器实测连接/字幕/状态流转/挂断全通过。注意:dev 跑在 3001,CORS 已放行 3000/3001;说话人归属用 `streamInfo.attributes["lk.transcribed_track_id"]` 对比本地 mic trackSid,因为双方转写都由 agent 发布;create-next-app 自带的 Google Fonts 在 dev 模式拉取失败,已移除改用系统字体)
- [ ] **M6**:打断实测调参(两人快慢语速各测一轮)

## 10. 验收标准

1. 浏览器点一下即可通话,首句 AI 响应 < 2s
2. AI 说话时用户开口,AI 在 ~0.5s 内停止
3. 命中不同 few-shot 关键词时,AI 回答风格可观察到明显差异
4. 控制台/SQLite 中能看到完整双向转写记录

## 11. 参考链接

- LiveKit Agents 文档:https://docs.livekit.io/agents/
- 轮次与打断:https://docs.livekit.io/agents/build/turns/
- Inworld 插件:https://docs.livekit.io/agents/integrations/ (搜 Inworld)
- Agents Playground:https://agents-playground.livekit.io
