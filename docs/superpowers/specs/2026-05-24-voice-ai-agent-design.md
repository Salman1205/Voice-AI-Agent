# Voice AI Agent — Design Spec

**Date:** 2026-05-24
**Author:** Salman
**Status:** Approved for implementation
**Recruitment task:** Associate AI Engineer — Developers Den
**Deadline:** 2026-05-24 23:58

---

## 1. Objective

Design and implement a voice AI agent capable of making outbound phone calls for a specific business scenario. The system must demonstrate **solution architecture quality** — how speech, language, and telephony components are connected into a working, production-minded service — and must remain **dynamic and flexible** so the task can be extended in a follow-up evaluation round without rewrites.

## 2. Scope

### In scope
- FastAPI backend that orchestrates STT, LLM, and TTS providers.
- Outbound calling via Twilio Programmable Voice + Media Streams (bidirectional audio).
- Single-page web UI to (a) use the preset scenario, or (b) provide a custom prompt override (persona / goal / opening line), then trigger a call.
- **One** scenario implemented and polished end-to-end: **Appointment Reminder & Confirmation**, loaded from a single YAML config file.
- Provider abstraction for STT, LLM, TTS, and telephony so any vendor can be swapped via env configuration. This is how the "dynamic and flexible" hint is genuinely answered.
- Context-aware conversation with full structured turn history fed to the LLM each turn.
- Structured outcome capture at call end (JSON: status, transcript, extracted entities, next action).
- Comprehensive edge-case handling (see §8).
- Setup README with architecture overview and design decisions.

### Out of scope (explicit YAGNI)
- Multiple preset scenarios. Task explicitly says "Choose one."
- Runtime scenario registration API (`POST /scenarios`). The "define" half of "select or define" is satisfied by a textarea in the UI that overrides the preset's prompt for that one call — no registry needed.
- Inbound calls.
- Persistent database (in-memory store is sufficient for the eval; clearly documented as swappable behind a `SessionStore` interface).
- Authentication / multi-tenant support.
- Production deployment (local + ngrok demo is the target).
- Mobile UI.

## 3. Architecture Overview

```
┌──────────────┐   POST /calls            ┌───────────────────────────────┐
│  Web UI      │ ───────────────────────▶ │  FastAPI App                  │
│  (one HTML)  │                          │                               │
│              │ ◀──── SSE call status ── │  ┌─────────────────────────┐  │
└──────────────┘                          │  │  REST API               │  │
                                          │  │  /calls /scenarios      │  │
                                          │  └────────┬────────────────┘  │
                                          │           │                   │
                                          │  ┌────────▼────────────────┐  │
                                          │  │  Call Manager           │  │
                                          │  │  - validates input      │  │
                                          │  │  - loads scenario       │  │
                                          │  │  - creates session      │  │
                                          │  │  - invokes Twilio REST  │  │
                                          │  └────────┬────────────────┘  │
                                          │           │                   │
                                          │  ┌────────▼────────────────┐  │
                                          │  │  Scenario Engine        │  │
                                          │  │  YAML loader + API      │  │
                                          │  │  scenario registry      │  │
                                          │  └─────────────────────────┘  │
                                          │                               │
                                          │  ┌─────────────────────────┐  │
                                          │  │  WS /ws/media/{sid}     │  │
                                          │  │  ◀── Twilio Media Stream│  │
                                          │  │  μ-law 8kHz audio       │  │
                                          │  │  ┌───────────────────┐  │  │
                                          │  │  │ Conversation Loop │  │  │
                                          │  │  │ STT ⇄ LLM ⇄ TTS   │  │  │
                                          │  │  └───────────────────┘  │  │
                                          │  └─────────────────────────┘  │
                                          │                               │
                                          │  ┌─────────────────────────┐  │
                                          │  │  Outcome Recorder       │  │
                                          │  │  JSON per call          │  │
                                          │  └─────────────────────────┘  │
                                          └───────────────┬───────────────┘
                                                          │
            ┌─────────────────────────────────────────────┼──────────────────────────────────────┐
            │                                             │                                       │
   ┌────────▼────────┐     ┌──────────────────┐  ┌────────▼────────┐    ┌────────────────┐
   │  Twilio         │     │  Deepgram STT    │  │  Groq LLM       │    │ Deepgram TTS    │
   │  Voice +        │     │  Nova-3 streaming│  │  Llama-3.3-70B  │    │ Aura-2 streaming│
   │  Media Streams  │     │  WebSocket       │  │  HTTP / stream  │    │ WebSocket       │
   └─────────────────┘     └──────────────────┘  └─────────────────┘    └─────────────────┘
```

