# Voice AI Agent

Outbound voice AI agent that makes a phone call, holds a context-aware
conversation, and produces a structured outcome JSON at call end. Built
for the **Developers Den — Associate AI Engineer** recruitment task.

The primary evaluation focus is **solution architecture** — how
speech, language, and telephony components are connected into a working,
production-minded system — so the codebase is organised around clean
provider abstractions, a small per-call state machine, and end-to-end
streaming over Twilio Media Streams.

---

## Stack

| Layer | Service | Why |
|---|---|---|
| Web framework | **FastAPI** | Required by the task; async-native, perfect for the WebSocket bridge. |
| Telephony | **Twilio** (Programmable Voice + Media Streams) | Industry standard, bidirectional audio over WebSocket. |
| STT | **Deepgram Nova-3** (streaming) | $200 free credit, lowest-latency streaming on the market. |
| LLM | **Groq — `llama-3.3-70b-versatile`** | Free tier, ~300 tok/s — voice needs speed. GPT-4 class reasoning. |
| TTS | **Deepgram Aura-2** (`aura-2-thalia-en`) | Same Deepgram credit, streaming mu-law output, no resampling. |
| Tunneling (dev) | **ngrok** | Public HTTPS+WSS URL so Twilio can reach your laptop. |

Every layer sits behind a `Protocol` in [app/providers/base.py](app/providers/base.py)
and is selected by env variable (`STT_PROVIDER`, `LLM_PROVIDER`,
`TTS_PROVIDER`, `TELEPHONY_PROVIDER`). Swapping vendors is a config
change plus one file — no business-logic changes.

---

## Architecture

```
┌──────────────┐   POST /calls          ┌───────────────────────────────┐
│  Web UI      │ ─────────────────────▶ │  FastAPI App                  │
│  (one HTML)  │                        │                               │
│              │ ◀──── SSE updates ──── │  ┌─────────────────────────┐  │
└──────────────┘                        │  │  REST API               │  │
                                        │  │  /calls /scenarios      │  │
                                        │  └────────┬────────────────┘  │
                                        │           │                   │
                                        │  ┌────────▼────────────────┐  │
                                        │  │  Call Manager           │  │
                                        │  │  (validate, dispatch,   │  │
                                        │  │   create CallSession)   │  │
                                        │  └────────┬────────────────┘  │
                                        │           │                   │
                                        │  ┌────────▼────────────────┐  │
                                        │  │  WS /ws/media/{call_id} │  │
                                        │  │  ◀── Twilio Media Stream│  │
                                        │  │  μ-law 8 kHz audio      │  │
                                        │  │  ┌───────────────────┐  │  │
                                        │  │  │ Conversation Loop │  │  │
                                        │  │  │ STT ⇄ LLM ⇄ TTS   │  │  │
                                        │  │  └───────────────────┘  │  │
                                        │  └─────────────────────────┘  │
                                        │                               │
                                        │  ┌─────────────────────────┐  │
                                        │  │  Outcome Recorder       │  │
                                        │  │  outcomes/<id>.json     │  │
                                        │  └─────────────────────────┘  │
                                        └────────────┬──────────────────┘
                                                     │
   ┌────────────┐   ┌────────────┐  ┌────────────┐  ┌────────────┐
   │  Twilio    │   │ Deepgram   │  │   Groq     │  │ Deepgram   │
   │  Voice +   │   │   STT      │  │   LLM      │  │   TTS      │
   │  Streams   │   │ (Nova-3)   │  │ (Llama 70B)│  │  (Aura-2)  │
   └────────────┘   └────────────┘  └────────────┘  └────────────┘
```

### What's where

