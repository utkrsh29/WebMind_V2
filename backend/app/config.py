import os
import sys
from typing import List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # API Keys & Provider
    LLM_API_KEY: str = Field(
        default="",
        description="The secret API key for LLM provider access"
    )
    LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = Field(
        default="gemini",
        description="Active LLM provider via LiteLLM"
    )

    # Models
    FAST_MODEL: str = Field(
        default="gemini/gemini-flash-lite-latest",
        description="Low latency model for normalization and fast actions"
    )
    GENERAL_MODEL: str = Field(
        default="gemini/gemma-4-26b-a4b-it",
        description="Balanced model for standard browser interactions"
    )
    REASONING_MODEL: str = Field(
        default="gemini/gemma-4-26b-a4b-it",
        description="Strongest reasoning model for complex or escalated tasks"
    )
    VLM_MODEL: str = Field(
        default="gemini/gemini-flash-lite-latest",
        description="Vision-language model for screenshot analysis"
    )

    # Database
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./webmind.db",
        description="Database connection URL (PostgreSQL+asyncpg or SQLite for tests)"
    )

    # Agent Limits & Execution
    MAX_STEPS_PER_RUN: int = Field(default=40, ge=1, le=100)
    MAX_RETRIES_PER_STEP: int = Field(default=2, ge=0, le=10)
    HEADLESS: bool = Field(default=True)

    # Server
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)

    # Safety Domain Filters
    DOMAIN_ALLOW_LIST: List[str] = Field(default_factory=list)
    DOMAIN_DENY_LIST: List[str] = Field(
        default_factory=lambda: [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "169.254.169.254",
            "*.internal",
            "*.local",
        ]
    )

    def validate_api_key_or_raise(self) -> None:
        """Fails startup with an actionable error if LLM_API_KEY is not configured."""
        if not self.LLM_API_KEY or not self.LLM_API_KEY.strip():
            msg = (
                "\n" + "=" * 70 + "\n"
                "CRITICAL ERROR: LLM_API_KEY is missing!\n"
                "WebMind requires an LLM API key to operate.\n"
                "Please configure 'LLM_API_KEY' in your .env file or environment.\n"
                f"Selected Provider: {self.LLM_PROVIDER}\n"
                "=" * 70 + "\n"
            )
            print(msg, file=sys.stderr)
            raise ValueError("LLM_API_KEY is not configured.")

    def setup_litellm_env(self) -> None:
        """Injects API key into standard environment variables for LiteLLM."""
        if not self.LLM_API_KEY:
            return

        key = self.LLM_API_KEY.strip()
        if self.LLM_PROVIDER == "gemini":
            os.environ["GEMINI_API_KEY"] = key
        elif self.LLM_PROVIDER == "openai":
            os.environ["OPENAI_API_KEY"] = key
        elif self.LLM_PROVIDER == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = key


# Singleton settings instance
settings = Settings()
