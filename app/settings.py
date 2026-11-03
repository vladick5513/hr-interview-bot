from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    BOT_TOKEN: str
    HR_IDS: list[int]

    OPENAI_API_KEY: str
    OPENAI_MODEL: str
    OPENAI_BASE_URL: str | None = None

    HH_CLIENT_ID: str | None = None
    HH_CLIENT_SECRET: str | None = None
    HH_REDIRECT_URI: str | None = None
    HH_USER_AGENT: str

    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str

    LOG_LEVEL: str

    @model_validator(mode="after")
    def _hh_settings_together(self) -> Self:
        values = (self.HH_CLIENT_ID, self.HH_CLIENT_SECRET, self.HH_REDIRECT_URI)
        if any(values) and not all(values):
            raise ValueError("HH_CLIENT_ID, HH_CLIENT_SECRET и HH_REDIRECT_URI задаются вместе")
        return self

    @property
    def hh_enabled(self) -> bool:
        return self.HH_CLIENT_ID is not None

    @property
    def DATABASE_URL_asyncpg(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