## 4. Components

### 4.1 Web UI (`web/index.html`)

Single HTML page, vanilla JS + Tailwind (CDN). No build step.

Sections:
- **Phone number input** — E.164 format with client-side regex validation and country-code hint.
- **Scenario mode** — radio toggle:
  - *Preset*: uses the bundled Appointment Reminder config + a small form for `patient_name`, `appointment_time`, `doctor_name`.
  - *Custom*: a single textarea where the user types the persona/goal/opening-line prompt. Submitted as-is for the one call; nothing persisted server-side.
- **Start Call** button — disabled until inputs are valid; shows spinner during dispatch.
- **Live status panel** — Server-Sent Events stream of: call state (`queued`, `ringing`, `in_progress`, `completed`, `failed`), live transcript (speaker-labeled), and final outcome JSON.

### 4.2 FastAPI Backend (`app/`)

| Module | Responsibility |
|---|---|
| `main.py` | App factory, middleware, static mount for `web/`, lifespan startup/shutdown |
| `api/calls.py` | `POST /calls`, `GET /calls/{id}`, `GET /calls/{id}/stream` (SSE) |
| `api/scenarios.py` | `GET /scenarios/preset` — returns the one bundled scenario config (for the UI preset form) |
| `api/webhooks.py` | Twilio status callbacks (`/twilio/status`), TwiML response (`/twilio/voice`) |
| `core/config.py` | Pydantic Settings (env-driven) |
| `core/logging.py` | Structured JSON logging with `call_id` correlation |
| `core/validation.py` | E.164 phone validation, scenario schema validation |
| `providers/base.py` | `STTProvider`, `LLMProvider`, `TTSProvider`, `TelephonyProvider` Protocols |
| `providers/stt_deepgram.py` | Streaming STT impl |
| `providers/llm_groq.py` | LLM impl (default) |
| `providers/llm_openai.py` | Alternate LLM impl (proves abstraction works) |
| `providers/tts_deepgram.py` | Streaming TTS impl |
| `providers/telephony_twilio.py` | Outbound call placement (with `MachineDetection=DetectMessageEnd` when `ANSWERING_MACHINE_DETECTION=true`) + TwiML generation + webhook signature validation |
| `scenarios/loader.py` | Loads `appointment_reminder.yaml` into a `ScenarioConfig` Pydantic model; same model accepts a custom prompt string from the UI for one-call overrides. |
| `scenarios/appointment_reminder.yaml` | The single shipped scenario config |
| `conversation/engine.py` | Per-call conversation orchestration |
| `conversation/state.py` | `CallSession` state machine (turn history, extracted entities, status) |
| `conversation/outcome.py` | LLM-driven outcome extraction at call end |
| `ws/media_stream.py` | Twilio Media Streams ↔ Deepgram STT ↔ LLM ↔ Deepgram TTS bridge |
| `store/sessions.py` | In-memory `Dict[call_id, CallSession]` with TTL eviction |

### 4.3 Provider abstractions

All four protocols are pure interfaces. Concrete implementations live behind them. The factory in `core/config.py` selects implementations by env var: `STT_PROVIDER`, `LLM_PROVIDER`, `TTS_PROVIDER`, `TELEPHONY_PROVIDER`.

```python
class LLMProvider(Protocol):
    async def generate(
        self, system: str, messages: list[Message], **kwargs
    ) -> AsyncIterator[str]: ...

class STTProvider(Protocol):
    async def stream(
        self, audio_in: AsyncIterator[bytes]
    ) -> AsyncIterator[Transcript]: ...

class TTSProvider(Protocol):
    async def synthesize(
        self, text_in: AsyncIterator[str]
    ) -> AsyncIterator[bytes]: ...

class TelephonyProvider(Protocol):
    async def place_call(self, to: str, callback_url: str) -> CallHandle: ...
    def build_voice_response(self, ws_url: str) -> str: ...   # TwiML or equivalent
```

### 4.4 Scenario Config

The single scenario is defined in YAML and loaded into a Pydantic model at startup. When the UI sends a custom prompt instead, the same model is constructed in-memory for that one call. No registry, no persistence.

`ScenarioConfig` Pydantic model:

