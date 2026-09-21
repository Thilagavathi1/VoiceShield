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
import contextlib
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

# How long a session may sit with no app listening before we drop it.
#
# Deliberately LONGER than Agora's own idle_timeout (30s, set in agora.py): Agora already
# stops billing by terminating the agent 30s after the elder's RTC peer leaves, so this is
# registry hygiene, not a billing control. Cutting it below 30s to grab the evidence
# transcript before Agora deletes it would mean a 25s mobile-data blip tears down the guard
# mid-call -- and dropping protection during a live scam is a far worse failure than losing
# a transcript on a path where the app has already been killed.
ORPHAN_GRACE_S = 90
# Absolute backstop for a session that somehow keeps a listener forever.
MAX_SESSION_S = 30 * 60
# How stale a captured transcript may get while a call is live. Below Agora's 30s
# idle_timeout, so a session that dies without warning still has evidence from within
# the last few seconds of the call rather than none at all.
EVIDENCE_REFRESH_S = 20
REAP_INTERVAL_S = 15


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Own the reaper task for exactly as long as the app is up."""
    task = asyncio.create_task(_reap_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Retire what we still own before the registry goes with us. This one DOES matter
        # for more than hygiene: the elder is still in the channel, so Agora's idle_timeout
        # has not started counting, and the agent would keep running against a backend that
        # no longer knows it exists. Best effort only: nothing runs on SIGKILL.
        for session in list(SESSIONS.values()):
            log.warning("shutdown: retiring session %s", session.channel)
            with contextlib.suppress(Exception):
                await _teardown(session, "backend shutting down")


app = FastAPI(title="VoiceShield", version="0.1.0", lifespan=lifespan)


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
    # Starts at creation time, not 0: /session/start returns before the app has had a
    # chance to open its WebSocket, and a session must not be reaped in that window.
    last_listener_at: float = field(default_factory=time.monotonic)
    # Last transcript we managed to pull, and when. Agora deletes an agent's short-term
    # history when the agent goes, and on the unhappy path the agent is already gone by
    # the time we notice -- so the evidence has to be captured while the call is live,
    # not asked for at teardown.
    evidence: dict[str, object] = field(default_factory=dict)
    evidence_at: float = -1e9


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


async def _snapshot_evidence(session: Session) -> bool:
    """Capture the transcript mid-call. Never raises: this runs on background paths."""
    try:
        session.evidence = await agora.history(session.agent_id, session.channel)
        session.evidence_at = time.monotonic()
        return True
    except Exception as e:
        log.debug("evidence snapshot failed for %s: %s", session.channel, e)
        return False


async def _teardown(session: Session, reason: str) -> dict[str, object]:
    """Retire a session and its Agora agent.

    Shared by the app's explicit stop and by the reaper, so an agent torn down because the
    phone vanished is retired exactly the same way as one the elder stopped by hand -- and
    in particular still yields its evidence transcript, which is the whole point of the
    feature and is the thing most likely to be silently dropped on the unhappy path.
    """
    SESSIONS.pop(session.channel, None)
    BY_AGENT.pop(session.agent_id, None)

    evidence: dict[str, object] = {}
    stale = False
    try:
        # Pull the transcript BEFORE stopping; short-term history dies with the agent.
        evidence = await agora.history(session.agent_id, session.channel)
    except Exception as e:
        # Expected whenever Agora retired the agent before us (the orphan path): history
        # goes with the agent. Fall back to the last mid-call snapshot, which is the only
        # copy that still exists, rather than reporting a scam call with no evidence.
        evidence = session.evidence
        stale = bool(evidence)
        log.warning(
            "live transcript unavailable for %s (%s); %s",
            session.channel,
            e,
            f"using snapshot from {time.monotonic() - session.evidence_at:.0f}s before teardown"
            if stale
            else "no snapshot was captured either",
        )
    finally:
        try:
            await agora.stop_agent(session.agent_id, session.channel)
        except Exception as e:
            log.warning("stop_agent failed: %s", e)

    return {
        "channel": session.channel,
        "reason": reason,
        # So a caller can tell "this is the whole call" from "this is what we had when
        # the phone vanished" without having to guess from the content.
        "evidence_is_snapshot": stale,
        "warned": session.warned,
        "peak_risk": session.peak_risk,
        "duration_s": round(time.monotonic() - session.started_at, 1),
        "evidence": evidence,
    }


async def _reap_forever() -> None:
    """Retire sessions whose app is gone.

    Expiry belongs on the server because no client-side hook covers the cases that matter:
    the guard runs as a foreground Service precisely so it outlives the Activity, so an
    Activity-lifecycle stop would kill it exactly when the elder switches to the dialer --
    and force-stop, a crash, or a flat battery run no client code at all.

    What this does NOT do is stop the billing; agora.py sets idle_timeout=30, so Agora has
    already terminated the agent by the time we get here (both /history and /leave 404 on
    this path, which is expected and logged, not a failure). What it does do is keep
    SESSIONS and /health honest, and stop dead channels resolving to a live-looking session.
    """
    while True:
        try:
            await asyncio.sleep(REAP_INTERVAL_S)
            now = time.monotonic()
            # Snapshot: _teardown mutates SESSIONS, and awaiting inside a live view of it
            # would skip or double-visit entries.
            for session in list(SESSIONS.values()):
                if session.listeners:
                    session.last_listener_at = now
                    orphaned_for = 0.0
                else:
                    orphaned_for = now - session.last_listener_at

                # Refresh regardless of orphan state: a session orphaned for less than
                # Agora's 30s idle_timeout still has a live agent, and that is the last
                # chance to capture the tail of the call before the history goes with it.
                if now - session.evidence_at > EVIDENCE_REFRESH_S:
                    await _snapshot_evidence(session)

                if orphaned_for > ORPHAN_GRACE_S:
                    reason = f"orphaned {orphaned_for:.0f}s"
                elif now - session.started_at > MAX_SESSION_S:
                    reason = f"max lifetime {MAX_SESSION_S}s"
                else:
                    continue

                log.warning("reaping session %s (%s)", session.channel, reason)
                await _teardown(session, reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A reaper that dies on one bad session stops protecting every other one.
            log.exception("reaper iteration failed; continuing")


@app.post("/session/{channel}/stop")
async def session_stop(channel: str) -> dict[str, object]:
    session = SESSIONS.get(channel)
    if session is None:
        raise HTTPException(404, "no such session")
    return await _teardown(session, "stopped by app")


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
            # Start the orphan clock from the moment the last listener left, so a session
            # is judged on how long the app has been gone, not on when it was created.
            if not session.listeners:
                session.last_listener_at = time.monotonic()


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

    # Route by the channel we embedded in the callback URL; fall back to the body.
    session = SESSIONS.get(request.query_params.get("channel", "")) or _resolve_session(body)

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
                # Sticky, not per-turn. A scammer says one incriminating sentence and then
                # something bland ("hello? are you there?"); recomputing this each turn sent
                # the elder's screen back to green a second or two after the warning fired.
                # session.warned is set by escalate() below, i.e. after this broadcast, so
                # the risk comparison still covers the turn that first trips the threshold.
                "warned": session.warned or verdict.risk >= settings().warn_threshold,
            },
        )
        was_warned = session.warned
        await guard.escalate(session, verdict)
        # Exactly once, when the alarm first trips: this is the call we will be asked to
        # produce evidence for, and waiting for a clean hang-up to ask for it is how the
        # transcript gets lost. Awaited rather than backgrounded -- it runs after the
        # spoken barge-in, so it delays only the sensor's own response, and once per call.
        if session.warned and not was_warned:
            await _snapshot_evidence(session)

    return StreamingResponse(
        guard.stream_response(body, on_verdict),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_session(body: dict) -> Session | None:
    """Last-resort routing when the callback URL carried no usable channel."""
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
