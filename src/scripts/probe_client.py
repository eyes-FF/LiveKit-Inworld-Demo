"""M2 链路探针:作为普通参与者加入房间,验证 agent 被派发并发出开场白音频。

用法: SSL_CERT_FILE=$(.venv/bin/python -m certifi) .venv/bin/python scripts/probe_client.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import rtc

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # 仓库根目录的 .env

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
ROOM = f"probe-{int(time.time())}"  # 每次唯一,避免残留房间里的旧 agent 干扰


async def main() -> int:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{BACKEND_URL}/api/token", params={"room": ROOM, "identity": "probe"}
        ) as resp:
            token = (await resp.json())["token"]

    room = rtc.Room()
    audio_frames = 0
    agent_joined = asyncio.Event()
    got_audio = asyncio.Event()

    @room.on("participant_connected")
    def on_participant(p: rtc.RemoteParticipant):
        print(f"[probe] participant joined: {p.identity} (kind={p.kind})")
        agent_joined.set()

    @room.on("track_subscribed")
    def on_track(track, pub, participant):
        print(f"[probe] subscribed to {track.kind} track from {participant.identity}")
        agent_joined.set()  # 订阅到轨道 = agent 必然在房,兜底 participant_connected
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(drain_audio(track))

    async def drain_audio(track):
        nonlocal audio_frames
        stream = rtc.AudioStream(track)
        async for _ in stream:
            audio_frames += 1
            if audio_frames == 50:  # ~0.5s+ 音频,足以证明 TTS 在播
                got_audio.set()

    await room.connect(LIVEKIT_URL, token)
    print(f"[probe] connected to room '{ROOM}' as 'probe'")

    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=20)
    except asyncio.TimeoutError:
        print("[probe] FAIL: agent 20s 内未加入房间(job 未派发?)")
        return 1

    try:
        await asyncio.wait_for(got_audio.wait(), timeout=30)
        print(f"[probe] OK: 收到 agent 音频 {audio_frames} 帧(开场白 TTS 正常)")
        result = 0
    except asyncio.TimeoutError:
        print(f"[probe] FAIL: agent 加入但 30s 内无音频(TTS 故障?帧数={audio_frames})")
        result = 1

    if result == 0:
        # 注意:track 在语音结束后仍持续推静音帧,不能用"帧停"判断播放结束。
        # 固定等 10s,足够开场白播完 + agent 把转写推给后端。
        await asyncio.sleep(10)
        print(f"[probe] 等待结束,共收到 {audio_frames} 帧", flush=True)

    await room.disconnect()
    return result


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
