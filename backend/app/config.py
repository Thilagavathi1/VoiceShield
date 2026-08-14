from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agora_app_id: str = ""
    agora_app_certificate: str = ""
    agora_customer_id: str = ""
    agora_customer_secret: str = ""

    anthropic_api_key: str = ""
    classifier_model: str = "claude-sonnet-5"

    public_base_url: str = ""

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
        return f"{self.public_base_url.rstrip('/')}/v1/chat/completions"

    def missing(self) -> list[str]:
        """Config keys that are required before a real session can start."""
        required = {
            "AGORA_APP_ID": self.agora_app_id,
            "AGORA_APP_CERTIFICATE": self.agora_app_certificate,
            "AGORA_CUSTOMER_ID": self.agora_customer_id,
            "AGORA_CUSTOMER_SECRET": self.agora_customer_secret,
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "PUBLIC_BASE_URL": self.public_base_url,
        }
        return [k for k, v in required.items() if not v]


@lru_cache
def settings() -> Settings:
    return Settings()
