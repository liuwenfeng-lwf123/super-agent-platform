from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Super Agent Platform"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8001
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001", "http://localhost:2026"]

    openai_api_key: Optional[str] = None
    openai_base_url: str = "https://api.openai.com/v1"
    modelscope_api_key: Optional[str] = None
    default_model: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    max_tokens: int = 4096

    data_dir: str = "./data"
    memory_dir: str = "./data/memory"
    threads_dir: str = "./data/threads"
    threads_db_path: str = "./data/threads.db"
    thread_store_backend: str = "json"  # "json" | "sqlite"

    # Auth — set API_SECRET_TOKEN in .env to enable. Empty = no auth (dev mode).
    # RBAC: set AUTH_RBAC_ENABLED=true + configure users in data/users.json
    api_secret_token: Optional[str] = None
    auth_rbac_enabled: bool = False

    sandbox_enabled: bool = False
    sandbox_timeout: int = 60
    sandbox_mode: str = "local"  # "local" | "docker"

    # Langfuse observability
    langfuse_tracing: bool = False
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    max_sub_agents: int = 5
    enable_tool_search: bool = True
    enable_agent_summaries: bool = True
    enable_tool_use_summary: bool = True
    enable_prompt_suggestions: bool = True
    enable_speculation: bool = True
    enable_magic_docs: bool = True
    enable_policy_limits: bool = True

    # Token budget control — disable to save tokens
    enable_gepa_evolution: bool = True
    enable_memory_extraction: bool = True
    enable_sub_agents: bool = True
    enable_intent_classify: bool = True
    daily_token_budget: int = 0  # 0 = unlimited, otherwise max tokens per day

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