```yaml
id: appointment_reminder
name: Appointment Reminder & Confirmation
persona: |
  You are Sara, a warm and concise receptionist at MediCare Clinic.
  Speak in short, natural sentences. Never sound robotic.
goal: |
  Confirm, reschedule, or cancel the patient's upcoming appointment.
opening_line: |
  Hi, this is Sara from MediCare Clinic. Am I speaking with {patient_name}?
context_variables:
  - patient_name
  - appointment_time
  - doctor_name
success_criteria:
  - status in [confirmed, rescheduled, cancelled]
extraction_schema:
  status: enum[confirmed, rescheduled, cancelled, no_response, voicemail]
  requested_new_time: string?
  notes: string?
max_turns: 12
voice:
  provider: deepgram
  model: aura-2-thalia-en
guardrails:
  - never quote medical advice
  - never disclose other patients
```

When the UI submits a custom prompt, the backend wraps it in a minimal `ScenarioConfig` (custom persona/goal/opening line, default success criteria) and uses it for that one call only — never persisted.

### 4.5 Conversation Engine

For every utterance from the user:

1. STT yields finalized transcript chunk.
2. Engine appends `{role: user, text, ts}` to `session.history`.
3. Engine calls LLM with:
   - System prompt = `scenario.persona + scenario.goal + structured rules block + context_variables`
   - Messages = full `session.history`
   - Tools = `update_extracted_data`, `end_call`, `request_clarification`
4. LLM streams tokens → TTS streams audio → Twilio plays it.
5. Engine appends `{role: assistant, text, ts}` to history and updates `session.extracted_data`.
6. If `end_call` tool fired or `max_turns` reached → engine triggers call termination via Twilio.

After call ends, `OutcomeRecorder` makes one final LLM call to produce the structured outcome JSON, then persists it to `outcomes/{call_id}.json`.

## 5. Data Flow

**Outbound call dispatch:**
```
User clicks Start
  → POST /calls {phone, scenario_id|custom_scenario, context_variables}
  → Call Manager validates, creates CallSession, returns call_id
  → Telephony provider places call, passes webhook URLs
  → Twilio rings the phone, on answer fetches /twilio/voice
  → /twilio/voice returns TwiML <Stream> pointing at /ws/media/{call_id}
  → Twilio opens WS, sends start event, then μ-law audio frames
  → Media bridge:
      ┌─ user audio → Deepgram STT → transcripts → Conversation Engine
      └─ Engine LLM tokens → Deepgram TTS → μ-law audio → Twilio → user phone
  → On end_call tool / max_turns / hangup / timeout, WS closes
  → OutcomeRecorder extracts structured outcome
  → SSE stream pushes final state to UI
```

## 6. State Machine

`CallSession.status` transitions:

```
QUEUED → DIALING → RINGING → IN_PROGRESS → COMPLETED
                                          ↘ FAILED
                                          ↘ NO_ANSWER
                                          ↘ VOICEMAIL
                                          ↘ BUSY
                                          ↘ TIMED_OUT
                                          ↘ ABANDONED   (caller hung up early)
```

State is driven by Twilio status callbacks + WS lifecycle events + internal timers.

## 7. Context Awareness — explicit treatment

Every LLM call receives the **entire structured conversation history**, not just the last utterance. The structure includes:
- Persona + goal (system message, immutable per call)
- Context variables interpolated into system prompt
- Full alternating `user`/`assistant` history with timestamps
- Current `extracted_data` dict (so the LLM never re-asks captured info)
- A summary block injected after turn 10 to keep token budget bounded for long calls

This is the eval criterion *Context aware conversation* — it is implemented in `conversation/engine.py` and documented as such in the README.

## 8. Edge Cases & Robustness (HR is likely to probe these)

### 8.1 Input validation
- **Invalid phone numbers** — non-E.164, too short, too long, letters, empty. Reject at API with 422 + clear message.
- **International formats** — Accept E.164 only; reject national formats with a hint.
- **Unverified Twilio numbers (trial mode)** — Twilio will reject; we catch and surface a clear UI error: *"Phone not verified in your Twilio trial. Verify it in Twilio Console first."*
- **Custom scenario with missing fields** — Pydantic 422 with field-level errors.
- **Prompt-injection in custom scenario** — strip control sequences; wrap user-supplied persona inside fixed safety preamble; tools cannot be defined by user input.
- **Excessively long custom prompts** — truncate at 4k chars; reject with 422 if context_variables exceed 1k each.

### 8.2 Call lifecycle
- **No answer / busy / voicemail** — Twilio status callback → mark session with appropriate status, no LLM cost incurred.
- **Voicemail detection** — Twilio AMD (Answering Machine Detection) enabled; on `machine_*` result, agent leaves a short pre-defined voicemail and hangs up.
- **User hangs up mid-call** — WS close handler finalizes session, runs outcome extraction over partial transcript.
- **Twilio webhook delivery failure** — idempotent handlers + reconciliation: a background sweeper polls Twilio REST for any session stuck in DIALING > 60s.

