from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Super Agent Platform"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8001
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:2026"]

    openai_api_key: Optional[str] = None
    openai_base_url: str = "https://api-inference.modelscope.cn/v1"
    modelscope_api_key: Optional[str] = None
    default_model: str = "Qwen/Qwen3-Coder-480B-A35B-Instruct"
    max_tokens: int = 4096

    data_dir: str = "./data"
    memory_dir: str = "./data/memory"
    threads_dir: str = "./data/threads"

    sandbox_enabled: bool = False
    sandbox_timeout: int = 60
    max_sub_agents: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
