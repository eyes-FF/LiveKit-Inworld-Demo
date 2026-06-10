"use client";

import { useEffect, useRef, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useDataChannel,
  useLocalParticipant,
  useTranscriptions,
  useVoiceAssistant,
} from "@livekit/components-react";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

type Conn = { token: string; url: string };

type Settings = {
  voice: string;
  persona: string;
  rate: number;
  temp: number;
  lang: "zh-CN" | "en-US";
  shots: boolean;
};

// agent 每轮注入后通过 data channel(topic "context")推送的统计与明细
type InjectedItem =
  | { type: "knowledge"; text: string; score?: number }
  | { type: "shot"; input: string; output: string; score?: number };

type CtxStats = {
  enabled: boolean;
  shots: number;
  knowledge: number;
  chars: number;
  items?: InjectedItem[];
};

type InjectionEvent = { ts: number; items: InjectedItem[] };

function fmtTime(t: number): string {
  const d = new Date(t);
  const p = (n: number, l = 2) => String(n).padStart(l, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

const DEFAULT_SETTINGS: Settings = {
  voice: "",
  persona: "",
  rate: 1.0,
  temp: 1.0,
  lang: "zh-CN",
  shots: true,
};

const STATE_TEXT: Record<string, string> = {
  disconnected: "未连接",
  connecting: "连接中",
  initializing: "助理启动中(首次约 20s)…",
  listening: "聆听中",
  thinking: "思考中",
  speaking: "说话中",
};

const PANEL_W = "w-72";

export default function Home() {
  const [conn, setConn] = useState<Conn | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [panelOpen, setPanelOpen] = useState(true);
  const [ctxStats, setCtxStats] = useState<CtxStats | null>(null);
  const [injections, setInjections] = useState<InjectionEvent[]>([]);

  function handleStats(s: CtxStats) {
    setCtxStats(s);
    if (s.items?.length) {
      const items = s.items;
      setInjections((prev) => [...prev, { ts: Date.now(), items }]);
    }
  }

  async function startCall() {
    setConnecting(true);
    setError(null);
    setCtxStats(null);
    setInjections([]);
    try {
      const resp = await fetch(`${BACKEND_URL}/api/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room: `web-${Date.now()}`,
          identity: `user-${Math.random().toString(36).slice(2, 8)}`,
          voice: settings.voice.trim(),
          persona: settings.persona.trim(),
          rate: settings.rate,
          temp: settings.temp,
          lang: settings.lang,
          shots: settings.shots,
        }),
      });
      if (!resp.ok) throw new Error(`token API ${resp.status}`);
      setConn(await resp.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConnecting(false);
    }
  }

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div
        className={`flex min-h-screen flex-col items-center px-6 py-12 transition-[padding] duration-200 ${
          panelOpen ? "lg:pr-72" : ""
        }`}
      >
        <h1 className="text-3xl font-light tracking-wide">语音 AI 助理 Demo</h1>
        <p className="mt-2 text-sm text-neutral-500">
          LiveKit + Inworld · few-shot 实时注入
        </p>

        {error ? (
          <p className="mt-6 rounded border border-red-800 bg-red-950 px-4 py-2 text-sm text-red-300">
            连接失败:{error}
          </p>
        ) : null}

        {!conn ? (
          <button
            onClick={startCall}
            disabled={connecting}
            className="mt-16 flex items-center gap-3 rounded-full bg-neutral-100 px-8 py-4 text-lg font-medium text-neutral-900 transition hover:bg-white disabled:opacity-50"
          >
            <MicIcon className="h-5 w-5" />
            {connecting ? "连接中…" : "开始通话"}
          </button>
        ) : (
          <LiveKitRoom
            serverUrl={conn.url}
            token={conn.token}
            audio
            video={false}
            onDisconnected={() => setConn(null)}
            onMediaDeviceFailure={() =>
              setError("麦克风不可用,请检查浏览器权限")
            }
            className="mt-10 flex w-full max-w-2xl flex-1 flex-col items-center"
          >
            <RoomAudioRenderer />
            <CallPanel
              onEnd={() => setConn(null)}
              onStats={handleStats}
              injections={injections}
            />
          </LiveKitRoom>
        )}
      </div>

      <SettingsSidebar
        open={panelOpen}
        onToggle={() => setPanelOpen((o) => !o)}
        value={settings}
        onChange={setSettings}
        locked={!!conn || connecting}
        inCall={!!conn}
        ctxStats={ctxStats}
      />
    </main>
  );
}

type VoiceOption = { id: string; desc: string };

function SettingsSidebar({
  open,
  onToggle,
  value,
  onChange,
  locked,
  inCall,
  ctxStats,
}: {
  open: boolean;
  onToggle: () => void;
  value: Settings;
  onChange: (s: Settings) => void;
  locked: boolean;
  inCall: boolean;
  ctxStats: CtxStats | null;
}) {
  const [voices, setVoices] = useState<VoiceOption[]>([]);
  const [customVoice, setCustomVoice] = useState(false);

  // 按所选语言拉取 Inworld 内置音色列表
  useEffect(() => {
    const lang = value.lang === "zh-CN" ? "zh" : "en";
    let cancelled = false;
    fetch(`${BACKEND_URL}/api/voices?lang=${lang}`)
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setVoices(d.voices ?? []);
      })
      .catch(() => {
        if (!cancelled) setVoices([]);
      });
    return () => {
      cancelled = true;
    };
  }, [value.lang]);

  function set<K extends keyof Settings>(key: K, v: Settings[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <>
      {!open && (
        <button
          onClick={onToggle}
          title="打开设置"
          className="fixed right-3 top-3 z-20 rounded-lg border border-neutral-800 bg-neutral-900 p-2 text-neutral-400 transition hover:text-neutral-100"
        >
          <PanelRightIcon className="h-4 w-4" />
        </button>
      )}

      <aside
        className={`fixed right-0 top-0 z-20 flex h-screen ${PANEL_W} flex-col border-l border-neutral-800 bg-neutral-900 transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-neutral-800 px-4">
          <span className="text-[13px] font-medium text-neutral-200">设置</span>
          <button
            onClick={onToggle}
            title="收起设置"
            className="rounded p-1 text-neutral-500 transition hover:bg-neutral-800 hover:text-neutral-200"
          >
            <ChevronsRightIcon className="h-4 w-4" />
          </button>
        </header>

        <fieldset
          disabled={locked}
          className="flex-1 overflow-y-auto disabled:opacity-50"
        >
          <Section title="音色">
            <div className="flex flex-col gap-1.5 py-1">
              <select
                value={customVoice ? "__custom__" : value.voice}
                onChange={(e) => {
                  if (e.target.value === "__custom__") {
                    setCustomVoice(true);
                    set("voice", "");
                  } else {
                    setCustomVoice(false);
                    set("voice", e.target.value);
                  }
                }}
                className="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1.5 text-xs text-neutral-100 focus:border-neutral-400 focus:outline-none"
              >
                <option value="">
                  默认({value.lang === "zh-CN" ? "Mei" : "Ashley"})
                </option>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id}
                    {v.desc ? ` — ${v.desc.slice(0, 40)}` : ""}
                  </option>
                ))}
                <option value="__custom__">自定义(voice ID / 英文描述)…</option>
              </select>
              {customVoice ? (
                <textarea
                  value={value.voice}
                  onChange={(e) => set("voice", e.target.value)}
                  rows={4}
                  maxLength={10000}
                  placeholder={
                    "填 voice ID 直接使用;\n或用英文描述声音属性(年龄/性别/口音/语气),开始通话时自动生成专属音色(约需 15s)。最长 10000 字。"
                  }
                  className="w-full resize-y rounded border border-neutral-700 bg-neutral-950 px-2.5 py-2 text-xs leading-relaxed text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-400 focus:outline-none"
                />
              ) : null}
            </div>
            <Row label="语速" detail={`${value.rate.toFixed(2)}×`}>
              <input
                type="range"
                min={0.5}
                max={2}
                step={0.05}
                value={value.rate}
                onChange={(e) => set("rate", Number(e.target.value))}
                className="w-32 accent-neutral-300"
              />
            </Row>
            <Row label="随机性" detail={value.temp.toFixed(1)}>
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={value.temp}
                onChange={(e) => set("temp", Number(e.target.value))}
                className="w-32 accent-neutral-300"
              />
            </Row>
          </Section>

          <Section title="人设">
            <div className="flex flex-col gap-1.5 py-1">
              <textarea
                value={value.persona}
                onChange={(e) => set("persona", e.target.value)}
                rows={7}
                maxLength={10000}
                placeholder={
                  "粘贴 persona / system prompt(最长 10000 字),定义 AI 是谁、怎么说话。\n留空用默认助理人设。"
                }
                className="w-full resize-y rounded border border-neutral-700 bg-neutral-950 px-2.5 py-2 text-xs leading-relaxed text-neutral-100 placeholder:text-neutral-600 focus:border-neutral-400 focus:outline-none"
              />
            </div>
          </Section>

          <Section title="对话">
            <Row label="语言">
              <select
                value={value.lang}
                onChange={(e) => {
                  // 换语言时重置音色:两种语言的内置音色列表不同
                  setCustomVoice(false);
                  onChange({
                    ...value,
                    lang: e.target.value as Settings["lang"],
                    voice: "",
                  });
                }}
                className="w-32 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs text-neutral-100 focus:border-neutral-400 focus:outline-none"
              >
                <option value="zh-CN">中文</option>
                <option value="en-US">English</option>
              </select>
            </Row>
            <Row label="few-shot 注入">
              <input
                type="checkbox"
                checked={value.shots}
                onChange={(e) => set("shots", e.target.checked)}
                className="h-4 w-4 accent-neutral-300"
              />
            </Row>
          </Section>
        </fieldset>

        <Section title="上下文">
          {!inCall ? (
            <p className="py-1 text-xs text-neutral-600">通话中实时显示注入量</p>
          ) : ctxStats === null ? (
            <p className="py-1 text-xs text-neutral-600">等待第一轮对话…</p>
          ) : !ctxStats.enabled ? (
            <p className="py-1 text-xs text-neutral-600">few-shot 注入已关闭</p>
          ) : (
            <>
              <Row label="风格示例">
                <span className="text-xs text-neutral-200">
                  {ctxStats.shots} 条
                </span>
              </Row>
              <Row label="知识片段">
                <span className="text-xs text-neutral-200">
                  {ctxStats.knowledge} 条
                </span>
              </Row>
              <Row label="注入大小">
                <span className="text-xs text-neutral-200">
                  {ctxStats.chars} 字符 · ≈{Math.ceil(ctxStats.chars / 1.5)}{" "}
                  tokens
                </span>
              </Row>
            </>
          )}
        </Section>

        <footer className="shrink-0 border-t border-neutral-800 px-4 py-3">
          <p className="text-[11px] leading-relaxed text-neutral-600">
            {locked
              ? "通话中已锁定,下次通话生效"
              : "设置在下一次通话开始时生效"}
          </p>
        </footer>
      </aside>
    </>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-neutral-800 px-4 py-3">
      <h3 className="mb-1 text-[11px] font-medium uppercase tracking-wider text-neutral-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Row({
  label,
  detail,
  children,
}: {
  label: string;
  detail?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-9 items-center justify-between gap-3">
      <span className="flex items-baseline gap-2 text-xs text-neutral-400">
        {label}
        {detail ? (
          <span className="text-[11px] text-neutral-600">{detail}</span>
        ) : null}
      </span>
      {children}
    </div>
  );
}

