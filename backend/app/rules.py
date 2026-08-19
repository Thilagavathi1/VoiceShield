"""Deterministic scam scorer — the free, always-available floor under the LLM.

Why this exists: an LLM classifier is the better judge, but it can be rate-limited,
out of quota, or simply down. Without a floor, those minutes are the minutes the
elder is unprotected. This layer costs nothing, adds no latency, needs no network,
and catches the blunt end of the distribution: OTP requests, remote-access installs,
digital-arrest threats, receive-money-with-your-PIN.

It is a floor, NOT a replacement. It cannot read tone, cannot follow a slow-burn
manipulation across ten turns, and will never catch a scam phrased in words it does
not know. Every number here is a hand-set heuristic, so treat rules-only mode as
degraded protection and say so in the UI.

Design rule that carries most of the accuracy: attack signals only count when the
UNKNOWN speaker produces them. The elder saying "OTP" is a confused person; the
stranger demanding one is an attack. This mirrors what Agora's Selective Attention
Locking gives us for free.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- signal vocabulary -------------------------------------------------------------
# Romanised Hinglish, Devanagari, and Tamil, because that is how these calls actually
# sound. Matching is substring-based on a normalised string, so partial words are
# intentional ("otp" matches "otp?" and "OTP bhejiye").

CREDENTIAL_REQUEST = [
    "otp", "ओटीपी", "ஓடிபி",
    "cvv", "pin number", "upi pin", "atm pin", "yupiai pin",
    "पिन", "பின்",
    "card number", "card ka number", "कार्ड नंबर",
    "card details", "card detail", "debit card", "credit card number",
    "password", "पासवर्ड", "கடவுச்சொல்",
    "net banking", "netbanking",
]

REMOTE_ACCESS = [
    "anydesk", "any desk", "teamviewer", "team viewer", "quicksupport",
    "quick support", "screen share", "screen sharing", "share your screen",
    "remote access", "take remote", "remote control", "install this app",
    "install the app", "app install kar", "स्क्रीन शेयर", "रिमोट",
]

RECEIVE_MONEY_INVERSION = [
    "scan the qr", "qr code scan", "qr scan", "scan karke", "qr कोड",
    "approve the request", "request approve", "accept the request",
    "to receive", "paisa aa jayega", "पैसा आ जाएगा", "receive karne ke liye",
    "enter your upi pin to receive", "pin daaliye", "पिन डालिए",
]

SECRECY = [
    "don't tell", "do not tell", "dont tell", "kisi ko na batao", "kisi ko mat batao",
    "किसी को न बताएं", "किसी को मत बताना", "yaarukkum sollakoodadhu",
    "யாருக்கும் சொல்ல", "don't hang up", "do not hang up", "call disconnect nahi",
    "call cut nahi", "कॉल काट", "stay on the line", "line par rahiye",
    "cut panna koodadhu", "veetla yaarukkum",
]

AUTHORITY_THREAT = [
    "cbi", "सीबीआई", "narcotics", "customs", "कस्टम", "enforcement directorate",
    "digital arrest", "डिजिटल अरेस्ट", "under arrest", "arrest warrant",
    "गिरफ्तार", "police station", "पुलिस", "போலீஸ்", "cyber cell", "cyber crime",
    "fir", "एफआईआर", "court", "summons",
]

URGENCY = [
    "immediately", "turant", "तुरंत", "right now", "abhi", "अभी",
    "within 2 hours", "do ghante", "aaj raat", "आज रात", "innaiku raathiri",
    "last warning", "final notice", "time nahi hai", "समय नहीं",
]

CONSEQUENCE = [
    "block ho jayega", "ब्लॉक हो जाएगा", "account block", "अकाउंट ब्लॉक",
    "connection kat", "कनेक्शन कट", "cut ho jayega", "कट जाएगा",
    "disconnect", "current cut", "cut aagidum", "suspend", "freeze",
    "seize", "जब्त",
]

SAFE_ACCOUNT = [
    "safe account", "rbi account", "आरबीआई", "verification account",
    "transfer everything", "sara paisa", "सारा पैसा", "move your money",
    "shift the funds",
]

KYC = [
    "kyc", "केवाईसी", "kyc expire", "kyc update", "kyc pending", "re-kyc",
    "aadhaar link", "आधार लिंक", "pan update",
]

PRIZE = [
    "you have won", "aapne jeeta", "आपने जीता", "lucky draw", "lottery",
    "लॉटरी", "kbc", "prize money", "inaam", "इनाम", "jackpot",
]

FEE_TO_UNLOCK = [
    "gst", "जीएसटी", "processing fee", "registration fee", "clearance fee",
    "tax jama", "फीस जमा", "pay to release", "pehle jama",
]

UTILITY = [
    "bijli", "बिजली", "electricity", "tneb", "bescom", "gas connection",
    "bill pending", "बिल पेंडिंग", "current bill", "meter",
]

RELATIVE_EMERGENCY = [
    "had an accident", "accident ho gaya", "एक्सीडेंट", "in the hospital",
    "hospital mein hai", "अस्पताल", "your son", "aapke bete", "आपके बेटे",
    "your daughter", "operation", "ऑपरेशन", "phone band hai", "फ़ोन बंद",
]

COURIER = [
    "fedex", "dhl", "courier", "कूरियर", "parcel", "पार्सल", "consignment",
    "package in your name", "aapke naam se parcel",
]

# A delivery agent at the door asking for the delivery OTP is legitimate and very
# common: that code confirms a handover, it moves no money. Without this carve-out the
# floor contributes 55 to every real parcel delivery.
DELIVERY_CONTEXT = [
    "delivery boy", "delivery agent", "delivery ka otp", "delivery otp",
    "parcel de raha", "parcel dene", "at your door", "gate ke bahar",
    "gate pe hoon", "niche hoon", "aapke ghar ke bahar", "courier de raha",
    "डिलीवरी", "delivery complete",
]

MONEY_CREDENTIALS = [
    "cvv", "pin number", "upi pin", "atm pin", "card number", "card details",
    "card detail", "net banking", "netbanking", "password", "पिन",
]

# Explicit de-escalators. A caller who volunteers that they will never ask for an
# OTP is behaving like a real bank; that should pull the score DOWN, not up, even
# though the word "OTP" is present.
REASSURANCE = [
    "we will not ask for any otp", "we never ask for otp", "koi otp nahi maangte",
    "ओटीपी नहीं मां", "otp kekka maatom", "will not ask for your pin",
    "never share your otp", "otp kisi ko na", "do not share that otp",
    "never tell that otp", "not even to me", "otp is secret", "completely secret",
    "ethuvum details kekka maatom", "no need to share",
]


@dataclass
class RuleVerdict:
    risk: int = 0
    pattern: str = "none"
    signals: list[str] = field(default_factory=list)


def _normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace — so matching is forgiving."""
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text)


