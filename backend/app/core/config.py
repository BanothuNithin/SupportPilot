from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=('.env', '../.env'), extra='ignore')

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    jwt_secret: str = "dev-secret-change-me-use-32-chars-min!!"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15
    # LLM: 'fake' (no model needed) or 'openai_compat' (Ollama, OpenAI, any compatible API)
    llm_provider: str = "fake"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "llama3.2"


settings = Settings()
