from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./food_ai.db"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-4-hydrogen"
    gemini_embedding_model: str = "gemini-embedding-001"
    chroma_path: str = "./data/chroma"
    app_env: str = "development"
    order_action_ttl_minutes: int = 10
    max_upload_bytes: int = 5 * 1024 * 1024
    admin_username: str = "admin"
    admin_password: str = "admin123!"
    user_username: str = "customer"
    user_password: str = "customer123!"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
