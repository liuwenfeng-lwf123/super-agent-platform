from langchain_openai import ChatOpenAI
from app.config import settings
from app.models.schemas import ModelConfig
from typing import Optional
import os


class LLMProvider:
    def __init__(self):
        self._models: dict[str, ModelConfig] = {}
        self._default_model = settings.default_model
        self._init_default_models()

    def _init_default_models(self):
        self._models["Qwen/Qwen3-Coder-480B-A35B-Instruct"] = ModelConfig(
            name="Qwen/Qwen3-Coder-480B-A35B-Instruct",
            display_name="Qwen3-Coder-480B (Fast)",
            model="Qwen/Qwen3-Coder-480B-A35B-Instruct",
        )

        self._models["Qwen/Qwen3-235B-A22B"] = ModelConfig(
            name="Qwen/Qwen3-235B-A22B",
            display_name="Qwen3-235B",
            model="Qwen/Qwen3-235B-A22B",
        )

        self._models["Qwen/Qwen3.5-397B-A17B"] = ModelConfig(
            name="Qwen/Qwen3.5-397B-A17B",
            display_name="Qwen3.5-397B (Free)",
            model="Qwen/Qwen3.5-397B-A17B",
        )

        self._models["Qwen/QwQ-32B"] = ModelConfig(
            name="Qwen/QwQ-32B",
            display_name="QwQ-32B Reasoning (Free)",
            model="Qwen/QwQ-32B",
        )

        self._models["deepseek-ai/DeepSeek-V3.2"] = ModelConfig(
            name="deepseek-ai/DeepSeek-V3.2",
            display_name="DeepSeek-V3.2 (Free)",
            model="deepseek-ai/DeepSeek-V3.2",
        )

    def get_chat_model(
        self,
        model_name: Optional[str] = None,
        streaming: bool = True,
    ) -> ChatOpenAI:
        name = model_name or self._default_model
        config = self._models.get(name)

        if not config:
            config = ModelConfig(name=name, display_name=name, model=name)

        api_key = os.getenv(config.api_key_env, settings.openai_api_key)
        base_url = config.base_url if config.base_url else settings.openai_base_url

        return ChatOpenAI(
            model=config.model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=2048,
            streaming=streaming,
            temperature=0.7,
            extra_body={"enable_thinking": False},
        )

    def list_models(self) -> list[ModelConfig]:
        return list(self._models.values())

    def add_model(self, config: ModelConfig):
        self._models[config.name] = config

    def remove_model(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            return True
        return False


llm_provider = LLMProvider()
