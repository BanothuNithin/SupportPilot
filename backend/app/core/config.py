from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=('.env', '../.env'), extra='ignore')

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    migration_database_url: str | None = None
    worker_database_url: str | None = None
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
    embed_provider: str = 'fake'
    embed_base_url: str = 'http://localhost:11434/v1'
    embed_api_key: str = 'ollama'
    embed_model: str = 'nomic-embed-text'

    rag_top_k: int = 4
    rag_min_score: float = 0.35
    chat_rate_limit_user_per_minute: int = 20
    chat_rate_limit_tenant_per_minute: int = 120
    embed_cache_ttl_seconds: int = 3600
    max_upload_bytes: int = 5 * 1024 * 1024
    history_limit: int = 6

    llm_small_model: str = ''
    llm_fallback_model: str = ''
    llm_fallback_base_url: str = ''
    llm_fallback_api_key: str = ''
    min_confidence: float = 0.5
    integrations_key: str = ""
    github_api_base: str = "https://api.github.com"
    frontend_base_url: str = "http://localhost:3000"
    mcp_timeout_seconds: int = 30
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "supportpilot-docs"
    s3_region: str = "us-east-1"
    job_max_attempts: int = 3
    job_lease_seconds: int = 300
    job_requeue_after_seconds: int = 60
    reconcile_interval_seconds: int = 30
    worker_concurrency: int = 4


settings = Settings()