```
app/
├── main.py                  FastAPI factory + WS route + lifespan
├── api/                     REST surface
│   ├── calls.py             POST /calls, GET /calls/{id}, SSE stream
│   ├── scenarios.py         GET /scenarios/preset
│   ├── webhooks.py          /twilio/voice/{id}, /twilio/status/{id}
│   ├── schemas.py           Pydantic request/response models
│   └── deps.py              FastAPI dependency providers
├── core/                    cross-cutting infra
│   ├── config.py            Pydantic Settings (env-driven)
│   ├── logging.py           structlog JSON logging with call_id
│   └── validation.py        E.164 phone + prompt sanitization
├── providers/               vendor adapters behind clean Protocols
│   ├── base.py              STT / LLM / TTS / Telephony Protocols
│   ├── stt_deepgram.py      streaming WebSocket STT
│   ├── llm_groq.py          streaming chat w/ tool calling
│   ├── tts_deepgram.py      streaming Aura-2 HTTP
│   ├── telephony_twilio.py  outbound call + TwiML + signature verify
│   └── factory.py           env → concrete provider
├── scenarios/
│   ├── loader.py            ScenarioConfig + YAML + custom builder
│   └── appointment_reminder.yaml
├── conversation/
│   ├── state.py             CallSession + CallStatus state machine
│   ├── engine.py            per-turn LLM orchestration + tool dispatch
│   ├── tools.py             LLM tool definitions (update / end_call)
│   └── outcome.py           post-call structured extraction
├── store/
│   └── sessions.py          in-memory store behind SessionStore Protocol
└── ws/
    └── media_stream.py      Twilio audio ⇄ STT ⇄ LLM ⇄ TTS bridge

web/index.html               single-page UI (vanilla + Tailwind CDN)
tests/                       pytest suite (unit + interface conformance)
outcomes/                    structured JSON written per call (gitignored)
```

---

## Setup

### 1. Sign up (all free, only Twilio needs verification)

| Service | URL | What to grab |
|---|---|---|
| Twilio | https://www.twilio.com/try-twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, a phone number with Voice capability |
| Deepgram | https://console.deepgram.com/signup | `DEEPGRAM_API_KEY` ($200 free, no card) |
| Groq | https://console.groq.com | `GROQ_API_KEY` (free, no card) |
| ngrok | https://dashboard.ngrok.com/signup | Authtoken |

**Twilio trial only allows calls to verified numbers.** After signup,
go to **Phone Numbers → Verified Caller IDs** and verify your own
mobile. The demo will only work when calling a verified number.

### 2. Install

Python 3.11+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and paste the keys you just collected.

### 3. Start ngrok (separate terminal)

```bash
ngrok http 8000
```

Copy the `https://….ngrok-free.app` URL and paste it into `.env` as
`PUBLIC_BASE_URL=…`. Restart the app if it was already running so the
new URL is picked up.

### 4. Run

```bash
make dev          # uvicorn with reload
# or
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000.

### 5. Place a call

1. Enter your **verified** mobile number in E.164 format (e.g. `+14155552671`).
2. Pick **Preset** and fill in patient_name / appointment_time / doctor_name,
   OR pick **Custom prompt** and write your own persona/goal.
3. Click **Start call**. Your phone rings.
4. Pick up and talk. The transcript appears live in the right pane.
5. When the call ends, the structured outcome JSON appears and is also
   saved to `outcomes/<call_id>.json`.

---

## How the conversation stays context-aware

This is a named evaluation criterion, so it gets a dedicated section.

Every LLM call in [app/conversation/engine.py](app/conversation/engine.py)
receives:

1. The full system prompt (persona + goal + context variables + guardrails).
2. The **entire** alternating `user` / `assistant` / `tool` message history
   for this call.
3. The currently-captured `extracted_data` blob, injected back into the
   system prompt so the model never re-asks information it has already
   confirmed.
4. Tool definitions for `update_extracted_data` and `end_call` so the
   model can persist structured info and gracefully terminate.

The model's tool calls are dispatched in-process: extracted updates
flow into the session, and `end_call` triggers a graceful TTS farewell
followed by Twilio teardown.

---

## Edge cases handled

This list is intentionally long because production voice agents fall
apart at the edges. Each item below maps to code paths in
[app/ws/media_stream.py](app/ws/media_stream.py),
[app/conversation/engine.py](app/conversation/engine.py), or
[app/api/calls.py](app/api/calls.py).

**Input**
- Invalid / non-E.164 / empty phone → 422 with a clear message.
- Custom prompt: stripped of control chars, clamped to 4000 chars.
- Preset missing required `context_variables` → 422 listing the missing ones.
- More than `MAX_CALLS_PER_HOUR` outbound calls → 429.

**Call lifecycle**
- No answer / busy / failed → status callback maps to terminal enum.
- **Voicemail detection** via Twilio AMD → agent leaves a spoken voicemail and hangs up.
- Caller hangs up mid-call → partial transcript is still finalised into an outcome.
- Hard duration cap (`CALL_MAX_DURATION_SECONDS`) → graceful farewell.
- Hard turn cap (`MAX_TURNS_PER_CALL`) → graceful farewell.

**Conversation**
- Silence (>6s) → "Are you still there?" then end on second silence.
- Barge-in (caller speaks while agent is talking) → current TTS playback is cancelled.
- Hostile / out-of-scope → guardrails in system prompt + `end_call` tool.
- "Are you a robot?" → honest disclosure baked into the system prompt.

**Provider failures**
- LLM exception during streaming → safe farewell + `end_reason=llm_failure`.
- STT WebSocket error → bridge tears down cleanly; outcome still produced.
- TTS HTTP error → logged; turn proceeds (degraded silence rather than crash).
- Missing API keys at startup → logged warning + listed at `/healthz`; app still boots.

**Concurrency / safety**
- Each call has an isolated `CallSession` and its own STT/TTS WS.
- Session store has TTL eviction (default 1 hour).
- Twilio webhook signature verification is wired (soft-fail in dev, easy to harden in prod).
- CORS limited to localhost in dev.

---

## Configuration reference

See [.env.example](.env.example) for the full list. Highlights:

```
STT_PROVIDER=deepgram         # swap vendors via env, no code change
LLM_PROVIDER=groq
TTS_PROVIDER=deepgram
TELEPHONY_PROVIDER=twilio

