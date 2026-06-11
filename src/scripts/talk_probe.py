"""对话探针:把 /tmp/probe_say.wav 当作麦克风发布,验证 STT→LLM→TTS 完整往返。

策略(应对 agent 冷启动慢):
  1. 入房后持续推静音(模拟真实麦克风)
  2. 等 agent 音频出现"有声"(开场白开始),再等连续 2.5s 静音(开场白结束)
  3. 播放 wav 提问
  4. 双重验证:a) agent 音频再次出现有声(TTS 回复) b) SQLite 出现 user + assistant 转写

预生成语音:
  say -v Tingting "今天天气怎么样" -o /tmp/probe_say.aiff
  afconvert -f WAVE -d LEI16@48000 -c 1 /tmp/probe_say.aiff /tmp/probe_say.wav
运行:
  SSL_CERT_FILE=$(.venv/bin/python -m certifi) .venv/bin/python -u scripts/talk_probe.py
"""

import array
import asyncio
import os
import sqlite3
import sys
import time
import wave
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import rtc

ROOT = Path(__file__).resolve().parents[1]  # src/
load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # 仓库根目录的 .env

LIVEKIT_URL = os.environ["LIVEKIT_URL"]
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
DB_PATH = ROOT / "api" / "utterances.db"
ROOM = f"talk-{int(time.time())}"
WAV = sys.argv[1] if len(sys.argv) > 1 else "/tmp/probe_say.wav"
SAMPLE_RATE = 48000
CHUNK = 480  # 10ms
ENERGY_THRESHOLD = 500  # int16 峰值,大于此视为"有声"


class VoiceWatch:
    """跟踪 agent 音频的有声/静音状态。"""

    def __init__(self):
        self.last_voice_at: float | None = None
        self.first_voice_at: float | None = None

    def feed(self, frame: rtc.AudioFrame):
        samples = array.array("h", frame.data)
        if samples and max(abs(s) for s in samples) > ENERGY_THRESHOLD:
            now = time.time()
            self.last_voice_at = now
            if self.first_voice_at is None:
                self.first_voice_at = now

    async def wait_voice_after(self, t: float, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.last_voice_at and self.last_voice_at > t:
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_silence(self, duration: float, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.last_voice_at and time.time() - self.last_voice_at > duration:
                return True
            await asyncio.sleep(0.1)
        return False


def db_rows(room: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM utterances WHERE room=? ORDER BY id", (room,)
    ).fetchall()
    conn.close()
    return rows


async def main() -> int:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{BACKEND_URL}/api/token", params={"room": ROOM, "identity": "probe"}
        ) as resp:
            token = (await resp.json())["token"]

    room = rtc.Room()
    watch = VoiceWatch()

    @room.on("track_subscribed")
    def on_track(track, pub, participant):
        print(f"[probe] subscribed to agent track ({participant.identity})")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(drain_audio(track))

    async def drain_audio(track):
        async for ev in rtc.AudioStream(track):
            watch.feed(ev.frame)

    await room.connect(LIVEKIT_URL, token)
    print(f"[probe] connected to room '{ROOM}'")

    source = rtc.AudioSource(SAMPLE_RATE, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    # 单一推流循环:pending 有数据推数据,否则推静音(capture_frame 不能并发调用)
    pending = bytearray()
    drained = asyncio.Event()
    drained.set()
    async def feeder():
        silence = b"\x00" * (CHUNK * 2)
        while True:
            if pending:
                chunk = bytes(pending[: CHUNK * 2])
                del pending[: CHUNK * 2]
                if len(chunk) < CHUNK * 2:
                    chunk += b"\x00" * (CHUNK * 2 - len(chunk))
                if not pending:
                    drained.set()
            else:
                chunk = silence
            await source.capture_frame(rtc.AudioFrame(chunk, SAMPLE_RATE, 1, CHUNK))
            await asyncio.sleep(0.005)
    pusher = asyncio.create_task(feeder())
    print("[probe] mic published, waiting for greeting (agent 冷启动可能 ~20s)...")

    if not await watch.wait_voice_after(0, timeout=60):
        print("[probe] FAIL: 60s 内未听到开场白")
        return 1
    print(f"[probe] greeting started, waiting for it to finish...")
    if not await watch.wait_silence(2.5, timeout=30):
        print("[probe] FAIL: 开场白 30s 未结束?")
        return 1

    # 说话
    with wave.open(WAV, "rb") as f:
        assert f.getframerate() == SAMPLE_RATE and f.getnchannels() == 1
        pcm = f.readframes(f.getnframes())
    print(f"[probe] speaking ({len(pcm) // 2 / SAMPLE_RATE:.1f}s): {WAV}")
    drained.clear()
    pending.extend(pcm)
    await drained.wait()
    spoke_at = time.time()
    print("[probe] done speaking, waiting for reply audio...")

    audio_ok = await watch.wait_voice_after(spoke_at, timeout=30)
    print(f"[probe] 回复音频: {'OK' if audio_ok else 'NONE'}")

    # 等转写落库(assistant 回复在播完后才提交,多等一会)
    text_ok = False
    deadline = time.time() + 30
    while time.time() < deadline:
        rows = db_rows(ROOM)
        if any(r == "user" for r, _ in rows) and sum(r == "assistant" for r, _ in rows) >= 2:
            text_ok = True
            break
        await asyncio.sleep(1)

    pusher.cancel()
    await room.disconnect()

    print(f"\n[probe] 房间 {ROOM} 转写:")
    for role, content in db_rows(ROOM):
        print(f"  {role}: {content}")
    ok = audio_ok and text_ok
    print(f"\n[probe] {'PASS' if ok else 'FAIL'} (回复音频={audio_ok}, 转写完整={text_ok})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
