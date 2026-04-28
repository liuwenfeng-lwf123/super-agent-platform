import asyncio
import inspect
import json
import logging
from typing import Optional, Any
from pathlib import Path

from langchain_openai import ChatOpenAI
import httpx

from app.config import settings
from app.models.schemas import ModelConfig
import os

logger = logging.getLogger(__name__)


_PROVIDER_DEFAULTS = {
    "openai": {"api_key_env": "OPENAI_API_KEY", "base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"},
    "modelscope": {"api_key_env": "MODELSCOPE_API_KEY", "base_url": os.getenv("MODELSCOPE_BASE_URL") or "https://api-inference.modelscope.cn/v1"},
    "deepseek": {"api_key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1"},
    "openrouter": {"api_key_env": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"},
    "groq": {"api_key_env": "GROQ_API_KEY", "base_url": "https://api.groq.com/openai/v1"},
    "xai": {"api_key_env": "XAI_API_KEY", "base_url": "https://api.x.ai/v1"},
    "perplexity": {"api_key_env": "PERPLEXITY_API_KEY", "base_url": "https://api.perplexity.ai"},
}


class LLMProvider:
    def __init__(self):
        self._models: dict[str, ModelConfig] = {}
        self._default_model = settings.default_model
        self._init_default_models()
        self._builtin_model_names = set(self._models)
        self._load_custom_models()

    _fallback_priority = (
        "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "deepseek-ai/DeepSeek-V3.2",
        "Qwen/Qwen3-235B-A22B",
        "Qwen/Qwen3.5-397B-A17B",
        "Qwen/QwQ-32B",
        "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    )

    _retryable_error_markers = (
        "429",
        "rate limit",
        "quota",
        "too many requests",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
        "connection error",
        "connection reset",
        "timed out",
        "timeout",
        "server disconnected",
    )

    async def aclose_model(self, model: Any) -> None:
        if model is None:
            return
        seen: set[int] = set()
        for attr in (
            "async_client",
            "_async_client",
            "root_async_client",
            "_root_async_client",
            "client",
            "_client",
            "root_client",
            "_root_client",
        ):
            client = getattr(model, attr, None)
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            aclose = getattr(client, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception as e:
                    logger.debug("Suppressed error in provider: %s", e)
                continue
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.debug("Suppressed error in provider: %s", e)

    def close_model(self, model: Any) -> None:
        try:
            asyncio.run(self.aclose_model(model))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self.aclose_model(model))
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def _init_default_models(self):
        modelscope_base = _PROVIDER_DEFAULTS["modelscope"]["base_url"]
        self._models["Qwen/Qwen3-Coder-30B-A3B-Instruct"] = ModelConfig(
            name="Qwen/Qwen3-Coder-30B-A3B-Instruct",
            display_name="Qwen3-Coder-30B (Stable)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        )

        self._models["Qwen/Qwen3-Coder-480B-A35B-Instruct"] = ModelConfig(
            name="Qwen/Qwen3-Coder-480B-A35B-Instruct",
            display_name="Qwen3-Coder-480B (Fast)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/Qwen3-Coder-480B-A35B-Instruct",
        )

        self._models["Qwen/Qwen3-235B-A22B"] = ModelConfig(
            name="Qwen/Qwen3-235B-A22B",
            display_name="Qwen3-235B",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/Qwen3-235B-A22B",
        )

        self._models["Qwen/Qwen3.5-397B-A17B"] = ModelConfig(
            name="Qwen/Qwen3.5-397B-A17B",
            display_name="Qwen3.5-397B (Free)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/Qwen3.5-397B-A17B",
        )

        self._models["Qwen/QwQ-32B"] = ModelConfig(
            name="Qwen/QwQ-32B",
            display_name="QwQ-32B Reasoning (Free)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/QwQ-32B",
        )

        self._models["deepseek-ai/DeepSeek-V3.2"] = ModelConfig(
            name="deepseek-ai/DeepSeek-V3.2",
            display_name="DeepSeek-V3.2 (Free)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="deepseek-ai/DeepSeek-V3.2",
        )

        self._models["Qwen/Qwen3-VL-235B-A22B-Instruct"] = ModelConfig(
            name="Qwen/Qwen3-VL-235B-A22B-Instruct",
            display_name="Qwen3-VL-235B (Vision)",
            provider="modelscope",
            api_key_env="MODELSCOPE_API_KEY",
            base_url=modelscope_base,
            model="Qwen/Qwen3-VL-235B-A22B-Instruct",
        )

    def _custom_models_path(self) -> Path:
        return Path(settings.data_dir) / "models.json"

    def _load_custom_models(self):
        path = self._custom_models_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in provider: %s", e)
            return
        for item in data if isinstance(data, list) else []:
            try:
                config = self._apply_provider_defaults(ModelConfig(**item))
            except Exception as e:
                logger.debug("Suppressed error in provider: %s", e)
                continue
            self._models[config.name] = config

    def _save_custom_models(self):
        path = self._custom_models_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        custom_models = [
            model.model_dump()
            for name, model in sorted(self._models.items())
            if name not in self._builtin_model_names
        ]
        path.write_text(json.dumps(custom_models, ensure_ascii=False, indent=2), encoding="utf-8")

    # Claude Code shorthand aliases (subagent configs use these).
    # When an Anthropic API key is not set, fall back to the default model.
    CLAUDE_SHORTHAND = {"haiku", "sonnet", "opus", "inherit"}

    def normalize_model_name(self, model_name: Optional[str] = None) -> str:
        name = model_name or self._default_model
        if name in self.CLAUDE_SHORTHAND and not os.getenv("ANTHROPIC_API_KEY"):
            return self._default_model
        return name

    def _infer_provider(self, name: str, base_url: str = "") -> str:
        lower_name = (name or "").lower()
        lower_base = (base_url or "").lower()
        if lower_name.startswith("openrouter:") or "openrouter.ai" in lower_base:
            return "openrouter"
        if lower_name.startswith("groq:") or "api.groq.com" in lower_base:
            return "groq"
        if lower_name.startswith("xai:") or lower_name.startswith("grok") or "api.x.ai" in lower_base:
            return "xai"
        if lower_name.startswith("perplexity:") or "api.perplexity.ai" in lower_base:
            return "perplexity"
        if lower_name.startswith("deepseek:") or lower_name.startswith("deepseek-chat") or lower_name.startswith("deepseek-reasoner") or "api.deepseek.com" in lower_base:
            return "deepseek"
        if lower_name.startswith("modelscope:") or lower_name.startswith("qwen/") or lower_name.startswith("deepseek-ai/") or "modelscope.cn" in lower_base:
            return "modelscope"
        return "openai"

    def _apply_provider_defaults(self, config: ModelConfig) -> ModelConfig:
        resolved = config.model_copy(deep=True)
        provider = (resolved.provider or "").strip().lower()
        if not provider or provider == "openai":
            provider = self._infer_provider(resolved.name or resolved.model, resolved.base_url)
        defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["openai"])
        resolved.provider = provider
        if not resolved.api_key_env or (resolved.api_key_env == "OPENAI_API_KEY" and provider != "openai"):
            resolved.api_key_env = defaults["api_key_env"]
        if not resolved.base_url:
            resolved.base_url = defaults["base_url"]
        if not resolved.model:
            resolved.model = resolved.name
        return resolved

    def resolve_model_config(
        self,
        model_name: Optional[str] = None,
    ) -> ModelConfig:
        name = self.normalize_model_name(model_name)
        config = self._models.get(name)
        if config:
            return self._apply_provider_defaults(config)
        provider_hint, sep, model = name.partition(":")
        if sep and provider_hint.lower() in _PROVIDER_DEFAULTS:
            return self._apply_provider_defaults(ModelConfig(
                name=name,
                display_name=model or name,
                provider=provider_hint.lower(),
                model=model or name,
            ))
        return self._apply_provider_defaults(ModelConfig(name=name, display_name=name, model=name))

    def list_supported_providers(self) -> list[dict]:
        return [
            {"name": name, "api_key_env": meta["api_key_env"], "base_url": meta["base_url"]}
            for name, meta in sorted(_PROVIDER_DEFAULTS.items())
        ]

    def get_fallback_model_names(self, model_name: Optional[str] = None) -> list[str]:
        requested = self.normalize_model_name(model_name)
        requested_config = self.resolve_model_config(requested)
        candidates: list[str] = []

        def _add(name: str | None, require_key: bool = True) -> None:
            normalized = self.normalize_model_name(name)
            if not normalized or normalized in candidates:
                return
            if require_key and not self.has_api_key(normalized):
                return
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        _add(requested, require_key=False)
        _add(self._default_model)
        if requested_config.provider != "openai":
            for model in self.list_models():
                if model.provider == requested_config.provider:
                    _add(model.name)
        for name in self._fallback_priority:
            _add(name)
        if requested_config.provider != "openai":
            for model in self.list_models():
                _add(model.name)
        return candidates

    def should_retry_with_fallback(self, error: Exception | str) -> bool:
        message = str(error).lower()
        return any(marker in message for marker in self._retryable_error_markers)

    def get_api_key_for_model(self, model_name: Optional[str] = None) -> str | None:
        config = self.resolve_model_config(model_name)
        try:
            from app.models.credentials import credential_store
            pool_key = credential_store.get_api_key(config.provider)
        except Exception as e:
            logger.debug("Suppressed error in provider: %s", e)
            pool_key = None
        env_key = os.getenv(config.api_key_env)
        if pool_key or env_key:
            return pool_key or env_key
        if config.provider == "openai" or config.api_key_env == "OPENAI_API_KEY":
            return settings.openai_api_key
        if config.provider == "modelscope" or config.api_key_env == "MODELSCOPE_API_KEY":
            return settings.modelscope_api_key
        return None

    def get_api_key_source_for_model(self, model_name: Optional[str] = None) -> str:
        config = self.resolve_model_config(model_name)
        try:
            from app.models.credentials import credential_store
            keys = credential_store.list_key_pool(config.provider)
            if any(not key.get("is_disabled") for key in keys):
                return "credential_store"
            oauth_providers = credential_store.list_oauth_providers()
            if any(provider.get("provider") == config.provider and provider.get("has_token") for provider in oauth_providers):
                return "oauth"
        except Exception as e:
            logger.debug("Suppressed error in provider: %s", e)
        if os.getenv(config.api_key_env):
            return "environment"
        if (config.provider == "openai" or config.api_key_env == "OPENAI_API_KEY") and self._is_valid_api_key(settings.openai_api_key):
            return "settings"
        if (config.provider == "modelscope" or config.api_key_env == "MODELSCOPE_API_KEY") and self._is_valid_api_key(settings.modelscope_api_key):
            return "settings"
        return "none"

    def _is_valid_api_key(self, api_key: str | None) -> bool:
        return bool(api_key and api_key != "sk-your-api-key-here")

    def has_api_key(self, model_name: Optional[str] = None) -> bool:
        return self._is_valid_api_key(self.get_api_key_for_model(model_name))

    def has_any_api_key(self) -> bool:
        if self._is_valid_api_key(settings.openai_api_key) or self._is_valid_api_key(os.getenv("ANTHROPIC_API_KEY")):
            return True
        try:
            from app.models.credentials import credential_store
        except Exception as e:
            logger.debug("Suppressed error in provider: %s", e)
            credential_store = None
        for provider in self.list_supported_providers():
            if credential_store is not None and self._is_valid_api_key(credential_store.get_api_key(provider["name"])):
                return True
            env_name = provider.get("api_key_env")
            if env_name and self._is_valid_api_key(os.getenv(env_name)):
                return True
        return False

    def _build_tracing_callbacks(self) -> list:
        """Build observability callbacks (Langfuse, etc.) based on config."""
        callbacks = []
        try:
            langfuse_enabled = os.getenv("LANGFUSE_TRACING", "").strip().lower() in ("true", "1", "yes")
            if langfuse_enabled:
                langfuse_public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
                langfuse_secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
                if langfuse_public and langfuse_secret:
                    from langfuse.callback import CallbackHandler as LangfuseHandler
                    langfuse_base = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").strip()
                    handler = LangfuseHandler(
                        public_key=langfuse_public,
                        secret_key=langfuse_secret,
                        host=langfuse_base,
                    )
                    callbacks.append(handler)
                    logger.info("Langfuse tracing enabled")
                else:
                    logger.warning("LANGFUSE_TRACING=true but missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY")
        except ImportError:
            logger.warning("langfuse package not installed — tracing disabled. Run: pip install langfuse")
        except Exception as exc:
            logger.warning("Langfuse callback init failed: %s", exc)
        return callbacks

    # Models that support and benefit from thinking/reasoning mode
    _THINKING_MODELS = {"Qwen/QwQ-32B", "qwq-32b", "deepseek-r1"}

    def _should_enable_thinking(self, config) -> bool:
        """Enable thinking for reasoning-capable models (e.g. QwQ-32B)."""
        model_lower = (config.model or "").lower()
        return any(m.lower() in model_lower for m in self._THINKING_MODELS)

    def get_chat_model(
        self,
        model_name: Optional[str] = None,
        streaming: bool = True,
    ) -> ChatOpenAI:
        name = self.normalize_model_name(model_name)

        config = self.resolve_model_config(name)

        api_key = self.get_api_key_for_model(name)
        base_url = config.base_url if config.base_url else settings.openai_base_url

        kwargs = dict(
            model=config.model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=2048,
            streaming=streaming,
            temperature=0.7,
            extra_body={"enable_thinking": self._should_enable_thinking(config)},
        )
        if config.provider == "openai" or config.api_key_env == "OPENAI_API_KEY":
            kwargs["http_async_client"] = httpx.AsyncClient(trust_env=False, timeout=60.0)
            kwargs["http_client"] = httpx.Client(trust_env=False, timeout=60.0)
        if streaming:
            kwargs["stream_usage"] = True

        # Attach tracing callbacks (Langfuse, etc.)
        callbacks = self._build_tracing_callbacks()
        if callbacks:
            kwargs["callbacks"] = callbacks

        return ChatOpenAI(**kwargs)

    def list_models(self) -> list[ModelConfig]:
        resolved_models = [self._apply_provider_defaults(model) for model in self._models.values()]
        resolved_models.sort(key=lambda model: (0 if model.name == self._default_model else 1, model.display_name.lower(), model.name.lower()))
        return resolved_models

    def add_model(self, config: ModelConfig):
        self._models[config.name] = self._apply_provider_defaults(config)
        self._save_custom_models()

    def remove_model(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            self._save_custom_models()
            return True
        return False


llm_provider = LLMProvider()
