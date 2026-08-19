"""VoiceShield backend.

Responsibilities:
  1. Mint RTC tokens for the Android app (App Certificate never leaves the server).
  2. Start/stop the Agora guard agent.
  3. Serve the custom-LLM sensor endpoint that Agora's pipeline calls each turn.
  4. Push risk updates to the app over a WebSocket so the screen can go red.

Run:  uvicorn app.main:app --reload --port 8000
Then: ngrok http 8000   ->  put that https URL in PUBLIC_BASE_URL
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agora, guard
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voiceshield")

app = FastAPI(title="VoiceShield", version="0.1.0")


@dataclass
class Session:
    channel: str
    agent_id: str
    language: str
    started_at: float = field(default_factory=time.monotonic)
    last_warned_at: float = -1e9
    warned: bool = False
    peak_risk: int = 0
    listeners: list[WebSocket] = field(default_factory=list)


SESSIONS: dict[str, Session] = {}          # channel -> session
BY_AGENT: dict[str, Session] = {}          # agent_id -> session


# --- app-facing API -----------------------------------------------------------------


class StartRequest(BaseModel):
    channel: str
    language: str | None = None
    voiceprint_url: str | None = None


class StartResponse(BaseModel):
    channel: str
    app_id: str
    uid: str
    rtc_token: str
    agent_id: str


@app.post("/session/start", response_model=StartResponse)
async def session_start(req: StartRequest) -> StartResponse:
    missing = settings().missing()
    if missing:
        raise HTTPException(503, f"backend not configured: missing {missing}")
    if req.channel in SESSIONS:
        raise HTTPException(409, "a guard is already watching this channel")

    language = req.language or settings().default_language
    agent_id = await agora.start_agent(
        channel=req.channel,
        agent_name=f"voiceshield-{req.channel}-{int(time.time())}",
        language=language,
        voiceprint_url=req.voiceprint_url,
    )
    session = Session(channel=req.channel, agent_id=agent_id, language=language)
    SESSIONS[req.channel] = session
    BY_AGENT[agent_id] = session

    return StartResponse(
        channel=req.channel,
        app_id=settings().agora_app_id,
        uid=agora.ELDER_UID,
        rtc_token=agora.build_rtc_token(req.channel, agora.ELDER_UID),
        agent_id=agent_id,
    )


@app.post("/session/{channel}/stop")
async def session_stop(channel: str) -> dict[str, object]:
    session = SESSIONS.pop(channel, None)
    if session is None:
        raise HTTPException(404, "no such session")
    BY_AGENT.pop(session.agent_id, None)

    evidence: dict[str, object] = {}
    try:
        # Pull the transcript BEFORE stopping; short-term history dies with the agent.
        evidence = await agora.history(session.agent_id, session.channel)
    except Exception as e:
        log.warning("could not retrieve evidence transcript: %s", e)
    finally:
        try:
            await agora.stop_agent(session.agent_id, session.channel)
        except Exception as e:
            log.warning("stop_agent failed: %s", e)

    return {
        "channel": channel,
        "warned": session.warned,
        "peak_risk": session.peak_risk,
        "duration_s": round(time.monotonic() - session.started_at, 1),
        "evidence": evidence,
    }


@app.get("/session/{channel}/metrics")
async def session_metrics(channel: str) -> dict[str, object]:
    """Turn-level latency, for the on-screen HUD in the demo."""
    session = SESSIONS.get(channel)
    if session is None:
        raise HTTPException(404, "no such session")
    return await agora.turns(session.agent_id, session.channel)


@app.websocket("/ws/{channel}")
async def ws_alerts(ws: WebSocket, channel: str) -> None:
    """Risk updates pushed to the app: drives the red screen and haptics.

    Using our own socket rather than Agora Signaling because the risk score is VoiceShield's
    own computation, not part of the conversation. Transcripts still arrive in the app
    via the Agora client toolkit's onTranscriptUpdated.
    """
    await ws.accept()
    session = SESSIONS.get(channel)
    if session is not None:
        session.listeners.append(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; app sends pings
    except WebSocketDisconnect:
        pass
    finally:
        if session is not None and ws in session.listeners:
            session.listeners.remove(ws)


async def _broadcast(session: Session, payload: dict[str, object]) -> None:
    dead = []
    for ws in session.listeners:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        session.listeners.remove(ws)


# --- Agora-facing sensor ------------------------------------------------------------

_DUMP_PATH = pathlib.Path("/tmp/voiceshield_llm_requests.jsonl")
_dumped = 0
_DUMP_LIMIT = 8


def _dump_request(body: dict) -> None:
    """Append the raw Agora request to a file, for the first few calls only."""
    global _dumped
    if _dumped >= _DUMP_LIMIT:
        return
    _dumped += 1
    try:
        with _DUMP_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")
        log.info("dumped Agora LLM request #%s to %s", _dumped, _DUMP_PATH)
    except Exception as e:
        log.warning("could not dump request: %s", e)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> StreamingResponse:
    """Called by Agora once per conversation turn. Must stream SSE."""
    body = await request.json()

    # Dump the first few real request bodies verbatim. The SAL `vpids` metadata shape
    # is beta and undocumented in detail, and guard.extract_turns() has to guess where
    # the speaker id lives -- this is how we replace that guess with fact.
    _dump_request(body)

    if not body.get("stream", True):
        raise HTTPException(400, "chat completions require streaming")

    # Agora does not echo the channel, so resolve the session by agent id when present
    # and otherwise fall back to the only live session (fine for a demo, and logged).
    session = _resolve_session(body)

    async def on_verdict(verdict: guard.Verdict) -> None:
        if session is None:
            log.info("verdict with no session: risk=%s", verdict.risk)
            return
        session.peak_risk = max(session.peak_risk, verdict.risk)
        await _broadcast(
            session,
            {
                "type": "risk",
                **verdict.as_dict(),
                "warned": verdict.risk >= settings().warn_threshold,
            },
        )
        await guard.escalate(session, verdict)

    return StreamingResponse(
        guard.stream_response(body, on_verdict),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_session(body: dict) -> Session | None:
    for key in ("agent_id", "agentId"):
        aid = body.get(key)
        if aid and aid in BY_AGENT:
            return BY_AGENT[aid]
    if len(SESSIONS) == 1:
        return next(iter(SESSIONS.values()))
    if SESSIONS:
        log.warning("multiple sessions and no agent_id in request; cannot route verdict")
    return None


# --- health -------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, object]:
    missing = settings().missing()
    ok, detail = (False, f"missing config: {missing}")
    if not missing:
        ok, detail = await agora.credentials_ok()
    return {
        "configured": not missing,
        "missing": missing,
        "agora": {"ok": ok, "detail": detail},
        "llm_callback_url": settings().llm_callback_url or "(PUBLIC_BASE_URL unset)",
        "active_sessions": list(SESSIONS),
    }
