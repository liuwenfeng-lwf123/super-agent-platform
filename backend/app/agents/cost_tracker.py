"""

logger = logging.getLogger(__name__)
Token & Cost Tracker — tracks usage per conversation and globally.
"""
import json
import logging
import os
import re
import contextvars
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
COST_LOG = os.path.join(DATA_DIR, "cost_log.json")


_tiktoken_encoder = None
_tiktoken_available: Optional[bool] = None
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')


def _get_tiktoken_encoder():
    """Lazy-load and cache tiktoken encoder (expensive to create)."""
    global _tiktoken_encoder, _tiktoken_available
    if _tiktoken_available is False:
        return None
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        _tiktoken_available = True
        return _tiktoken_encoder
    except ImportError:
        _tiktoken_available = False
        return None


def estimate_tokens(text: str) -> int:
    """Estimate token count from text. More accurate than len(text).
    Uses cached tiktoken encoder if available, falls back to heuristic."""
    if not text:
        return 0
    enc = _get_tiktoken_encoder()
    if enc is not None:
        return len(enc.encode(text))
    # Heuristic estimation
    cjk_chars = len(_CJK_RE.findall(text))
    non_cjk = _CJK_RE.sub('', text)
    words = len(non_cjk.split())
    return int(cjk_chars * 2.5 + words * 1.3)

# Pricing per 1M tokens (USD) — update as needed
# cache_write = cost to create cache, cache_read = cost to read from cache
MODEL_PRICING = {
    "default": {"input": 0.50, "output": 1.50, "cache_write": 0.625, "cache_read": 0.05},
    "Qwen/Qwen3.5-397B-A17B": {"input": 0.50, "output": 1.50, "cache_write": 0.625, "cache_read": 0.05},
    "Qwen/Qwen3-235B-A22B": {"input": 0.40, "output": 1.20, "cache_write": 0.50, "cache_read": 0.04},
    "Qwen/Qwen3-VL-235B-A22B-Instruct": {"input": 0.60, "output": 1.80, "cache_write": 0.75, "cache_read": 0.06},
    "deepseek-chat": {"input": 0.14, "output": 0.28, "cache_write": 0.14, "cache_read": 0.014},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_write": 3.75, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_write": 0.225, "cache_read": 0.075},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
}


class UsageRecord:
    def __init__(self):
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_creation_tokens: int = 0
        self.cache_read_tokens: int = 0
        self.total_tokens: int = 0
        self.cost_usd: float = 0.0
        self.model: str = ""
        self.thread_id: str = ""
        self.mode: str = ""
        self.timestamp: str = ""
        self.tool_calls: int = 0
        self.agents_spawned: int = 0

    def to_dict(self) -> dict:
        d = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "model": self.model,
            "thread_id": self.thread_id,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "tool_calls": self.tool_calls,
            "agents_spawned": self.agents_spawned,
        }
        if self.cache_creation_tokens:
            d["cache_creation_tokens"] = self.cache_creation_tokens
        if self.cache_read_tokens:
            d["cache_read_tokens"] = self.cache_read_tokens
        return d


class CostTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._session_records: list[UsageRecord] = []
        self._current_var: contextvars.ContextVar[Optional[UsageRecord]] = contextvars.ContextVar(
            "cost_tracker_current",
            default=None,
        )
        self._max_budget_usd: float = 0.0  # 0 = unlimited
        self._model_usage: dict[str, dict] = {}  # per-model cumulative
        self._pending_writes: list[dict] = []  # batched disk writes

    def start_tracking(self, model: str = "", thread_id: str = "", mode: str = ""):
        record = UsageRecord()
        record.model = model
        record.thread_id = thread_id
        record.mode = mode
        record.timestamp = datetime.now().isoformat()
        self._current_var.set(record)

    def _get_current_record(self) -> Optional[UsageRecord]:
        return self._current_var.get()

    def set_current_model(self, model: str):
        record = self._get_current_record()
        if record and model:
            record.model = model

    def has_active_tracking(self) -> bool:
        return self._get_current_record() is not None

    def current_input_tokens(self) -> int:
        record = self._get_current_record()
        return record.input_tokens if record else 0

    def set_budget(self, max_usd: float):
        """Set maximum budget for this session. 0 = unlimited."""
        self._max_budget_usd = max_usd

    def is_over_budget(self) -> bool:
        if self._max_budget_usd <= 0:
            return False
        spent = sum(r.cost_usd for r in self._session_records)
        return spent >= self._max_budget_usd

    def get_budget_status(self) -> dict:
        spent = sum(r.cost_usd for r in self._session_records)
        return {
            "spent": round(spent, 6),
            "limit": self._max_budget_usd,
            "remaining": round(max(0, self._max_budget_usd - spent), 6) if self._max_budget_usd > 0 else -1,
            "is_over": self.is_over_budget(),
        }

    def add_tokens(self, input_tokens: int = 0, output_tokens: int = 0):
        record = self._get_current_record()
        if not record:
            return
        with self._lock:
            record.input_tokens += input_tokens
            record.output_tokens += output_tokens
            record.total_tokens = record.input_tokens + record.output_tokens

    def add_tokens_from_api_response(self, response) -> dict:
        """Extract real token usage from LLM API response object.
        Supports OpenAI-compatible and LangChain response formats.
        Also extracts prompt cache tokens when available."""
        usage = {}
        cache_creation = 0
        cache_read = 0

        # LangChain AIMessage with usage_metadata
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            meta = response.usage_metadata
            _get = (lambda k, d=0: meta.get(k, d)) if isinstance(meta, dict) else (lambda k, d=0: getattr(meta, k, d))
            inp = _get('input_tokens', 0)
            out = _get('output_tokens', 0)
            cache_creation = _get('cache_creation_input_tokens', 0) or _get('cache_creation_tokens', 0)
            cache_read = _get('cache_read_input_tokens', 0) or _get('cache_read_tokens', 0)
            usage = {'input_tokens': inp, 'output_tokens': out}
        # LangChain response_metadata
        elif hasattr(response, 'response_metadata') and response.response_metadata:
            rmeta = response.response_metadata
            if 'token_usage' in rmeta:
                tu = rmeta['token_usage']
                usage = {'input_tokens': tu.get('prompt_tokens', 0), 'output_tokens': tu.get('completion_tokens', 0)}
                cache_creation = tu.get('cache_creation_input_tokens', 0)
                cache_read = tu.get('cache_read_input_tokens', 0)
            elif 'usage' in rmeta:
                u = rmeta['usage']
                usage = {'input_tokens': u.get('prompt_tokens', u.get('input_tokens', 0)), 'output_tokens': u.get('completion_tokens', u.get('output_tokens', 0))}
                cache_creation = u.get('cache_creation_input_tokens', 0)
                cache_read = u.get('cache_read_input_tokens', 0)
        # Direct OpenAI-like response
        elif hasattr(response, 'usage') and response.usage:
            u = response.usage
            usage = {'input_tokens': getattr(u, 'prompt_tokens', 0), 'output_tokens': getattr(u, 'completion_tokens', 0)}
            cache_creation = getattr(u, 'cache_creation_input_tokens', 0) or 0
            cache_read = getattr(u, 'cache_read_input_tokens', 0) or 0

        if usage.get('input_tokens') or usage.get('output_tokens'):
            self.add_tokens(usage.get('input_tokens', 0), usage.get('output_tokens', 0))

        if cache_creation or cache_read:
            record = self._get_current_record()
            if record:
                record.cache_creation_tokens += cache_creation
                record.cache_read_tokens += cache_read
            usage['cache_creation_tokens'] = cache_creation
            usage['cache_read_tokens'] = cache_read

        return usage

    def add_tool_call(self):
        record = self._get_current_record()
        if record:
            record.tool_calls += 1

    def add_agent_spawn(self):
        record = self._get_current_record()
        if record:
            record.agents_spawned += 1

    def finish_tracking(self) -> Optional[dict]:
        record = self._get_current_record()
        if not record:
            return None
        # Calculate cost (with prompt cache awareness)
        pricing = MODEL_PRICING.get(record.model, MODEL_PRICING["default"])
        cache_write_price = pricing.get("cache_write", pricing["input"] * 1.25)
        cache_read_price = pricing.get("cache_read", pricing["input"] * 0.1)
        # Non-cache input = total input minus cache tokens
        regular_input = max(0, record.input_tokens - record.cache_creation_tokens - record.cache_read_tokens)
        record.cost_usd = (
            (regular_input / 1_000_000) * pricing["input"]
            + (record.output_tokens / 1_000_000) * pricing["output"]
            + (record.cache_creation_tokens / 1_000_000) * cache_write_price
            + (record.cache_read_tokens / 1_000_000) * cache_read_price
        )
        with self._lock:
            self._session_records.append(record)
            # Update per-model usage
            model = record.model or "unknown"
            if model not in self._model_usage:
                self._model_usage[model] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "requests": 0}
            self._model_usage[model]["input_tokens"] += record.input_tokens
            self._model_usage[model]["output_tokens"] += record.output_tokens
            self._model_usage[model]["cost_usd"] += record.cost_usd
            self._model_usage[model]["requests"] += 1
        self._persist(record, force=True)  # flush on finish
        result = record.to_dict()
        self._current_var.set(None)
        return result

    def get_current(self) -> Optional[dict]:
        record = self._get_current_record()
        if record:
            pricing = MODEL_PRICING.get(record.model, MODEL_PRICING["default"])
            cache_write_price = pricing.get("cache_write", pricing["input"] * 1.25)
            cache_read_price = pricing.get("cache_read", pricing["input"] * 0.1)
            regular_input = max(0, record.input_tokens - record.cache_creation_tokens - record.cache_read_tokens)
            record.cost_usd = (
                (regular_input / 1_000_000) * pricing["input"]
                + (record.output_tokens / 1_000_000) * pricing["output"]
                + (record.cache_creation_tokens / 1_000_000) * cache_write_price
                + (record.cache_read_tokens / 1_000_000) * cache_read_price
            )
            return record.to_dict()
        return None

    def get_session_summary(self) -> dict:
        total_input = sum(r.input_tokens for r in self._session_records)
        total_output = sum(r.output_tokens for r in self._session_records)
        total_cost = sum(r.cost_usd for r in self._session_records)
        return {
            "session_requests": len(self._session_records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_usd": round(total_cost, 4),
            "budget": self.get_budget_status(),
            "model_breakdown": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in self._model_usage.items()},
            "recent": [r.to_dict() for r in self._session_records[-10:]],
        }

    def get_daily_summary(self) -> dict:
        """Load persisted logs and summarize by day."""
        logs = self._load_logs()
        today = date.today().isoformat()
        today_logs = [l for l in logs if l.get("timestamp", "").startswith(today)]
        return {
            "date": today,
            "requests": len(today_logs),
            "total_input_tokens": sum(l.get("input_tokens", 0) for l in today_logs),
            "total_output_tokens": sum(l.get("output_tokens", 0) for l in today_logs),
            "total_cost_usd": round(sum(l.get("cost_usd", 0) for l in today_logs), 4),
        }

    def _persist(self, record: UsageRecord, force: bool = False):
        self._pending_writes.append(record.to_dict())
        # Batch writes: flush every 5 records or when forced
        if len(self._pending_writes) < 5 and not force:
            return
        logs = self._load_logs()
        logs.extend(self._pending_writes)
        self._pending_writes.clear()
        # Keep last 1000
        logs = logs[-1000:]
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            Path(COST_LOG).write_text(json.dumps(logs, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.debug("Suppressed error in cost_tracker: %s", e)

    def get_history(self, limit: int = 100) -> list:
        """Return recent cost history records."""
        logs = self._load_logs()
        session = [r.to_dict() for r in self._session_records]
        all_records = logs + session
        return all_records[-limit:]

    def get_model_breakdown(self) -> dict:
        """Return cost/token breakdown by model."""
        return {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in self._model_usage.items()}

    def _load_logs(self) -> list:
        if os.path.exists(COST_LOG):
            try:
                return json.loads(Path(COST_LOG).read_text())
            except Exception as e:
                logger.debug("Suppressed error in cost_tracker: %s", e)
        return []


cost_tracker = CostTracker()
