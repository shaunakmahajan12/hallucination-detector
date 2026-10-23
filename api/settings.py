"""All API configuration, from HD_* environment variables (HF_TOKEN only for a private Hub model)."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hd.detector import MODEL, PLATT_A, PLATT_B, THRESHOLD


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HD_", env_file=".env", extra="ignore")

    model: str = MODEL  # Hub repo or local checkpoint directory
    hf_token: str | None = Field(default=None, validation_alias=AliasChoices("HF_TOKEN", "HD_HF_TOKEN"))
    threshold: float = THRESHOLD
    platt_a: float = PLATT_A
    platt_b: float = PLATT_B
    max_len: int = 2048
    threads: int | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    # request limits, so one call can't tie up the CPU for minutes
    max_sources: int = 20
    max_source_chars: int = 200_000
    max_answer_chars: int = 20_000