_ASCII = re.compile(r"^[a-z0-9 '\-]+$")


def _hits(haystack: str, needles: list[str]) -> bool:
    """Match a vocabulary against normalised text.

    ASCII terms match on word boundaries, native-script terms on substring. Without
    the boundary, short tokens quietly wreck accuracy: "fir" fires inside "confirm",
    so a real bank saying "calling to confirm a transaction" scored as a police
    threat. Devanagari and Tamil don't use \\b usefully, hence the split.
    """
    for n in needles:
        if _ASCII.match(n):
            if re.search(rf"\b{re.escape(n)}\b", haystack):
                return True
        elif n in haystack:
            return True
    return False


def score(turns: list[str]) -> RuleVerdict:
    """Score speaker-tagged turns of the form `[elder] ...` / `[unknown] ...`.

    Returns a 0-100 risk with the signals that fired, so the UI and the evidence
    log can show *why* — a warning nobody can justify is a warning nobody trusts.
    """
    unknown_text = _normalise(
        " ".join(t.split("] ", 1)[-1] for t in turns if t.startswith("[unknown]"))
    )
    if not unknown_text:
        return RuleVerdict()

    risk = 0
    signals: list[str] = []
    pattern = "none"

    def add(points: int, label: str, pat: str | None = None) -> None:
        nonlocal risk, pattern
        risk += points
        signals.append(label)
        if pat and pattern == "none":
            pattern = pat

    # Near-conclusive on their own.
    if _hits(unknown_text, CREDENTIAL_REQUEST):
        add(55, "stranger asked for OTP/PIN/card credentials", "credential_request")
    if _hits(unknown_text, REMOTE_ACCESS):
        add(50, "asked to install remote-access software", "tech_support")
    if _hits(unknown_text, RECEIVE_MONEY_INVERSION):
        add(50, "PIN/QR required to *receive* money — always backwards", "army_upi")
    if _hits(unknown_text, SAFE_ACCOUNT):
        add(45, "asked to move money to a 'safe' account", "safe_account")

    # Strong contextual pressure.
    if _hits(unknown_text, SECRECY):
        add(30, "demanded secrecy or told them not to hang up", "digital_arrest")
    if _hits(unknown_text, AUTHORITY_THREAT):
        add(35, "claimed police/CBI/court authority", "digital_arrest")

    # Scenario framings — weaker alone, decisive in combination.
    if _hits(unknown_text, KYC):
        add(20, "KYC expiry or account-block story", "kyc_block")
    if _hits(unknown_text, PRIZE):
        add(20, "prize or lottery win", "lottery")
    if _hits(unknown_text, UTILITY):
        add(15, "utility disconnection story", "utility_cut")
    if _hits(unknown_text, COURIER):
        add(20, "parcel or courier in their name", "courier")
    if _hits(unknown_text, RELATIVE_EMERGENCY):
        add(35, "relative-in-trouble story from an unknown number",
            "relative_emergency")
    if _hits(unknown_text, FEE_TO_UNLOCK):
        add(25, "fee demanded before releasing money")

    # Urgency only counts when paired with a consequence — "come right now" from a
    # neighbour is not a threat; "pay right now or we cut your power" is.
    if _hits(unknown_text, URGENCY) and _hits(unknown_text, CONSEQUENCE):
        add(25, "deadline paired with a threatened consequence")

    if _hits(unknown_text, REASSURANCE):
        add(-45, "caller volunteered they will never ask for OTP/PIN")

    # Doorstep delivery OTP, with no money credential asked for alongside it.
    if _hits(unknown_text, DELIVERY_CONTEXT) and not _hits(
        unknown_text, MONEY_CREDENTIALS
    ):
        add(-45, "doorstep delivery OTP, no banking credential requested")

    return RuleVerdict(
        risk=max(0, min(100, risk)),
        pattern=pattern,
        signals=signals[:6],
    )