function CallPanel({
  onEnd,
  onStats,
  injections,
}: {
  onEnd: () => void;
  onStats: (s: CtxStats) => void;
  injections: InjectionEvent[];
}) {
  const { state } = useVoiceAssistant();

  useDataChannel("context", (msg) => {
    try {
      onStats(JSON.parse(new TextDecoder().decode(msg.payload)));
    } catch {
      // 忽略坏包
    }
  });

  return (
    <>
      <div className="flex items-center gap-2 text-sm text-neutral-400">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            state === "speaking"
              ? "bg-emerald-400"
              : state === "thinking"
                ? "bg-amber-400"
                : state === "listening"
                  ? "bg-sky-400"
                  : "bg-neutral-600"
          }`}
        />
        {STATE_TEXT[state] ?? state}
      </div>

      <Subtitles />
      <InjectionLog events={injections} />

      <button
        onClick={onEnd}
        className="mt-6 flex items-center gap-2 rounded-full border border-neutral-700 px-6 py-3 text-sm text-neutral-300 transition hover:border-red-700 hover:text-red-400"
      >
        <HangUpIcon className="h-4 w-4" />
        结束通话
      </button>
    </>
  );
}

type UtteranceTiming = { start: number; lastChange: number; finalized: boolean };

function Subtitles() {
  const transcriptions = useTranscriptions();
  const { microphoneTrack } = useLocalParticipant();
  const boxRef = useRef<HTMLDivElement>(null);
  // 每条转写的出现/结束时间(本地时钟,出现=首次收到,结束=最后一次内容变化且已 final)
  const timingsRef = useRef(new Map<string, UtteranceTiming & { text: string }>());

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [transcriptions]);

  const micSid = microphoneTrack?.trackSid;
  const now = Date.now();
  for (const t of transcriptions) {
    const id = t.streamInfo.id;
    let tm = timingsRef.current.get(id);
    if (!tm) {
      tm = { start: now, lastChange: now, finalized: false, text: t.text };
      timingsRef.current.set(id, tm);
    } else if (!tm.finalized) {
      if (t.text !== tm.text) {
        tm.text = t.text;
        tm.lastChange = now;
      }
      if (t.streamInfo.attributes?.["lk.transcription_final"] === "true") {
        tm.finalized = true;
      }
    }
  }
  // 兜底:用户转写的 final 属性不更新(stream 打开时定格),
  // 一旦有更新的转写出现,之前的条目视为已结束
  for (let i = 0; i < transcriptions.length - 1; i++) {
    const tm = timingsRef.current.get(transcriptions[i].streamInfo.id);
    if (tm && !tm.finalized) tm.finalized = true;
  }

  // 响应延迟:用户最后一条说完 → AI 第一段开口的间隔(ms)。
  // 只标在每轮回答的第一段上,分段回复的后续段不重复计算。
  const latencyById = new Map<string, number>();
  let pendingUserEnd: number | null = null;
  for (const t of transcriptions) {
    const isUser =
      micSid != null &&
      t.streamInfo.attributes?.["lk.transcribed_track_id"] === micSid;
    const tm = timingsRef.current.get(t.streamInfo.id);
    if (!tm) continue;
    if (isUser) {
      pendingUserEnd = tm.lastChange;
    } else if (pendingUserEnd != null) {
      latencyById.set(t.streamInfo.id, tm.start - pendingUserEnd);
      pendingUserEnd = null;
    }
  }

  return (
    <div
      ref={boxRef}
      className="mt-6 h-80 w-full overflow-y-auto rounded-xl border border-neutral-800 bg-neutral-900 p-4"
    >
      {transcriptions.length === 0 && (
        <p className="text-sm text-neutral-600">字幕将显示在这里…</p>
      )}
      {transcriptions.map((t) => {
        // 用户和 AI 的转写都由 agent 发布,说话人靠 transcribed_track_id 区分
        const isUser =
          micSid != null &&
          t.streamInfo.attributes?.["lk.transcribed_track_id"] === micSid;
        const tm = timingsRef.current.get(t.streamInfo.id);
        const latency = latencyById.get(t.streamInfo.id);
        return (
          <div
            key={t.streamInfo.id}
            className={`mb-3 flex ${isUser ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
                isUser
                  ? "bg-sky-900/60 text-sky-100"
                  : "bg-neutral-800 text-neutral-200"
              }`}
            >
              <span className="mb-0.5 flex items-center gap-2 font-mono text-[10px] text-neutral-500">
                {isUser ? "你" : "AI"}
                {tm
                  ? ` · ${fmtTime(tm.start)} 至 ${fmtTime(tm.lastChange)}${tm.finalized ? "" : "(进行中)"}`
                  : ""}
                {latency != null && latency >= 0 ? (
                  <span
                    className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                      latency < 1500
                        ? "bg-emerald-500/15 text-emerald-300"
                        : latency < 3000
                          ? "bg-amber-500/15 text-amber-300"
                          : "bg-red-500/15 text-red-300"
                    }`}
                  >
                    <TimerIcon className="h-3 w-3" />
                    {Math.round(latency)} ms
                  </span>
                ) : null}
              </span>
              {t.text}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function InjectionLog({ events }: { events: InjectionEvent[] }) {
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [events]);

  return (
    <div className="mt-4 w-full">
      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-neutral-500">
        注入记录
      </h3>
      <div
        ref={boxRef}
        className="h-44 w-full overflow-y-auto rounded-xl border border-neutral-800 bg-neutral-900 p-3"
      >
        {events.length === 0 ? (
          <p className="text-xs text-neutral-600">
            命中检索时,注入的每条内容会显示在这里…
          </p>
        ) : (
          events.map((ev, i) => (
            <div key={`${ev.ts}-${i}`} className="mb-3">
              <p className="mb-1 font-mono text-[10px] text-neutral-500">
                {fmtTime(ev.ts)}
              </p>
              {ev.items.map((item, j) => (
                <div
                  key={j}
                  className="mb-1 flex items-start gap-2 rounded bg-neutral-800/60 px-2 py-1.5"
                >
                  <span
                    className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
                      item.type === "knowledge"
                        ? "bg-emerald-900/60 text-emerald-300"
                        : "bg-sky-900/60 text-sky-300"
                    }`}
                  >
                    {item.type === "knowledge" ? "知识" : "示例"}
                  </span>
                  <span className="text-xs leading-relaxed text-neutral-300">
                    {item.type === "knowledge" ? (
                      item.text
                    ) : (
                      <>
                        <span className="text-neutral-500">用户:</span>
                        {item.input}
                        <span className="ml-2 text-neutral-500">助理:</span>
                        {item.output}
                      </>
                    )}
                    {item.score != null ? (
                      <span className="ml-2 font-mono text-[10px] text-neutral-600">
                        {item.score.toFixed(3)}
                      </span>
                    ) : null}
                  </span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function TimerIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="10" x2="14" y1="2" y2="2" />
      <line x1="12" x2="15" y1="14" y2="11" />
      <circle cx="12" cy="14" r="8" />
    </svg>
  );
}

function MicIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  );
}

function PanelRightIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M15 3v18" />
    </svg>
  );
}

function ChevronsRightIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m6 17 5-5-5-5" />
      <path d="m13 17 5-5-5-5" />
    </svg>
  );
}

function HangUpIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67m-2.67-3.34a19.79 19.79 0 0 1-3.07-8.63A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91" />
      <line x1="2" x2="22" y1="2" y2="22" />
    </svg>
  );
}
