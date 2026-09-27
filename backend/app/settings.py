from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LMS_", extra="ignore")

    database_url: str = "sqlite:///./lms.db"
    public_base_url: str = "http://localhost:8080"
    session_secret: str = "dev-only-change-me"
    ai_encryption_secret: str = ""
    admin_token: str = "dev-admin-change-me"
    lti_private_key_path: str = "./storage/keys/lti-private.pem"
    storage_root: str = "./storage"
    scorm_content_base_url: str = ""
    max_scorm_upload_mb: int = 512
    max_media_upload_mb: int = 2048
    cors_origins: str = "http://localhost:8080"

    @property
    def base_url(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def key_path(self) -> Path:
        return Path(self.lti_private_key_path)

    @property
    def content_base_url(self) -> str:
        return self.scorm_content_base_url.rstrip("/") if self.scorm_content_base_url else self.base_url + "/scorm-content"

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
