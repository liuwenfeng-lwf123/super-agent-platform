"""
ML-based safety classifier (yoloClassifier).

Classifies tool calls into risk levels using feature extraction + weighted scoring.
No external ML library needed — uses a lightweight logistic-like model with
hand-tuned features that can be upgraded to a trained model later.

Risk levels:
  - safe        (score < 0.3)  → auto-approve
  - low_risk    (0.3 ≤ score < 0.5) → approve with logging
  - medium_risk (0.5 ≤ score < 0.7) → require confirmation
  - high_risk   (0.7 ≤ score < 0.9) → block unless explicitly allowed
  - critical    (score ≥ 0.9) → always block
"""
import math
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClassificationResult:
    tool_name: str
    risk_level: str          # safe / low_risk / medium_risk / high_risk / critical
    risk_score: float        # 0.0 ~ 1.0
    features: dict           # extracted feature vector
    reasons: list[str]       # human-readable explanations
    auto_approve: bool       # whether to auto-approve
    requires_confirm: bool   # whether to ask user


# --- Feature weights (can be replaced with trained weights) ---
FEATURE_WEIGHTS = {
    # Tool-level features
    "is_destructive":        0.35,
    "is_write_op":           0.20,
    "is_exec_op":            0.30,
    "is_network_op":         0.10,
    "is_system_op":          0.25,
    "is_read_only":         -0.80,
    "is_concurrency_safe":  -0.20,
    # Argument-level features
    "has_path_traversal":    0.50,
    "has_sensitive_path":    0.45,
    "has_url":               0.02,
    "has_shell_metachar":    0.20,
    "has_long_content":      0.02,
    "arg_complexity":        0.05,
    # Context features
    "is_first_call":        -0.05,
    "repeated_tool":        -0.05,
    "high_turn_count":       0.05,
}

BIAS = -0.30  # base risk (negative = default safe)

# --- Tool category risk priors ---
TOOL_RISK_PRIOR = {
    "execute_bash":     0.60,
    "execute_python":   0.35,
    "execute_javascript": 0.30,
    "write_file":       0.30,
    "git_command":       0.25,
    "http_request":     0.20,
    "open_app":         0.25,
    "open_url":         0.15,
    "clipboard_write":  0.20,
    "notify":           0.10,
    "create_tool":      0.40,
    "remove_custom_tool": 0.35,
    "local_execute_bash": 0.70,
    "local_write_file":   0.45,
    "local_execute_python": 0.50,
    "local_open_app":     0.35,
}

SENSITIVE_PATHS = re.compile(
    r'(/etc/|/usr/|/var/|/boot/|/sys/|/proc/|~/.ssh|~/.gnupg|~/.aws|\.env|/root/|/home/[^/]+/\.)',
    re.IGNORECASE,
)
PATH_TRAVERSAL = re.compile(r'\.\./|\.\.\\')
SHELL_META = re.compile(r'[;&|`$(){}]')
URL_PATTERN = re.compile(r'https?://')


