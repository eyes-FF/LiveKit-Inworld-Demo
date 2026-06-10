"""LiveKit Agent Worker

运行(在仓库根目录,读 .env):
  .venv/bin/python agent/main.py download-files   # 首次:预拉 turn-detector 模型
  .venv/bin/python agent/main.py dev              # 开发模式,接 LiveKit Cloud
"""

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit.agents import (
    Agent, AgentSession, AutoSubscribe, ChatContext, ChatMessage,
    JobContext, JobProcess, WorkerOptions, cli,
)
from livekit.plugins import inworld, openai, silero

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# 轮次检测:默认用神经网络模型(效果好,但推理子进程吃 ~400MB 内存);
# 小内存环境(如 Railway Trial 1GB)设 TURN_DETECTION=vad 用静音判停,免推理进程
TURN_DETECTION_MODE = os.getenv("TURN_DETECTION", "model")
if TURN_DETECTION_MODE != "vad":
    from livekit.plugins.turn_detector.multilingual import MultilingualModel


def prewarm(proc: JobProcess):
    # Worker 进程启动时预加载 VAD,避免首通话卡顿(见 HANDOFF 坑 6,不要删)
    proc.userdata["vad"] = silero.VAD.load()


INSTRUCTIONS = {
    "zh-CN": "你是一个友好的中文语音助理。回答口语化、简短。",
    "en-US": "You are a friendly voice assistant. Keep replies conversational and short.",
}
GREETINGS = {
    "zh-CN": "你好,我是 AI 助理,可以开始说话了。",
    "en-US": "Hi, I'm your AI assistant. You can start talking now.",
}
# 未指定音色时按语言选原生默认:Ashley 是英文音色,说中文洋腔洋调;
# Mei 是 Inworld 中文库里的标准普通话女声(list_voices(language='zh') 确认)
DEFAULT_VOICE_BY_LANG = {"zh-CN": "Mei", "en-US": "Ashley"}


class DemoAgent(Agent):
    def __init__(
        self,
        lang: str = "zh-CN",
        inject_shots: bool = True,
        room=None,
        persona: str = "",
    ):
        # 用户自定义人设(persona)优先,否则按语言用默认 instructions
        super().__init__(
            instructions=persona or INSTRUCTIONS.get(lang, INSTRUCTIONS["zh-CN"]),
        )
        self._inject_shots = inject_shots
        self._room = room

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        """★ 核心:用户说完一轮、LLM 生成之前被调用。在这里注入 RAG 上下文。"""
        stats = {
            "enabled": self._inject_shots,
            "shots": 0,
            "knowledge": 0,
            "chars": 0,
            "items": [],  # 注入明细,前端"注入记录"窗口逐条展示
        }
        try:
            if not self._inject_shots:
                return
            user_text = new_message.text_content
            if not user_text:
                return

            ctx = await fetch_context(user_text)
            knowledge = ctx.get("knowledge", [])
            shots = ctx.get("shots", [])

            # 知识片段:作为参考资料注入(RAG)
            if knowledge:
                lines = ["回答时可参考以下资料(与用户问题相关时才使用,不相关则忽略):"]
                lines += [f"- {k['text']}" for k in knowledge]
                content = "\n".join(lines)
                turn_ctx.add_message(role="system", content=content)
                stats["knowledge"] = len(knowledge)
                stats["chars"] += len(content)
                stats["items"] += [
                    {"type": "knowledge", "text": k["text"], "score": k.get("score")}
                    for k in knowledge
                ]

            # 风格示例:打包成单条 system 消息注入。
            # 不用伪造 user/assistant 轮次:若示例 input 与用户实际提问相同/相近,
            # LLM 会误以为已经回答过,导致答非所问(实测踩过)。
            if shots:
                lines = ["回答下一条用户消息时,严格模仿以下示例的语气、风格和长度:"]
                for i, ex in enumerate(shots, 1):
                    lines.append(f"示例{i} 用户:{ex['input']}")
                    lines.append(f"示例{i} 助理:{ex['output']}")
                content = "\n".join(lines)
                turn_ctx.add_message(role="system", content=content)
                stats["shots"] = len(shots)
                stats["chars"] += len(content)
                stats["items"] += [
                    {
                        "type": "shot",
                        "input": ex["input"],
                        "output": ex["output"],
                        "score": ex.get("score"),
                    }
                    for ex in shots
                ]
        finally:
            # 把本轮注入统计推给前端(data channel),不阻塞生成
            if self._room is not None:
                asyncio.create_task(self._publish_stats(stats))

    async def _publish_stats(self, stats: dict):
        try:
            await self._room.local_participant.publish_data(
                json.dumps(stats), reliable=True, topic="context"
            )
        except Exception:
            pass


async def fetch_session_config(room: str) -> dict:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{BACKEND_URL}/api/session-config",
                params={"room": room},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        print(f"[agent] 取会话配置失败,用默认设置: {e}")
    return {}


async def fetch_context(text: str) -> dict:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{BACKEND_URL}/api/context",
                json={"text": text},
                timeout=aiohttp.ClientTimeout(total=0.3),  # 300ms 硬上限,超时降级为无注入
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return {}


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # 会话设置存在 API 侧(长文本不走 token metadata),按房间名取
    cfg = await fetch_session_config(ctx.room.name)
    lang = cfg.get("lang") or "zh-CN"
    persona = (cfg.get("persona") or "").strip()
    inject_shots = bool(cfg.get("shots", True))
    voice = (cfg.get("voice") or "").strip() or DEFAULT_VOICE_BY_LANG.get(lang, "")

    tts_kwargs: dict = {
        "speaking_rate": float(cfg.get("rate") or 1.0),
        "temperature": float(cfg.get("temp") or 1.0),
        # 显式告诉 TTS 目标语言,改善多语音色的发音
        "language": "zh" if lang.startswith("zh") else "en",
    }
    if voice:
        tts_kwargs["voice"] = voice

    session = AgentSession(
        # Inworld STT 默认 en-US,中文必须显式指定,否则误识别成英文
        stt=inworld.STT(language=lang),
        llm=openai.LLM(model="gpt-4.1"),
        tts=inworld.TTS(**tts_kwargs),
        vad=ctx.proc.userdata["vad"],
        turn_detection="vad" if TURN_DETECTION_MODE == "vad" else MultilingualModel(),
        allow_interruptions=True,  # 保持 True,False 有已知 bug(HANDOFF 坑 5)
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

    await session.start(
        agent=DemoAgent(
            lang=lang, inject_shots=inject_shots, room=ctx.room, persona=persona
        ),
        room=ctx.room,
    )
    if persona:
        # 自定义人设:让 LLM 按人设生成开场白,固定问候语会出戏
        session.generate_reply(
            instructions="Greet the user briefly, fully in character."
        )
    else:
        await session.say(GREETINGS.get(lang, GREETINGS["zh-CN"]))


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
