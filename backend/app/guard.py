"""The sensor: an OpenAI-compatible /chat/completions endpoint that Agora's pipeline
calls once per conversation turn.

Two things make this different from a normal voice-agent LLM:

1. It almost always returns SILENCE. A guard that chats is a guard that gets muted.
   We return an empty assistant message so the TTS module has nothing to say.

2. When risk crosses the threshold it fires POST /speak (INTERRUPT, interruptable=False)
   out-of-band instead of answering in-line. That matters: an in-line answer is only
   spoken after the current turn ends, but a scammer's monologue may not end for a
   minute. /speak INTERRUPT cuts in mid-sentence, which is the whole product.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from . import agora
from .config import settings
from .prompts import SYSTEM_PROMPT, turn_block

log = logging.getLogger("voiceshield.guard")

_client: AsyncAnthropic | None = None


def claude() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings().anthropic_api_key)
    return _client


class Verdict:
    __slots__ = ("risk", "pattern", "signals", "warnings", "latency_ms")

    def __init__(
        self,
        risk: int = 0,
        pattern: str = "none",
        signals: list[str] | None = None,
        warnings: dict[str, str] | None = None,
        latency_ms: int = 0,
    ) -> None:
        self.risk = risk
        self.pattern = pattern
        self.signals = signals or []
        self.warnings = warnings or {}
        self.latency_ms = latency_ms

    def warning_for(self, language: str) -> str:
        key = {"hi-IN": "warning_hi", "ta-IN": "warning_ta"}.get(language, "warning_en")
        return self.warnings.get(key) or self.warnings.get("warning_en", "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "pattern": self.pattern,
            "signals": self.signals,
            "latency_ms": self.latency_ms,
        }


def extract_turns(messages: list[dict[str, Any]]) -> list[str]:
    """Flatten Agora's message history into speaker-tagged lines.

    With SAL in `recognition` mode, Agora attaches speaker ids in a `metadata.vpids`
    field. The exact shape is beta and undocumented in detail, so we probe the likely
    locations and degrade to `unknown` rather than crashing mid-call. Log what we
    actually receive on day 1 and tighten this.
    """
    out: list[str] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):  # multimodal-style parts
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        if not content:
            continue

        meta = m.get("metadata") or {}
        vpids = meta.get("vpids") or m.get("vpids")
        if isinstance(vpids, list) and vpids:
            speaker = str(vpids[0])
        elif isinstance(vpids, str) and vpids:
            speaker = vpids
        else:
            speaker = "unknown"

        # Agora reserves "unknown"; our registered voiceprint key is "elder".
        speaker = "elder" if speaker == "elder" else "unknown"
        out.append(turn_block(speaker, str(content).strip()))
    return out


async def classify(messages: list[dict[str, Any]]) -> Verdict:
    turns = extract_turns(messages)
    if not turns:
        return Verdict()

    transcript = "\n".join(turns[-16:])
    started = time.perf_counter()
    try:
        resp = await claude().messages.create(
            model=settings().classifier_model,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        )
        raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        # Be tolerant: the model may still fence the JSON despite instructions.
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("classifier returned non-JSON, treating as safe")
        return Verdict()
    except Exception as e:
        # Never let a classifier failure break the call audio.
        log.error("classifier error: %s", e)
        return Verdict()

    latency = int((time.perf_counter() - started) * 1000)
    return Verdict(
        risk=int(data.get("risk", 0)),
        pattern=str(data.get("pattern", "none")),
        signals=list(data.get("signals", []))[:6],
        warnings={
            k: str(data.get(k, "")) for k in ("warning_hi", "warning_ta", "warning_en")
        },
        latency_ms=latency,
    )


# --- OpenAI-compatible SSE response -------------------------------------------------
# Agora requires streaming; a non-streaming reply is rejected with 400.


def _chunk(model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": "voiceshield-guard",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def stream_response(
    body: dict[str, Any],
    on_verdict,
) -> AsyncIterator[str]:
    """Score the turn, stay silent, and escalate out-of-band when warranted.

    `on_verdict(verdict)` is awaited so the session layer can push the risk score to the
    Android UI and fire /speak.
    """
    model = str(body.get("model", "voiceshield-guard"))
    messages = body.get("messages") or []

    verdict = await classify(messages)
    try:
        await on_verdict(verdict)
    except Exception as e:
        log.error("verdict side-effect failed: %s", e)

    # Silence. The elder must never hear the guard unless it is warning them.
    yield _chunk(model, {"role": "assistant", "content": ""})
    yield _chunk(model, {}, finish="stop")
    yield "data: [DONE]\n\n"


async def escalate(session, verdict: Verdict) -> None:
    """Fire the spoken warning if this turn crossed the line.

    Debounced: once warned, don't re-warn for 20s. Repeating every turn would drown out
    the call and the elder would stop listening to us.
    """
    s = settings()
    if verdict.risk < s.warn_threshold:
        return
    now = time.monotonic()
    if now - session.last_warned_at < 20:
        return
    text = verdict.warning_for(session.language)
    if not text:
        return
    session.last_warned_at = now
    session.warned = True
    log.warning(
        "WARNING fired on %s risk=%s pattern=%s",
        session.channel,
        verdict.risk,
        verdict.pattern,
    )
    await agora.speak(session.agent_id, text)