GROQ_MODEL=llama-3.3-70b-versatile
DEEPGRAM_STT_MODEL=nova-3
DEEPGRAM_TTS_MODEL=aura-2-thalia-en

MAX_CALLS_PER_HOUR=20         # hourly outbound cap
MAX_TURNS_PER_CALL=12         # conversation turn cap
CALL_MAX_DURATION_SECONDS=300 # hard cutoff

STREAMING_MODE=true           # set false to fall back to <Gather>/<Say> (planned)
ANSWERING_MACHINE_DETECTION=true
```

---

## Tests

```bash
pytest -v
```

Coverage focuses on the highest-value seams:

- `test_validation.py` — phone normalisation + prompt sanitization edges.
- `test_scenario_loader.py` — YAML load, prompt rendering, schema validation.
- `test_conversation_state.py` — state machine, history, terminal transitions.
- `test_engine.py` — LLM turn orchestration with a scripted stub (text-only, tool calls, max turns, LLM failure, silence handling).
- `test_outcome.py` — finalisation + lenient JSON parsing + failure fallback.
- `test_session_store.py` — store contract + TTL eviction.

---

## Design decisions

| Decision | Why |
|---|---|
| **DIY pipeline** (vs Vapi/Retell) | Eval criterion explicitly rewards solution architecture. A managed platform would minimise the surface being evaluated. |
| **One polished scenario** + custom-prompt UI override | Task says "Choose one." The "select or define" UI requirement is met by a textarea, not a runtime registry. |
| **Provider abstraction layer** | This is the genuine answer to "keep the implementation dynamic and flexible" — every vendor lives behind a `Protocol`. Adding a new LLM is one file + one factory branch. |
| **Deepgram for both STT and TTS** | One auth, one connection pattern, less to test. Aura-2 quality is competitive with ElevenLabs and avoids ElevenLabs' tight free-tier char cap. |
| **Groq for LLM** | Free, no card, ~300 tok/s — voice perceives latency aggressively. Llama-3.3-70B is GPT-4 class for reasoning and supports native tool calling. |
| **In-memory `SessionStore`** behind a Protocol | Right size for the eval; Redis/Postgres swap is a one-file change. |
| **SSE for live UI updates** | Simpler than a WS for one-way push and works cleanly through ngrok. |
| **Streaming end-to-end** | Sub-second perceived latency. Turn-based `<Gather>` is a legitimate fallback (env-toggleable concept) but feels robotic. |
| **Structured outcomes via post-call LLM call** | The model that ran the conversation has the cleanest view; one extra cheap call gives us a machine-readable result. |
| **Honest-disclosure guardrail** | "Are you a robot?" is a common edge case; baked into the system prompt because lying isn't an option. |

---

## Known limitations

- In-memory session store: if the FastAPI process restarts, live calls
  lose state. Documented; swap behind `SessionStore` for production.
- ngrok free URLs rotate on restart; update `PUBLIC_BASE_URL` each time.
- Groq free tier has 30 req/min — sufficient for one live call, can throttle
  parallel testing.
- Twilio trial accounts can only call **verified** numbers. There is no
  way around this without upgrading the Twilio account.

---

## Repo structure

```
.
├── app/                  application code
├── web/                  single-page UI
├── tests/                pytest suite
├── docs/                 design spec
├── outcomes/             generated at runtime (gitignored)
├── .env.example
├── requirements.txt
├── Makefile
├── pytest.ini
└── README.md
```
