from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agora_app_id: str = ""
    agora_app_certificate: str = ""
    agora_customer_id: str = ""
    agora_customer_secret: str = ""

    # Gemini powers the classifier: it has a genuinely free tier, which keeps the
    # only paid component of VoiceShield at zero cost. The rule layer in rules.py
    # runs underneath it and needs no key at all.
    gemini_api_key: str = ""
    classifier_model: str = "gemini-3.5-flash-lite"

    public_base_url: str = ""

    # Free-tier request ceiling, used to pace the corpus run so rate limiting does
    # not silently turn an accuracy measurement into a rule-floor measurement.
    eval_requests_per_minute: int = 5

    warn_threshold: int = 70
    notice_threshold: int = 40

    default_language: str = "hi-IN"

    @property
    def agora_api_base(self) -> str:
        return (
            "https://api.agora.io/api/conversational-ai-agent"
            f"/v2/projects/{self.agora_app_id}"
        )

    @property
    def llm_callback_url(self) -> str:
        # Empty rather than a bare path when unset, so /health reports it as missing
        # instead of printing a relative URL that Agora could never call.
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url.rstrip('/')}/v1/chat/completions"

    def missing(self) -> list[str]:
        """Config keys required before a real session can start.

        Customer ID/Secret are deliberately NOT required: the REST API also accepts token
        auth derived from the App ID and App Certificate, and the redesigned Agora console
        does not expose a Customer Secret on every account.
        """
        required = {
            "AGORA_APP_ID": self.agora_app_id,
            "AGORA_APP_CERTIFICATE": self.agora_app_certificate,
            "GEMINI_API_KEY": self.gemini_api_key,
            "PUBLIC_BASE_URL": self.public_base_url,
        }
        return [k for k, v in required.items() if not v]


@lru_cache
def settings() -> Settings:
    return Settings()
