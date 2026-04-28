import ipaddress
import logging
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile
import hashlib
from pathlib import Path
from typing import Any

from app.skills.agentskills_compat import parse_skill_md
from app.agents.evolution import _scan_skill_security


logger = logging.getLogger(__name__)
MAX_SKILL_MD_BYTES = 200_000
MAX_CLAWHUB_BUNDLE_BYTES = 5_000_000
MAX_SUPPORTING_FILE_BYTES = 120_000
MAX_SUPPORTING_FILES = 30
MAX_CLAWHUB_DOWNLOAD_BYTES = 5_000_000
CLAWHUB_DOWNLOAD_RETRIES = 3
CLAWHUB_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CLAWHUB_API_BASE = "https://clawhub.ai/api/v1"
CLAWHUB_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "clawhub_cache"
CLAWHUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
TEXT_FILE_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
}

KNOWN_TOOL_NAMES = [
    "web_search",
    "web_fetch",
    "summarize_url",
    "read_file",
    "write_file",
    "list_files",
    "execute_python",
    "execute_javascript",
    "execute_bash",
    "calculate",
    "remember",
    "knowledge_search",
    "session_search",
    "browser_open",
    "browser_get_state",
    "browser_click",
    "browser_fill",
    "browser_extract_text",
    "browser_run_javascript",
    "screenshot",
    "local_read_file",
    "local_write_file",
    "local_list_files",
    "local_get_system_info",
    "local_open_app",
]

OPENCLAW_TOOL_ALIASES = {
    "browser_screenshot": "screenshot",
    "browser_snapshot": "browser_get_state",
    "browser_type": "browser_fill",
    "browser_evaluate": "browser_run_javascript",
}

FRONTMATTER_KEYS = [
    "name",
    "description",
    "display_name",
    "title",
    "category",
    "keywords",
    "tags",
    "license",
    "homepage",
    "repository",
    "metadata",
    "user-invocable",
    "disable-model-invocation",
    "command-dispatch",
    "command-tool",
    "command-arg-mode",
]


def normalize_github_raw_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return url
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 5 and parts[2] == "blob":
        owner, repo, _, branch = parts[:4]
        rest = "/".join(parts[4:])
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
    return url


def validate_remote_skill_url(url: str) -> str:
    normalized = normalize_github_raw_url(url.strip())
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme != "https":
        raise ValueError("只支持 HTTPS 的 SKILL.md URL")
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("不允许从本机地址导入远程技能")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError("不允许从私有网络地址导入远程技能")
    except ValueError as exc:
        if "不允许" in str(exc):
            raise
    return normalized


