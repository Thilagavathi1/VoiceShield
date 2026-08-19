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

from google import genai
from google.genai import types
from pydantic import BaseModel

from . import agora, rules
from .config import settings
from .prompts import SYSTEM_PROMPT, turn_block

log = logging.getLogger("voiceshield.guard")

_client: genai.Client | None = None


def gemini() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings().gemini_api_key)
    return _client


class LlmVerdict(BaseModel):
    """Response schema handed to Gemini.

    Passing this as `response_schema` makes the model return schema-valid JSON, so
    there is no fenced-output guessing to do. That removes a whole class of
    fail-open bug: previously an unparseable reply scored as "safe".
    """

    risk: int
    pattern: str
    signals: list[str]
    warning_hi: str
    warning_ta: str
    warning_en: str


class Verdict:
    """One turn's judgement.

    `error` is the difference between "this conversation looks safe" and "we could
    not judge it at all". Both used to come back as risk=0, which meant an outage,
    an expired key, or an empty credit balance rendered as a green "you are
    protected" screen. A safety device that fails silently is worse than none,
    because it manufactures the confidence the scammer needs. Anything that cannot
    reach a verdict must say so, loudly.
    """

    __slots__ = (
        "risk", "pattern", "signals", "warnings", "latency_ms", "error", "source",
    )

    def __init__(
        self,
        risk: int = 0,
        pattern: str = "none",
        signals: list[str] | None = None,
        warnings: dict[str, str] | None = None,
        latency_ms: int = 0,
        error: str | None = None,
        source: str = "none",
    ) -> None:
        self.risk = risk
        self.pattern = pattern
        self.signals = signals or []
        self.warnings = warnings or {}
        self.latency_ms = latency_ms
        self.error = error
        # "llm"      judged by the model
        # "screened" rules found nothing and the model was deliberately not asked
        # "rules"    model was unreachable, keyword floor only (degraded)
        # "none"     no judgement at all
        self.source = source

    @property
    def usable(self) -> bool:
        """True when this verdict reflects an actual judgement."""
        return self.source in ("llm", "screened", "rules")

    @property
    def degraded(self) -> bool:
        """True when we judged, but only with the free keyword floor."""
        return self.source == "rules"

    def warning_for(self, language: str) -> str:
        key = {"hi-IN": "warning_hi", "ta-IN": "warning_ta"}.get(language, "warning_en")
        return self.warnings.get(key) or self.warnings.get("warning_en", "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "pattern": self.pattern,
            "signals": self.signals,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "source": self.source,
            "degraded": self.degraded,
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
    """Score one turn: free rule layer first, then Gemini on top.

    The rule layer always runs. It is instant, costs nothing, and needs no network,
    so it doubles as the fallback when the model is unreachable, rate-limited, or out
    of quota -- the guard then degrades from *smart* to *basic* instead of to nothing.
    """
    turns = extract_turns(messages)
    if not turns:
        return Verdict(source="none")

    transcript = "\n".join(turns[-16:])
    rule = rules.score(turns)
    started = time.perf_counter()

    # NO GATING. An earlier version skipped the model whenever the rule layer scored
    # 0, to conserve the free tier's request budget. Measured against the hard corpus
    # that cost 36 points of recall: investment pitches, task-job scams, SIM-swap,
    # lapsed-insurance, fake-support and oblique "read me the six digit number" all
    # trip no keyword at all, so screening on a silent rule layer silently discards
    # real scams. The floor has a narrow vocabulary and zero false alarms, which makes
    # it a good fallback and a terrible filter.
    #
    # Quota pressure is handled where it belongs instead: one retry on 429/503, and
    # the rule floor as a labelled degraded verdict when the model truly cannot be
    # reached. A safety device does not skip the judgement to save a request.
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        # Schema-constrained JSON: no fence parsing, so a malformed reply can no
        # longer be mistaken for "safe".
        response_mime_type="application/json",
        response_schema=LlmVerdict,
        max_output_tokens=600,
        # Deterministic: the same call must not score differently on a retry, or
        # the corpus numbers mean nothing.
        temperature=0,
        # Minimum reasoning: this classifier is racing a live scammer. Note Gemini
        # 3.x rejects `thinking_budget` with a 400 -- `thinking_level` is the
        # replacement, and Gemini 3.x thinks by default if neither is set.
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        # We pass no tools; disabling AFC silences an SDK warning and removes any
        # chance of the model trying to call something.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    try:
        llm: LlmVerdict | None = None
        last_error: Exception | None = None
        # Flash tiers get transiently overloaded (503) and rate-limited (429) on the
        # free tier. One quick retry converts most of those into a real verdict
        # instead of dropping the whole call to the rule floor.
        for attempt in range(2):
            try:
                resp = await gemini().aio.models.generate_content(
                    model=settings().classifier_model,
                    contents=transcript,
                    config=config,
                )
                llm = resp.parsed
                if llm is None:
                    raise ValueError(
                        f"no parsed verdict (raw={(resp.text or '')[:200]!r})"
                    )
                break
            except Exception as e:  # noqa: PERF203 - two attempts, not a hot loop
                last_error = e
                transient = any(
                    s in str(e)
                    for s in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")
                )
                if attempt == 0 and transient:
                    await asyncio.sleep(0.4)
                    continue
                raise
        if llm is None:
            raise last_error or ValueError("classifier returned nothing")
    except Exception as e:
        # Never break the call audio, and never claim safety we cannot vouch for.
        # Fall back to the rule layer and label the verdict as rules-only so the UI
        # shows reduced protection rather than a green shield.
        log.error("LLM classifier unavailable, falling back to rules: %s", e)
        return Verdict(
            risk=rule.risk,
            pattern=rule.pattern,
            signals=rule.signals,
            warnings=rules.canned_warnings(rule.pattern),
            latency_ms=int((time.perf_counter() - started) * 1000),
            source="rules",
            error=f"{type(e).__name__}: {e}",
        )

    latency = int((time.perf_counter() - started) * 1000)

    # Take the higher of the two. The rule layer scored 0 false alarms across the
    # corpus's 12 innocent lookalikes, so it adds recall without measurably adding
    # false positives -- but that evidence base is small, so log every disagreement
    # and re-check it as the corpus grows.
    risk = max(int(llm.risk), rule.risk)
    if rule.risk >= settings().warn_threshold > int(llm.risk):
        log.warning(
            "rules flagged %s (%s) but LLM scored %s — review this case",
            rule.risk,
            rule.pattern,
            llm.risk,
        )

    return Verdict(
        risk=risk,
        pattern=llm.pattern if llm.pattern != "none" else rule.pattern,
        signals=(list(llm.signals) + rule.signals)[:6],
        warnings={
            "warning_hi": llm.warning_hi,
            "warning_ta": llm.warning_ta,
            "warning_en": llm.warning_en,
        }
        if int(llm.risk) >= settings().warn_threshold
        # LLM saw no need to warn but rules did: use the canned text, since the
        # model returned empty warning strings.
        else rules.canned_warnings(rule.pattern),
        latency_ms=latency,
        source="llm",
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
    if not verdict.usable:
        # No judgement was made. Don't speak (we have nothing to say), but the UI
        # has already been told the guard is degraded so the elder is not shown a
        # protection claim we cannot back.
        return
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
    await agora.speak(session.agent_id, text, channel=session.channel)
