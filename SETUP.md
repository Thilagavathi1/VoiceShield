# VoiceShield — Agora setup (do this first, today)

You need four secrets. All of them live on the server, **none** go in the APK.

| Secret | Where you get it | Used for |
|---|---|---|
| `AGORA_APP_ID` | Console → Project | Client + server |
| `AGORA_APP_CERTIFICATE` | Console → Project → Enable "App Certificate" | Generating RTC tokens (server only) |
| `AGORA_CUSTOMER_ID` | Console → Developer Toolkit → RESTful API | Basic auth for the Conversational AI REST API |
| `AGORA_CUSTOMER_SECRET` | Same — **downloadable exactly once** | Same |

---

## Step 1 — Create the account and project

1. Sign up at <https://console.agora.io/>. The free tier includes 10,000 free minutes/month, which is far more than you'll need.
2. **"What are you building?" → choose `Voice Agent Builder`**, not Realtime Communications Builder.

   VoiceShield genuinely needs both halves — Conversational AI Engine for the agent, and RTC for the Android channel — but Conversational AI is the hackathon's mandatory component and the harder thing to get enabled, so start in its workspace. Nothing is locked: App ID, App Certificate, and the RESTful API secrets are all project/account-level and reachable from either, and the console lets you switch builders from the account menu.
3. **Project Management → Create a project.**
   - Name: `voiceshield`
   - Use case: pick anything (Social/Voice)
   - Authentication: **Secured mode: APP ID + Token** ← not testing mode. Token mode is required for Conversational AI, and testing mode will silently waste you a day.
4. Copy the **App ID**.
5. In the project's settings, find **App Certificate** and enable/reveal it. Copy it.

## Step 2 — Enable Conversational AI Engine ⚠️ DO THIS NOW

In the project's feature/product list, enable **Conversational AI Engine**.

**This is the single biggest schedule risk in the whole project.** On some accounts it's a toggle; on others it requires a support request that takes a business day or two. You have 24 days and this blocks *everything* — the app, the backend, the demo. If it's not a self-serve toggle for you, open the support ticket **today (Aug 5)**, not on the weekend.

While you wait for enablement you can still build: the FastAPI backend and the Android UI both develop fine against a stub.

## Step 3 — Generate REST credentials

1. Console → **Developer Toolkit → RESTful API**
2. **Add a secret** → OK
3. Click **Download** in the Customer Secret column → save `key_and_secret.txt` somewhere safe.

> You can download the Customer Secret **only once**. If you lose it, you must create a new one.

## Step 4 — Verify before writing any app code

This is a read-only call. Run it as soon as you have credentials:

```bash
export AGORA_APP_ID="your_app_id"
export AGORA_CUSTOMER_ID="your_customer_id"
export AGORA_CUSTOMER_SECRET="your_customer_secret"

curl -i -s \
  -u "$AGORA_CUSTOMER_ID:$AGORA_CUSTOMER_SECRET" \
  "https://api.agora.io/api/conversational-ai-agent/v2/projects/$AGORA_APP_ID/agents?limit=1"
```

Read the status code carefully — it tells you exactly which step failed:

| Status | Meaning | Fix |
|---|---|---|
| `200` | ✅ Everything works. Start building. | — |
| `401` | Credentials wrong | Re-check Customer ID/Secret; no stray newline from the .txt file |
| `403` / `404` | Auth fine, **product not enabled** | Go back to Step 2 |
| `400` | Auth fine, request shape off | Harmless here — auth is what you're testing |

Only move on when you see `200`.

## Step 5 — Fill in the backend env

```bash
cd backend
cp .env.example .env
# then edit .env with the four Agora values + your GEMINI_API_KEY
```

---

## Why these specific product choices

Decisions already made for you, with reasons — useful for your presentation's architecture slide.

> **These were the day-1 plan. Two of them did not survive contact with the API** — the
> shipped configuration is in `backend/app/agora.py`, with the probe results in comments.
> Quote the code, not this section.

**ASR: `ares`** (planned: `sarvam`) — Sarvam is an Indian Indic-language speech model, but it is BYOK: it needs an API key of your own, and it rejects an empty params block with `Invalid value at properties.asr.params.model`. `ares` is Agora's own engine, needs no third-party credential, takes no params, and covers hi-IN, ta-IN and eight more Indic languages — so the whole pipeline stays key-free. Scam calls are almost never in clean English, and ares handles the code-mixing.

**LLM: `custom`** (your FastAPI endpoint) — required, for two reasons. Selective Attention Locking in `recognition` mode only passes speaker identity (`vpids`) to a *custom* LLM, and you need your own code in the loop to score risk and fire the warning.

**TTS: `minimax`, `credential_mode: managed`** — Agora supplies the credentials, so you don't need an ElevenLabs/Azure key. Only `minimax` and `openai` are actually available in managed mode on this SKU: microsoft, google, elevenlabs, cartesia, deepgram and amazon all answer *"vendor is not available for the current SKU when credential_mode is 'managed'"*. Minimax over openai because its models are natively multilingual, and the warning has to be spoken in Hindi or Tamil to land.

**Pipeline: ASR → LLM → TTS, NOT `mllm`.** Critical. The `/speak` endpoint — your entire warning mechanism — **is not supported with `mllm` configuration**, and SAL `recognition` needs a custom LLM anyway. Every Agora starter sample pushes you toward MLLM because it's lower latency for a chatbot. Do not follow them.

## Known unknown to verify on day 1

The Conversational AI Android toolkit (`io.agora.agents:agora-agent-client-toolkit:2.9.0`) declares only Kotlin/coroutines/Gson as dependencies, so the RTC and Signaling (RTM) SDKs are expected to come from you. `io.agora.rtc:voice-sdk:4.6.3` covers RTC. If `subscribeMessage()` fails to resolve at runtime, you're missing the RTM SDK — add it then, rather than guessing now.