### 8.3 Conversation pathology
- **Silence / no speech** — VAD timer: if no speech for 6s, agent says *"Are you still there?"*. Two consecutive silences → polite goodbye.
- **Barge-in (user interrupts agent)** — TTS playback cancellable on VAD speech detection; current LLM stream cancelled; new turn started.
- **User talks over the entire reply** — same as barge-in; logged as `interrupt_count` for the outcome.
- **Profanity / hostility** — agent acknowledges politely once, then if it continues, ends call with `status=hostile_caller` and records.
- **Off-topic questions** — scenario YAML includes `guardrails`; LLM is instructed to gently steer back. After 2 steers, end call.
- **"Are you a robot?"** — required honest disclosure: *"Yes, I'm an AI assistant calling on behalf of MediCare Clinic. Would you like to continue?"* — wired into the system prompt.
- **Non-English speech** — Deepgram returns confidence; below threshold or wrong-language detection → agent says *"I can only assist in English right now, I'll arrange a human to call back."* and ends.
- **Background noise / multiple speakers** — Deepgram diarization on; we use only the dominant speaker; below confidence → request clarification.
- **Very long pauses mid-sentence** — STT endpointing timeout tuned to 800ms; allows natural conversational gaps.
- **Caller asks something out of scope** — the LLM is system-prompted to acknowledge and offer to transfer/schedule a human callback.

### 8.4 Provider failures
- **STT WebSocket disconnect** — auto-reconnect with backoff (max 3 attempts in 5s); on permanent fail, agent says *"I'm having trouble hearing you, a human will call you back"* and ends.
- **LLM timeout / 5xx** — single retry with backoff; on second fail, fallback to a canned `"Apologies, I'll have a human call you back"` line and end call gracefully.
- **TTS failure** — fallback to Twilio `<Say>` with neural voice as a degraded mode.
- **Rate limits hit** — surfaced to UI; new call dispatch returns 429.
- **API key missing/invalid at startup** — fail fast with clear error listing which keys are missing; the app does not boot.

### 8.5 Concurrency & resource safety
- **Multiple concurrent calls** — each call has isolated `CallSession`; provider clients are async and pool-safe.
- **Race on `/calls` spam** — IP-based rate limit (5 calls/minute) via `slowapi`.
- **Memory leak prevention** — sessions older than 1 hour evicted; transcripts persisted to disk before eviction.
- **WS half-open detection** — keepalive pings every 20s; close after 60s of no pong.
- **Page refresh during call** — call continues server-side (state lives on backend); UI reconnects via SSE using stored `call_id` in `localStorage`.

### 8.6 Demo robustness
- **Local dev requires ngrok** — `ngrok` URL injected via env; startup logs print the exact URLs to paste into Twilio.
- **Twilio trial cannot call unverified numbers** — README has explicit "Verify Your Number" walkthrough.
- **Cost guardrails** — `MAX_CALLS_PER_HOUR` env cap to prevent runaway spend.

## 9. Configuration

`.env` (all keys documented in `.env.example`):
```
# Telephony
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Voice AI
DEEPGRAM_API_KEY=
GROQ_API_KEY=

# Optional alternates (provider abstraction proof)
OPENAI_API_KEY=

# Routing
STT_PROVIDER=deepgram
LLM_PROVIDER=groq
TTS_PROVIDER=deepgram
TELEPHONY_PROVIDER=twilio

# Public URL (ngrok during dev)
PUBLIC_BASE_URL=https://abc123.ngrok.io

# Safety
MAX_CALLS_PER_HOUR=20
MAX_TURNS_PER_CALL=12
CALL_MAX_DURATION_SECONDS=300

# Mode flags
STREAMING_MODE=true        # false = use Twilio <Gather>/<Say> turn-based fallback
ANSWERING_MACHINE_DETECTION=true
```

## 10. Testing Strategy

Focused tests covering the highest-risk areas:
- `tests/test_phone_validation.py` — E.164 happy/edge.
- `tests/test_scenario_loader.py` — YAML parsing, schema validation, missing-field rejection.
- `tests/test_scenario_registry.py` — preset load + custom POST + duplicate handling.
- `tests/test_conversation_state.py` — turn history, max-turns, end-call signal, silence handling.
- `tests/test_outcome_extraction.py` — mock LLM responses → structured outcome shape.
- `tests/test_providers_stub.py` — verifies all four protocols implemented by concrete classes (interface conformance).
- `tests/test_calls_api.py` — happy path + invalid phone + missing scenario + rate limit.
- `tests/test_twilio_webhooks.py` — TwiML shape + status callback transitions.

