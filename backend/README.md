# VoiceShield backend

Three jobs: mint RTC tokens, run the Agora guard agent, and act as the custom LLM that
scores every conversation turn for scam risk.

## Run

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in — see ../SETUP.md
uvicorn app.main:app --reload --port 8000
```

Agora's servers must be able to reach your custom LLM endpoint, so localhost will not do.
In development, tunnel it:

```bash
ngrok http 8000
# put the https URL into PUBLIC_BASE_URL in .env, then restart uvicorn
```

Check everything is wired:

```bash
curl -s localhost:8000/health | python3 -m json.tool
```

`/health` tells you exactly what is missing — unset env vars, bad Agora credentials, or
Conversational AI not enabled on the project.

## Score the classifier (works before Agora is enabled)

This only needs `ANTHROPIC_API_KEY`, so it is the most useful thing you can do on day 1
while waiting for Conversational AI enablement.

```bash
python -m eval.score_corpus
```

Prints per-case results plus recall, false-positive rate, precision, and classify latency.
**Put that confusion matrix on a slide.** The seed corpus in `data/scam_corpus.jsonl` has
16 scams and 12 innocent lookalikes; grow it toward ~40/20. The lookalikes matter more than
the scams — a false alarm that makes an elder hang up on their real daughter is a genuine
harm, and it is the failure mode a judge will probe in Q&A.

When you miss a case, fix `app/prompts.py` before touching `WARN_THRESHOLD`. Lowering the
threshold to catch one scam usually buys you three false alarms.

## Endpoints

| Endpoint | Who calls it | Purpose |
|---|---|---|
| `POST /session/start` | Android app | RTC token + starts the guard agent |
| `POST /session/{ch}/stop` | Android app | Stops agent, returns evidence transcript |
| `GET /session/{ch}/metrics` | Android app | Turn latency, for the on-screen HUD |
| `WS /ws/{ch}` | Android app | Live risk updates → red screen + haptics |
| `POST /v1/chat/completions` | **Agora** | The sensor. SSE required. |
| `GET /health` | you | Config and credential diagnostics |

## How the intervention works

`/v1/chat/completions` is called by Agora once per conversation turn. It does two unusual
things:

1. **It returns silence.** An empty assistant message, every time. A guard that chats is a
   guard the elder mutes.
2. **When risk ≥ `WARN_THRESHOLD`, it fires `POST /agents/{id}/speak` out-of-band** with
   `priority: INTERRUPT, interruptable: false`.

That second point is the design's core. Answering in-line would only be spoken after the
current turn ends, and a scammer's monologue may not end for a minute. `INTERRUPT` cuts in
mid-sentence, and `interruptable: false` means the scammer talking over it cannot suppress
it. Live social engineering works by never letting the victim stop and think; this is how
you break that.

Warnings are debounced to one per 20s. Repeating every turn would drown out the call and
the elder would tune us out.

## Things to verify on day 1 and tighten

- **SAL metadata shape.** `advanced_features.enable_sal` with `sal_mode: "recognition"` is
  documented as passing speaker ids in a `vpids` metadata field, but the exact JSON shape
  is beta and not fully specified. `guard.extract_turns()` probes the likely locations and
  degrades to `unknown` rather than crashing. **Log the raw request body on your first real
  call and tighten that function** — everything downstream depends on knowing who spoke.
- **Voiceprint enrollment.** `sal.sample_urls` needs a publicly downloadable `.pcm` of the
  elder's voice. You need an enrollment screen that records ~10s and uploads it somewhere
  Agora can fetch. Until then SAL falls back to locking onto whoever speaks first and
  clearest, which is good enough for a demo but not for the pitch.
- **Session routing.** `_resolve_session()` falls back to "the only live session" because
  Agora does not echo the channel back in the LLM request. Fine for a demo, wrong for
  production; log a warning if you ever run two at once.