# Canned warnings, so rules-only mode can still speak without any model call.
# Deliberately generic: the rule layer knows a pattern, not the specifics.
CANNED_WARNINGS: dict[str, dict[str, str]] = {
    "credential_request": {
        "warning_hi": "रुकिए! OTP या PIN किसी को न बताएं। कोई बैंक यह नहीं मांगता। फ़ोन काट दीजिए।",
        "warning_ta": "நிற்குங்க! OTP அல்லது PIN யாருக்கும் சொல்லாதீங்க. வங்கி இதை கேட்காது. ஃபோனை வையுங்க.",
        "warning_en": "Stop. Never share an OTP or PIN. No bank ever asks for it. Hang up now.",
    },
    "tech_support": {
        "warning_hi": "रुकिए! कोई ऐप इंस्टॉल न करें। यह धोखा है। फ़ोन काट दीजिए।",
        "warning_ta": "நிற்குங்க! எந்த ஆப்பையும் இன்ஸ்டால் பண்ணாதீங்க. இது மோசடி. ஃபோனை வையுங்க.",
        "warning_en": "Stop. Do not install any app they ask for. This is a scam. Hang up.",
    },
    "army_upi": {
        "warning_hi": "रुकिए! पैसा लेने के लिए PIN नहीं डालते। यह धोखा है। फ़ोन काट दीजिए।",
        "warning_ta": "நிற்குங்க! பணம் வாங்க PIN போட வேண்டாம். இது மோசடி. ஃபோனை வையுங்க.",
        "warning_en": "Stop. Receiving money never needs your PIN. This is a scam. Hang up.",
    },
    "safe_account": {
        "warning_hi": "रुकिए! किसी 'सेफ अकाउंट' में पैसा न भेजें। RBI ऐसा नहीं कहता। फ़ोन काट दीजिए।",
        "warning_ta": "நிற்குங்க! 'safe account' ku பணம் அனுப்பாதீங்க. இது மோசடி. ஃபோனை வையுங்க.",
        "warning_en": "Stop. Never move money to a 'safe account'. Hang up and call your bank.",
    },
    "digital_arrest": {
        "warning_hi": "रुकिए! पुलिस फ़ोन पर पैसा नहीं मांगती। यह धोखा है। फ़ोन काट दीजिए और परिवार को बताएं।",
        "warning_ta": "நிற்குங்க! போலீஸ் ஃபோனில் பணம் கேட்க மாட்டாங்க. ஃபோனை வைத்து குடும்பத்துக்கு சொல்லுங்க.",
        "warning_en": "Stop. Police never demand money by phone. Hang up and tell your family.",
    },
}

GENERIC_WARNING = {
    "warning_hi": "सावधान! यह कॉल धोखा हो सकता है। कुछ भी भेजने से पहले फ़ोन काटकर परिवार से बात करें।",
    "warning_ta": "கவனம்! இது மோசடி கால் ஆக இருக்கலாம். எதையும் அனுப்பும் முன் குடும்பத்தில் கேளுங்க.",
    "warning_en": "Careful. This call may be a scam. Hang up and check with your family before paying.",
}


def canned_warnings(pattern: str) -> dict[str, str]:
    return CANNED_WARNINGS.get(pattern, GENERIC_WARNING)