def fetch_remote_skill_md(url: str) -> str:
    safe_url = validate_remote_skill_url(url)
    req = urllib.request.Request(safe_url, headers={"User-Agent": "super-agent-openclaw-import/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        content_type = resp.headers.get("content-type", "")
        data = resp.read(MAX_SKILL_MD_BYTES + 1)
    if len(data) > MAX_SKILL_MD_BYTES:
        raise ValueError("SKILL.md 文件太大，已拒绝导入")
    text = data.decode("utf-8", errors="replace")
    if "text/html" in content_type.lower() and "---" not in text[:200]:
        raise ValueError("该 URL 看起来是网页，不是 raw SKILL.md 文件")
    return text


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=_clawhub_headers("application/json"))
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read(1_000_000)
    parsed = json.loads(data.decode("utf-8", errors="replace"))
    return parsed if isinstance(parsed, dict) else {}


def _read_clawhub_token_from_config() -> str:
    candidates = []
    override = os.environ.get("CLAWHUB_CONFIG_PATH") or os.environ.get("CLAWDHUB_CONFIG_PATH")
    if override:
        candidates.append(Path(override).expanduser())
    home = Path.home()
    candidates.extend([
        home / "Library" / "Application Support" / "clawhub" / "config.json",
        home / "Library" / "Application Support" / "clawdhub" / "config.json",
        Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "clawhub" / "config.json",
        Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "clawdhub" / "config.json",
    ])
    for path in candidates:
        try:
            data = json.loads(path.read_text())
            token = str(data.get("token", "")).strip()
            if token:
                return token
        except Exception as e:
            logger.debug("Suppressed error in openclaw_import: %s", e)
            continue
    return ""


def get_clawhub_token() -> str:
    return (os.environ.get("CLAWHUB_TOKEN") or os.environ.get("CLAWDHUB_TOKEN") or "").strip() or _read_clawhub_token_from_config()


def _clawhub_headers(accept: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": "super-agent-clawhub/1.0"}
    if accept:
        headers["Accept"] = accept
    token = get_clawhub_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_clawhub_auth_status() -> dict:
    token = get_clawhub_token()
    if not token:
        return {
            "authenticated": False,
            "message": "未配置 CLAWHUB_TOKEN，下载使用匿名额度，容易触发限流",
        }
    try:
        data = _fetch_json(f"{CLAWHUB_API_BASE}/whoami")
        user = data.get("user") or {}
        return {
            "authenticated": True,
            "handle": user.get("handle"),
            "message": "已使用 ClawHub token 认证",
        }
    except Exception as exc:
        return {
            "authenticated": False,
            "message": f"已检测到 token，但验证失败：{exc}",
        }


def _bundle_cache_path(slug: str, version: str) -> Path:
    digest = hashlib.sha256(f"{slug}:{version}".encode()).hexdigest()[:16]
    safe_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", slug)[:80]
    return CLAWHUB_CACHE_DIR / f"{safe_slug}-{version}-{digest}.zip"


def _read_cached_bundle(slug: str, version: str, allow_stale: bool = False) -> bytes | None:
    path = _bundle_cache_path(slug, version)
    if not path.exists():
        return None
    if not allow_stale and path.stat().st_mtime < time.time() - CLAWHUB_CACHE_TTL_SECONDS:
        return None
    data = path.read_bytes()
    if data:
        return data
    return None


def _write_cached_bundle(slug: str, version: str, data: bytes):
    _bundle_cache_path(slug, version).write_bytes(data)


def search_clawhub_skills(query: str, limit: int = 20) -> dict:
    q = urllib.parse.quote(query.strip())
    url = f"{CLAWHUB_API_BASE}/search?q={q}"
    data = _fetch_json(url)
    results = data.get("results", [])
    if not isinstance(results, list):
        results = []
    return {"results": results[: max(1, min(limit, 50))], "count": min(len(results), max(1, min(limit, 50)))}


def get_clawhub_skill(slug: str) -> dict:
    safe_slug = urllib.parse.quote(slug.strip())
    if not safe_slug:
        raise ValueError("请提供 ClawHub 技能 slug")
    return _fetch_json(f"{CLAWHUB_API_BASE}/skills/{safe_slug}")


def download_clawhub_bundle(slug: str, version: str | None = None) -> tuple[bytes, dict, str]:
    detail = get_clawhub_skill(slug)
    resolved_version = version or (detail.get("latestVersion") or {}).get("version")
    if not resolved_version:
        raise ValueError("无法解析 ClawHub 技能版本")
    cached = _read_cached_bundle(slug, resolved_version)
    if cached:
        return cached, detail, resolved_version
    params = urllib.parse.urlencode({"slug": slug, "version": resolved_version})
    req = urllib.request.Request(f"{CLAWHUB_API_BASE}/download?{params}", headers=_clawhub_headers("application/zip"))
    last_error: Exception | None = None
    for attempt in range(CLAWHUB_DOWNLOAD_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = resp.read(MAX_CLAWHUB_BUNDLE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                stale = _read_cached_bundle(slug, resolved_version, allow_stale=True)
                if stale:
                    return stale, detail, resolved_version
                raise ValueError("ClawHub 下载接口当前限流，请稍后再试；搜索和详情仍可用") from exc
            last_error = exc
        except Exception as exc:
            last_error = exc
        if attempt < CLAWHUB_DOWNLOAD_RETRIES - 1:
            time.sleep(1.5 * (attempt + 1))
    else:
        stale = _read_cached_bundle(slug, resolved_version, allow_stale=True)
        if stale:
            return stale, detail, resolved_version
        raise ValueError(f"ClawHub 下载失败：{last_error}") from last_error
    if len(data) > MAX_CLAWHUB_BUNDLE_BYTES:
        raise ValueError("ClawHub 技能包太大，已拒绝下载")
    _write_cached_bundle(slug, resolved_version, data)
    return data, detail, resolved_version


def extract_clawhub_bundle(bundle: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        skill_member = next((member for member in members if member.filename.split("/")[-1].lower() == "skill.md"), None)
        if not skill_member:
            raise ValueError("ClawHub 技能包中没有找到 SKILL.md")
        if skill_member.file_size > MAX_SKILL_MD_BYTES:
            raise ValueError("SKILL.md 文件太大，已拒绝导入")
        skill_md = archive.read(skill_member).decode("utf-8", errors="replace")
        root_prefix = "/".join(skill_member.filename.split("/")[:-1])
        files: dict[str, str] = {}
        for member in members:
            if member.filename == skill_member.filename:
                continue
            if member.file_size > MAX_SUPPORTING_FILE_BYTES:
                continue
            relative_path = member.filename
            if root_prefix and relative_path.startswith(root_prefix + "/"):
                relative_path = relative_path[len(root_prefix) + 1:]
            suffix = "." + relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
            if suffix not in TEXT_FILE_EXTENSIONS:
                continue
            try:
                files[relative_path] = archive.read(member).decode("utf-8", errors="replace")
            except Exception as e:
                logger.debug("Suppressed error in openclaw_import: %s", e)
                continue
            if len(files) >= MAX_SUPPORTING_FILES:
                break
    return {"skill_md": skill_md, "files": files}


def _parse_inline_frontmatter(content: str) -> tuple[dict, str]:
    match = re.match(r"^---\s+(.*?)\s+---\s*(.*)$", content, re.DOTALL)
    if not match:
        return {}, content
    frontmatter = match.group(1).strip()
    body = match.group(2)
    for key in FRONTMATTER_KEYS:
        frontmatter = re.sub(rf"\s+({re.escape(key)}):", rf"\n\1:", frontmatter)
    return parse_skill_md(f"---\n{frontmatter}\n---\n{body}")


def parse_openclaw_skill_md(content: str) -> tuple[dict, str]:
    frontmatter, body = parse_skill_md(content)
    if not frontmatter and content.lstrip().startswith("--- "):
        frontmatter, body = _parse_inline_frontmatter(content)
    return frontmatter, body


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    return []


def _safe_skill_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip(".-_").lower()
    return name[:80] or "openclaw-skill"


def _display_name(name: str, frontmatter: dict) -> str:
    value = frontmatter.get("display_name") or frontmatter.get("display-name") or frontmatter.get("title")
    if value:
        return str(value).strip()
    return name.replace("_", " ").replace("-", " ").title()


def _metadata(frontmatter: dict) -> dict:
    metadata = frontmatter.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _openclaw_metadata(frontmatter: dict) -> dict:
    metadata = _metadata(frontmatter)
    openclaw = metadata.get("openclaw", {})
    return openclaw if isinstance(openclaw, dict) else {}


def _requirements(frontmatter: dict) -> dict:
    openclaw = _openclaw_metadata(frontmatter)
    requires = frontmatter.get("requires") or openclaw.get("requires") or {}
    return requires if isinstance(requires, dict) else {}


def _infer_tools(text: str, frontmatter: dict) -> list[str]:
    declared = _as_list(frontmatter.get("tools")) or _as_list(frontmatter.get("allowed_tools"))
    found = {OPENCLAW_TOOL_ALIASES.get(tool, tool) for tool in declared}
    lowered = text.lower()
    for tool_name in KNOWN_TOOL_NAMES:
        if tool_name.lower() in lowered:
            found.add(tool_name)
    for alias, actual_tool in OPENCLAW_TOOL_ALIASES.items():
        if alias.lower() in lowered:
            found.add(actual_tool)
    if "截图" in text or "screenshot" in lowered:
        found.add("screenshot")
    return sorted(found)


def _normalize_tool_references(text: str) -> str:
    normalized = text
    for alias, actual_tool in OPENCLAW_TOOL_ALIASES.items():
        normalized = normalized.replace(alias, actual_tool)
    return normalized


def build_openclaw_skill_data(content: str, source_url: str = "") -> dict:
    frontmatter, body = parse_openclaw_skill_md(content)
    raw_name = str(frontmatter.get("name") or frontmatter.get("slug") or "openclaw-skill")
    name = _safe_skill_name(raw_name)
    description = str(frontmatter.get("description") or "OpenClaw imported skill").strip()
    tags = _as_list(frontmatter.get("tags")) or _as_list(frontmatter.get("keywords"))
    requirements = _requirements(frontmatter)
    env_vars = _as_list(requirements.get("env"))
    bins = _as_list(requirements.get("bins"))
    system_prompt = _normalize_tool_references(body.strip() or content.strip())
    tools = _infer_tools(system_prompt, frontmatter)
    skill_data = {
        "name": name,
        "display_name": _display_name(name, frontmatter),
        "description": description,
        "system_prompt": system_prompt[:50_000],
        "tools": tools,
        "allowed_tools": tools or None,
        "category": str(frontmatter.get("category") or "openclaw"),
        "source": "openclaw",
        "source_url": source_url,
        "tags": tags,
        "license": frontmatter.get("license", ""),
        "homepage": frontmatter.get("homepage") or _openclaw_metadata(frontmatter).get("homepage", ""),
        "repository": frontmatter.get("repository", ""),
        "user_invocable": bool(frontmatter.get("user-invocable", True)),
        "disable_model_invocation": bool(frontmatter.get("disable-model-invocation", False)),
        "required_environment_variables": [{"name": item, "required": True} for item in env_vars],
        "required_binaries": bins,
    }
    return skill_data


def build_clawhub_skill_data(slug: str, version: str | None = None) -> tuple[dict, dict]:
    bundle, detail, resolved_version = download_clawhub_bundle(slug, version)
    extracted = extract_clawhub_bundle(bundle)
    source_url = f"https://clawhub.ai/skills/{slug}"
    skill_data = build_openclaw_skill_data(extracted["skill_md"], source_url=source_url)
    skill_meta = detail.get("skill") or {}
    if skill_meta.get("displayName"):
        skill_data["display_name"] = skill_meta.get("displayName")
    if skill_meta.get("summary"):
        skill_data["description"] = skill_meta.get("summary")
    skill_data["source"] = "clawhub"
    skill_data["source_url"] = source_url
    skill_data["clawhub_slug"] = slug
    skill_data["clawhub_version"] = resolved_version
    skill_data["files"] = extracted.get("files", {})
    return skill_data, detail


def preview_clawhub_skill(slug: str, version: str | None = None) -> dict:
    skill_data, detail = build_clawhub_skill_data(slug, version)
    safe, threats = _scan_skill_security(skill_data.get("system_prompt", ""))
    for path, content in skill_data.get("files", {}).items():
        file_safe, file_threats = _scan_skill_security(content)
        if not file_safe:
            safe = False
            threats.extend([f"{path}: {threat}" for threat in file_threats])
    return {
        "ok": True,
        "skill": {key: value for key, value in skill_data.items() if key != "system_prompt"},
        "detail": detail,
        "system_prompt_preview": skill_data.get("system_prompt", "")[:1600],
        "system_prompt_length": len(skill_data.get("system_prompt", "")),
        "supporting_files": sorted(skill_data.get("files", {}).keys()),
        "security": {"safe": safe, "threats": threats},
    }


def preview_openclaw_skill(content: str, source_url: str = "") -> dict:
    skill_data = build_openclaw_skill_data(content, source_url)
    safe, threats = _scan_skill_security(skill_data.get("system_prompt", ""))
    return {
        "ok": True,
        "skill": {key: value for key, value in skill_data.items() if key != "system_prompt"},
        "system_prompt_preview": skill_data.get("system_prompt", "")[:1600],
        "system_prompt_length": len(skill_data.get("system_prompt", "")),
        "security": {"safe": safe, "threats": threats},
    }