def _sigmoid(x: float) -> float:
    """Clamp + sigmoid for score normalization."""
    x = max(-10.0, min(10.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _extract_features(
    tool_name: str,
    args: dict,
    context: Optional[dict] = None,
) -> tuple[dict[str, float], list[str]]:
    """Extract feature vector from a tool call."""
    features: dict[str, float] = {}
    reasons: list[str] = []
    ctx = context or {}

    # --- Tool-level features ---
    from app.agents.tool_runtime import get_tool_metadata
    meta = get_tool_metadata(tool_name)

    features["is_destructive"] = 1.0 if meta.is_destructive else 0.0
    features["is_read_only"] = 1.0 if meta.is_read_only else 0.0
    features["is_concurrency_safe"] = 1.0 if meta.is_concurrency_safe else 0.0

    features["is_write_op"] = 1.0 if tool_name in {
        "write_file", "local_write_file", "clipboard_write",
    } else 0.0

    features["is_exec_op"] = 1.0 if tool_name in {
        "execute_bash", "execute_python", "execute_javascript",
        "local_execute_bash", "local_execute_python",
    } else 0.0

    features["is_network_op"] = 1.0 if tool_name in {
        "http_request", "web_fetch", "web_search", "open_url",
    } else 0.0

    features["is_system_op"] = 1.0 if tool_name in {
        "open_app", "notify", "screenshot", "clipboard_read", "clipboard_write",
        "system_info", "local_open_app", "local_get_system_info",
    } else 0.0

    if features["is_destructive"]:
        reasons.append(f"Tool '{tool_name}' is marked destructive")
    if features["is_exec_op"]:
        reasons.append(f"Tool '{tool_name}' executes code")

    # --- Argument-level features ---
    args_str = str(args).lower()

    features["has_path_traversal"] = 1.0 if PATH_TRAVERSAL.search(args_str) else 0.0
    if features["has_path_traversal"]:
        reasons.append("Path traversal detected in arguments")

    features["has_sensitive_path"] = 1.0 if SENSITIVE_PATHS.search(args_str) else 0.0
    if features["has_sensitive_path"]:
        reasons.append("Sensitive path in arguments")

    features["has_url"] = 1.0 if URL_PATTERN.search(args_str) else 0.0
    features["has_shell_metachar"] = 1.0 if SHELL_META.search(args_str) else 0.0
    if features["has_shell_metachar"] and features["is_exec_op"]:
        reasons.append("Shell metacharacters in exec arguments")

    features["has_long_content"] = 1.0 if len(args_str) > 2000 else 0.0
    features["arg_complexity"] = min(1.0, len(args) / 10.0)

    # --- Context features ---
    turn_count = ctx.get("turn_count", 0)
    features["is_first_call"] = 1.0 if turn_count == 0 else 0.0
    features["repeated_tool"] = 1.0 if ctx.get("prev_tool") == tool_name else 0.0
    features["high_turn_count"] = 1.0 if turn_count > 20 else 0.0

    # --- Bash-specific deep analysis ---
    if tool_name in ("execute_bash", "local_execute_bash"):
        command = args.get("command", args.get("code", ""))
        if command:
            from app.agents.orchestrator import check_bash_safety
            bash_result = check_bash_safety(command)
            if not bash_result["safe"]:
                features["is_destructive"] = 1.0
                reasons.extend(bash_result.get("warnings", []))

    return features, reasons


def classify_tool_call(
    tool_name: str,
    args: dict,
    context: Optional[dict] = None,
) -> ClassificationResult:
    """Classify a tool call by risk level."""
    features, reasons = _extract_features(tool_name, args, context)

    # Compute weighted score
    raw_score = BIAS + TOOL_RISK_PRIOR.get(tool_name, 0.0)
    for feat_name, weight in FEATURE_WEIGHTS.items():
        raw_score += features.get(feat_name, 0.0) * weight

    risk_score = round(_sigmoid(raw_score), 4)

    # Map to level
    if risk_score >= 0.9:
        level = "critical"
    elif risk_score >= 0.7:
        level = "high_risk"
    elif risk_score >= 0.5:
        level = "medium_risk"
    elif risk_score >= 0.3:
        level = "low_risk"
    else:
        level = "safe"

    return ClassificationResult(
        tool_name=tool_name,
        risk_level=level,
        risk_score=risk_score,
        features=features,
        reasons=reasons,
        auto_approve=level in ("safe", "low_risk"),
        requires_confirm=level in ("medium_risk", "high_risk"),
    )


def should_auto_approve(tool_name: str, args: dict, context: Optional[dict] = None) -> bool:
    """Quick check: can this tool call be auto-approved?"""
    result = classify_tool_call(tool_name, args, context)
    return result.auto_approve


def get_risk_summary(tool_name: str, args: dict, context: Optional[dict] = None) -> dict:
    """Return a serializable risk assessment summary."""
    result = classify_tool_call(tool_name, args, context)
    return {
        "tool_name": result.tool_name,
        "risk_level": result.risk_level,
        "risk_score": result.risk_score,
        "auto_approve": result.auto_approve,
        "requires_confirm": result.requires_confirm,
        "reasons": result.reasons,
        "features": {k: v for k, v in result.features.items() if v != 0.0},
    }
