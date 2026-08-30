"""Configuration. Everything is env-driven so the demo runs with zero edits."""
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # --- listeners ---------------------------------------------------
    # 514 is privileged. Default to 5514 so the demo runs without sudo.
    syslog_udp_port: int = _int("SYSLOG_UDP_PORT", 5514)
    syslog_tcp_port: int = _int("SYSLOG_TCP_PORT", 5514)
    syslog_bind: str = os.getenv("SYSLOG_BIND", "0.0.0.0")

    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = _int("API_PORT", 8080)

    db_path: str = os.getenv("DB_PATH", "logai.db")

    # --- AI provider -------------------------------------------------
    # one of: ollama | vllm | openrouter | openai | anthropic | offline | none
    #   offline = built-in deterministic fixture, no model or network needed
    provider: str = os.getenv("LOGAI_PROVIDER", "ollama").strip().lower()
    model: str = os.getenv("LOGAI_MODEL", "qwen2.5:14b")
    api_key: str = os.getenv("LOGAI_API_KEY", "")
    base_url: str = os.getenv("LOGAI_BASE_URL", "")
    request_timeout: int = _int("LOGAI_TIMEOUT", 120)
    max_tokens: int = _int("LOGAI_MAX_TOKENS", 1400)

    # --- agent behaviour ---------------------------------------------
    # How many clusters the model is allowed to look at per run.
    # Deterministic ranking decides WHICH ones. This keeps cost bounded.
    analyze_top_n: int = _int("ANALYZE_TOP_N", 5)
    # Sample events per cluster handed to the model (with their IDs, for citation)
    samples_per_cluster: int = _int("SAMPLES_PER_CLUSTER", 4)
    auto_analyze_seconds: int = _int("AUTO_ANALYZE_SECONDS", 0)  # 0 = manual only

    # --- alerting ----------------------------------------------------
    alert_score_threshold: float = float(os.getenv("ALERT_SCORE_THRESHOLD", "80"))

    # Default endpoints per provider when LOGAI_BASE_URL is unset.
    _defaults: dict = field(default_factory=lambda: {
        "ollama": "http://localhost:11434/v1",
        "vllm": "http://localhost:8000/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
    })

    def resolved_base_url(self) -> str:
        return self.base_url or self._defaults.get(self.provider, "")

    def ai_enabled(self) -> bool:
        if self.provider in ("none", ""):
            return False
        # offline is a built-in fixture; local providers need no key
        if self.provider in ("offline", "ollama", "vllm"):
            return True
        return bool(self.api_key)


settings = Settings()
