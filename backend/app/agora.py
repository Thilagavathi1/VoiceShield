"""Thin client over the Agora Conversational AI REST API.

Deliberately raw httpx rather than the `agora_agent` builder SDK: VoiceShield depends on
Selective Attention Locking (beta) and on /speak, and raw REST guarantees we can set
every field the docs expose. We do use the official SDK's token helper, since
hand-rolling AccessToken2 is a pointless risk.

Endpoints used:
  POST /join                      start an agent in a channel
  POST /agents/{id}/leave         stop it
  POST /agents/{id}/speak         barge in with a fixed warning  <-- the intervention
  GET  /agents/{id}/history       transcript, used as scam evidence
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("voiceshield.agora")

# The agent's own UID in the channel. The Android client uses ELDER_UID.
AGENT_UID = "1001"
ELDER_UID = "2001"

TOKEN_TTL_SECONDS = 24 * 3600


def build_rtc_token(channel: str, uid: str) -> str:
    """RTC token so a participant (app or agent) can join `channel` as `uid`."""
    from agora_agent.agentkit.token import generate_convo_ai_token

    return generate_convo_ai_token(
        app_id=settings().agora_app_id,
        app_certificate=settings().agora_app_certificate,
        channel_name=channel,
        account=uid,
        token_expire=TOKEN_TTL_SECONDS,
    )


# Channel used only to mint an auth token for account-level calls that have no channel of
# their own (listing agents, the /health probe).
AUTH_PROBE_CHANNEL = "voiceshield-auth"


def auth_mode() -> str:
    """Which REST auth scheme we can use with the credentials present.

    Basic auth needs a Customer ID/Secret pair, which in the redesigned Agora console is
    no longer a self-serve item on every account. Token auth needs only the App ID and App
    Certificate, so it is the path that always works -- we prefer Basic when available
    purely because a single static credential is easier to debug.
    """
    s = settings()
    if s.agora_customer_id and s.agora_customer_secret:
        return "basic"
    if s.agora_app_id and s.agora_app_certificate:
        return "token"
    return "none"


def _auth_header(channel: str | None = None) -> dict[str, str]:
    s = settings()
    headers = {"Content-Type": "application/json"}

    if auth_mode() == "basic":
        raw = f"{s.agora_customer_id}:{s.agora_customer_secret}".encode()
        headers["Authorization"] = f"Basic {base64.b64encode(raw).decode()}"
        return headers

    # Token auth. Per the docs the token's channel and uid must match the agent request,
    # so callers pass the channel they are operating on.
    token = build_rtc_token(channel or AUTH_PROBE_CHANNEL, AGENT_UID)
    headers["Authorization"] = f"agora token={token}"
    return headers


async def _post(
    path: str,
    body: dict[str, Any] | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    url = f"{settings().agora_api_base}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json=body or {}, headers=_auth_header(channel))
    if r.status_code != 200:
        log.error("agora POST %s -> %s %s", path, r.status_code, r.text)
        r.raise_for_status()
    return r.json() if r.content else {}


async def _get(path: str, channel: str | None = None) -> dict[str, Any]:
    url = f"{settings().agora_api_base}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers=_auth_header(channel))
    r.raise_for_status()
    return r.json() if r.content else {}


def _agent_payload(
    channel: str,
    agent_name: str,
    language: str,
    voiceprint_url: str | None,
) -> dict[str, Any]:
    """Build the /join body.

    Two choices here carry the whole project, so they're commented in place.
    """
    sal: dict[str, Any] = {
        # `recognition` tags every utterance with a speaker id (vpids) in the metadata
        # handed to the custom LLM, so the classifier knows whether the ELDER or an
        # UNKNOWN voice said a given line. That distinction is the core signal:
        # "unknown speaker demands OTP" is a scam; the elder saying "OTP" is not.
        # It also suppresses ambient noise, which matters on a speakerphone.
        "sal_mode": "recognition",
    }
    if voiceprint_url:
        # Personalised mode: we know exactly which voice is the person being protected.
        # Without this, SAL falls back to identifying whoever speaks first and clearest.
        # "unknown" is reserved by Agora, so never use it as a key.
        sal["sample_urls"] = {"elder": voiceprint_url}

    return {
        "name": agent_name,
        "properties": {
            "channel": channel,
            "token": build_rtc_token(channel, AGENT_UID),
            "agent_rtc_uid": AGENT_UID,
            "remote_rtc_uids": [ELDER_UID],
            # Numeric UIDs: the Android RTC SDK joins with an Int, so keeping string UIDs
            # off avoids a mismatch that shows up as a silent join failure.
            "enable_string_uid": False,
            # If the elder hangs up and leaves, don't keep billing a dead agent.
            "idle_timeout": 30,
            "advanced_features": {
                "enable_sal": True,
                "enable_rtm": True,
            },
            "sal": sal,
            "asr": {
                # Sarvam: Indic-first ASR. Scam calls are Hindi/Tamil/Hinglish,
                # and English-first models mangle exactly the words that matter.
                "vendor": "sarvam",
                "credential_mode": "managed",
                "language": language,
                "params": {},
            },
            "llm": {
                # Our FastAPI guard. Required for SAL recognition, and it's where
                # risk scoring happens.
                "vendor": "custom",
                "url": settings().llm_callback_url,
                "params": {"model": "voiceshield-guard"},
                "system_messages": [],
                # Keep a short window: scam scripts escalate over several turns, so
                # the classifier needs context, but not the whole call.
                "max_history": 16,
            },
            "tts": {
                "vendor": "microsoft",
                "credential_mode": "managed",
                "params": {"voice_name": _voice_for(language)},
            },
        },
    }


def _voice_for(language: str) -> str:
    return {
        "hi-IN": "hi-IN-SwaraNeural",
        "ta-IN": "ta-IN-PallaviNeural",
        "en-IN": "en-IN-NeerjaNeural",
    }.get(language, "hi-IN-SwaraNeural")


async def start_agent(
    channel: str,
    agent_name: str,
    language: str | None = None,
    voiceprint_url: str | None = None,
) -> str:
    """Start the guard agent. Returns the agent id used by /speak and /leave."""
    body = _agent_payload(
        channel, agent_name, language or settings().default_language, voiceprint_url
    )
    data = await _post("/join", body)
    agent_id = data.get("agent_id", "")
    log.info("guard agent %s watching channel %s", agent_id, channel)
    return agent_id


async def stop_agent(agent_id: str, channel: str | None = None) -> None:
    await _post(f"/agents/{agent_id}/leave", channel=channel)


async def speak(
    agent_id: str,
    text: str,
    *,
    channel: str | None = None,
    interruptable: bool = False,
) -> None:
    """Barge in with a spoken warning.

    priority=INTERRUPT makes the agent abandon whatever it is doing and say this
    immediately. interruptable=False means the scammer talking over it cannot
    suppress it -- which is the entire point, because live social engineering works
    by never letting the victim stop and think.

    Max 512 bytes of text. Devanagari/Tamil are 3 bytes/char in UTF-8, so a Hindi
    warning gets ~170 characters. Keep warnings short anyway; panicking people do
    not parse long sentences.
    """
    await _post(
        f"/agents/{agent_id}/speak",
        {"text": text[:500], "priority": "INTERRUPT", "interruptable": interruptable},
        channel=channel,
    )


async def history(agent_id: str, channel: str | None = None) -> dict[str, Any]:
    """Short-term transcript. VoiceShield uses this as evidence for a cybercrime report."""
    return await _get(f"/agents/{agent_id}/history", channel=channel)


async def turns(agent_id: str, channel: str | None = None) -> dict[str, Any]:
    """Turn-level metrics. Drives the latency HUD in the demo."""
    return await _get(f"/agents/{agent_id}/turns", channel=channel)


async def credentials_ok() -> tuple[bool, str]:
    """Read-only probe used by /health, mirroring SETUP.md step 4."""
    mode = auth_mode()
    if mode == "none":
        return False, "no usable credentials: need App ID + App Certificate at minimum"
    try:
        await _get("/agents?limit=1")
        return True, f"ok (auth: {mode})"
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            hint = (
                "customer id/secret wrong"
                if mode == "basic"
                else "app id/certificate wrong, or token auth rejected for this call"
            )
            return False, f"{code} ({mode}): {hint} — {e.response.text[:160]}"
        if code == 404:
            return False, f"404 ({mode}): Conversational AI not enabled on this project"
        return False, f"{code} ({mode}): {e.response.text[:200]}"
    except Exception as e:  # network, DNS, missing config
        return False, f"{type(e).__name__}: {e}"
