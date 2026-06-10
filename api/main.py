"""Demo API: /api/token /api/shots /api/context /api/utterance

运行: uvicorn main:app --port 8000 --reload  (在 api/ 目录下)
"""

import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import aiohttp

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from livekit import api as lk_api
from pydantic import BaseModel, Field

# .env 放在仓库根目录,api/ 是子目录
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY", "")
INWORLD_BASE = "https://api.inworld.ai"

# 像音色名/voice ID 的串(Ashley、design voice id 等)直接用;其他文本视为音色描述
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,80}$")

# 部署时用环境变量覆盖:CORS 放行前端域名,SQLite 指到持久卷
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:3001"
    ).split(",")
    if o.strip()
]
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "utterances.db"))
SHOTS_PATH = Path(__file__).resolve().parent / "few_shots.json"
KNOWLEDGE_PATH = Path(__file__).resolve().parent / "knowledge.json"

FEW_SHOTS: list[dict] = []
RETRIEVER = None  # 语义检索器,加载失败时为 None,降级为关键词匹配


def load_shots() -> list[dict]:
    with open(SHOTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS utterances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            room TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global FEW_SHOTS, RETRIEVER
    FEW_SHOTS = load_shots()
    init_db()
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        print("[warn] LIVEKIT_API_KEY/SECRET 未配置,/api/token 将返回 503")
    try:
        from retrieval import Retriever

        RETRIEVER = Retriever(SHOTS_PATH, KNOWLEDGE_PATH)
        print(
            f"[init] 语义检索就绪: shots {len(RETRIEVER.shots)} 条, "
            f"knowledge {len(RETRIEVER.knowledge)} 条"
        )
    except Exception as e:
        print(f"[warn] 语义检索初始化失败,降级为关键词匹配: {e}")
    print(f"[init] few-shot 库: {len(FEW_SHOTS)} 条; SQLite: {DB_PATH}")
    yield


app = FastAPI(title="LiveKit-Inworld Demo API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- /api/token ----------

class TokenRequest(BaseModel):
    room: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    # 音色:音色名/voice ID 直接用;描述性文字走 Inworld Voice Design 生成专属音色
    voice: str = Field("", max_length=1000)
    persona: str = Field("", max_length=8000)  # 人设/instructions,给 LLM
    rate: float = Field(1.0, ge=0.5, le=2.0)
    temp: float = Field(1.0, ge=0.0, le=2.0)
    lang: str = Field("zh-CN", pattern="^(zh-CN|en-US)$")
    shots: bool = True


async def resolve_voice(voice_text: str, lang: str) -> str:
    """音色名/ID 原样返回;描述性文字调 Inworld Voice Design 生成 voiceId。

    生成失败返回空串(降级为默认音色),不阻塞通话。
    """
    v = voice_text.strip()
    if not v or VOICE_ID_RE.match(v):
        return v
    if not INWORLD_API_KEY:
        print("[voice-design] INWORLD_API_KEY 未配置,忽略音色描述")
        return ""

    lang_code = "zh" if lang.startswith("zh") else "en"
    preview_text = (
        "你好,很高兴认识你,今天想聊点什么?"
        if lang_code == "zh"
        else "Hi there, lovely to meet you. What shall we talk about today?"
    )
    headers = {
        "Authorization": f"Basic {INWORLD_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(
                f"{INWORLD_BASE}/voices/v1/voices:design",
                json={
                    "voiceDesignConfig": {"numberOfSamples": 1},
                    "designPrompt": v,
                    "langCode": lang_code,
                    "previewText": preview_text,
                },
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status != 200:
                    print(f"[voice-design] design 失败 {resp.status}: {(await resp.text())[:300]}")
                    return ""
                data = await resp.json()
            previews = data.get("previewVoices") or data.get("voicePreviews") or []
            if not previews:
                print("[voice-design] 无预览返回")
                return ""
            preview_id = previews[0].get("voiceId") or previews[0].get("previewId")

            # preview 是一次性的,publish 成永久 voice 保证整通电话可用
            async with s.post(
                f"{INWORLD_BASE}/voices/v1/voices/{preview_id}:publish",
                json={"voiceId": preview_id, "displayName": f"demo-{int(time.time())}"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp2:
                if resp2.status != 200:
                    print(f"[voice-design] publish 失败 {resp2.status},尝试直接用 preview")
                    return preview_id
                d2 = await resp2.json()
            final_id = (d2.get("voice") or d2).get("voiceId") or preview_id
            print(f"[voice-design] 描述生成音色: {final_id}")
            return final_id
    except Exception as e:
        print(f"[voice-design] 异常,降级默认音色: {e}")
        return ""


def _issue_token(req: TokenRequest) -> dict:
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(503, "LIVEKIT_API_KEY/SECRET 未配置")
    # 会话设置写进参与者 metadata,agent 入房后读取并以此构造 STT/TTS/instructions
    settings = {
        "voice": req.voice.strip(),
        "persona": req.persona.strip(),
        "rate": req.rate,
        "temp": req.temp,
        "lang": req.lang,
        "shots": req.shots,
    }
    token = (
        lk_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(req.identity)
        .with_grants(lk_api.VideoGrants(room_join=True, room=req.room))
        .with_ttl(timedelta(hours=2))
        .with_metadata(json.dumps(settings))
        .to_jwt()
    )
    return {"token": token, "url": LIVEKIT_URL}


@app.post("/api/token")
async def post_token(req: TokenRequest):
    """前端用 POST(人设/音色描述是长文本,不走 URL)。"""
    req.voice = await resolve_voice(req.voice, req.lang)
    return _issue_token(req)


@app.get("/api/token")
async def get_token(
    room: str = Query(..., min_length=1),
    identity: str = Query(..., min_length=1),
):
    """简单 GET 入口,供脚本/调试用(默认设置)。"""
    return _issue_token(TokenRequest(room=room, identity=identity))


# ---------- /api/shots & /api/context ----------

class ShotsRequest(BaseModel):
    text: str


def _keyword_shots(text: str) -> list[dict]:
    """降级路径:纯关键词命中计分。"""
    scored = []
    for entry in FEW_SHOTS:
        score = sum(1 for kw in entry.get("keywords", []) if kw in text)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"input": e["input"], "output": e["output"]} for _, e in scored[:3]]


@app.post("/api/shots")
async def get_shots(req: ShotsRequest):
    """few-shot 风格示例检索(语义优先,降级关键词)。"""
    text = req.text.strip()
    if not text:
        return {"shots": []}
    if RETRIEVER is not None:
        return {"shots": RETRIEVER.search(text)["shots"]}
    return {"shots": _keyword_shots(text)}


@app.post("/api/context")
async def get_context(req: ShotsRequest):
    """RAG 上下文检索:few-shot 风格示例 + 知识片段。"""
    text = req.text.strip()
    if not text:
        return {"shots": [], "knowledge": []}
    if RETRIEVER is not None:
        return RETRIEVER.search(text)
    return {"shots": _keyword_shots(text), "knowledge": []}


# ---------- /api/utterance ----------

class Utterance(BaseModel):
    room: str
    role: str
    content: str


@app.post("/api/utterance")
async def post_utterance(u: Utterance):
    ts = time.time()
    print(f"[utterance] {u.room} | {u.role}: {u.content}")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO utterances (ts, room, role, content) VALUES (?, ?, ?, ?)",
        (ts, u.room, u.role, u.content),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "ts": ts}


@app.get("/health")
async def health():
    return {"ok": True, "shots_loaded": len(FEW_SHOTS)}