Manual smoke test (documented in README): real call to verified number using the appointment reminder scenario.

## 11. Logging & Observability

- Structured JSON logs with `call_id`, `turn_index`, `provider`, `latency_ms` per event.
- Every LLM call logs: tokens in, tokens out, latency, model.
- Every STT/TTS event logs: bytes, latency.
- Call summary log at termination with full outcome.
- README screenshot shows logs alongside the UI for the demo.

## 12. Security & Safety

- API keys only via env; `.env` gitignored; `.env.example` checked in.
- Twilio webhook signature verification on `/twilio/*` endpoints.
- Custom scenarios escape `{}` placeholders; only allow-listed variables interpolate.
- Outbound CORS allowed only from localhost in dev.
- No PII logged beyond the call transcript (which is necessary for the eval).

## 13. Repo Layout

```
voice-ai-agent/
├── app/
│   ├── main.py
│   ├── api/  {calls,scenarios,webhooks}.py
│   ├── core/ {config,logging,validation}.py
│   ├── providers/  base + concrete impls
│   ├── scenarios/  loader.py + appointment_reminder.yaml
│   ├── conversation/  engine, state, outcome
│   ├── ws/  media_stream.py
│   └── store/  sessions.py
├── web/
│   └── index.html
├── tests/
│   └── ...
├── outcomes/   (gitignored, runtime artifacts)
├── .env.example
├── .gitignore
├── requirements.txt
├── Makefile         (run, test, ngrok shortcuts)
├── docker-compose.yml  (optional)
└── README.md
```

## 14. Design Decisions Log

| Decision | Why |
|---|---|
| DIY pipeline over Vapi | Eval criterion explicitly rewards solution architecture; Vapi minimizes the surface that's being evaluated. |
| One scenario + custom prompt textarea | Task literally says "Choose one." The "select or define" UI requirement is satisfied by a textarea that overrides the prompt for one call — no registry, no persistence. |
| Flexibility via provider abstraction, not scenario engine | The flexibility hint is answered structurally: STT/LLM/TTS/telephony are all behind interfaces and swappable via env. Adding scenarios is one YAML file. |
| Deepgram for both STT and TTS | Less vendor surface, fewer auth flows, one connection pattern to test. Aura-2 quality is competitive. |
| Groq for LLM | Free tier without card, sub-second latency suits voice conversation. OpenAI implementation included to prove abstraction. |
| In-memory session store | Within scope of the eval; clearly documented as swappable via repository interface. |
| FastAPI + WebSocket (no Celery, no Redis) | Single-process simplicity for a demo; horizontal scaling out of scope. |
| Streaming end-to-end | Lower perceived latency; demonstrates real engineering vs turn-based `<Gather>` shortcut. |
| Server-Sent Events for UI updates | Simpler than WS for one-way push; works through ngrok cleanly. |

## 15. Risks

| Risk | Mitigation |
|---|---|
| Twilio trial restrictions block live demo | README documents number verification; demo video as fallback. |
| Streaming TTS+STT integration bugs eat time | Build-time fallback: set `STREAMING_MODE=false` to use Twilio `<Say>`/`<Gather>` turn-based flow (separate code path, same conversation engine). Distinct from the in-call TTS-failure recovery in §8.4. |
| Free-tier rate limits trip during eval | `MAX_CALLS_PER_HOUR` and clear quota docs in README. |
| LLM hallucinates outside scenario | Guardrails block in system prompt + post-turn validation against scenario rules. |
| Time pressure (deadline same day) | Strict scope per §2; commit frequently; ship a "working" tag even if streaming polish is incomplete. |

## 16. Acceptance Criteria

- `uvicorn app.main:app` boots without errors given a valid `.env`.
- `GET /scenarios` returns the appointment reminder preset.
- The UI custom-prompt mode lets a user override the scenario for one call.
- `POST /calls` to a Twilio-verified number triggers a real call that:
  - Greets with the persona's opening line
  - Holds a context-aware conversation through confirm/reschedule/cancel
  - Handles at least three edge cases live (silence, "are you a robot?", off-topic)
  - Ends gracefully and produces an `outcomes/{call_id}.json` with the structured shape from §4.4
- README documents setup, architecture, design decisions, edge-case coverage, and a demo walkthrough.
- All unit tests pass (`pytest`).
