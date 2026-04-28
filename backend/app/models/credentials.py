"""
Provider credential management — OAuth2 token lifecycle + key pool (Hermes pattern).

Features:
  - Encrypted-at-rest credential storage (Fernet symmetric encryption)
  - OAuth2 authorization_code + client_credentials flows
  - Automatic token refresh before expiry
  - Multi-key round-robin pool per provider (load balancing / rate-limit avoidance)
  - Thread-safe credential access
"""
import json
import os
import time
import threading
import secrets
import hashlib
import base64
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CREDENTIALS_DIR = os.path.join("./data", "credentials")
os.makedirs(CREDENTIALS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Encryption helpers (Fernet-compatible, stdlib only)
# ---------------------------------------------------------------------------

def _derive_key() -> bytes:
    """Derive encryption key from HERMES_CREDENTIAL_SECRET or generate one."""
    secret = os.environ.get("HERMES_CREDENTIAL_SECRET", "")
    if not secret:
        key_file = os.path.join(CREDENTIALS_DIR, ".key")
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        if os.path.exists(key_file):
            secret = Path(key_file).read_text(encoding="utf-8").strip()
        else:
            secret = secrets.token_hex(32)
            with open(key_file, "w") as f:
                f.write(secret)
            os.chmod(key_file, 0o600)
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _encrypt(data: str) -> str:
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        return f.encrypt(data.encode()).decode()
    except ImportError:
        # Fallback: base64 (not secure, but functional without cryptography)
        return "b64:" + base64.b64encode(data.encode()).decode()


def _decrypt(token: str) -> str:
    if token.startswith("b64:"):
        return base64.b64decode(token[4:]).decode()
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_derive_key())
        return f.decrypt(token.encode()).decode()
    except Exception as e:
        logger.warning("Decryption failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class OAuthCredential:
    """OAuth2 credential with token lifecycle."""
    provider: str
    client_id: str = ""
    client_secret_encrypted: str = ""
    access_token_encrypted: str = ""
    refresh_token_encrypted: str = ""
    token_url: str = ""
    authorize_url: str = ""
    scopes: list[str] = field(default_factory=list)
    expires_at: float = 0.0  # unix timestamp
    grant_type: str = "client_credentials"  # or "authorization_code"
    extra: dict = field(default_factory=dict)

    @property
    def access_token(self) -> str:
        return _decrypt(self.access_token_encrypted) if self.access_token_encrypted else ""

    @property
    def refresh_token(self) -> str:
        return _decrypt(self.refresh_token_encrypted) if self.refresh_token_encrypted else ""

    @property
    def client_secret(self) -> str:
        return _decrypt(self.client_secret_encrypted) if self.client_secret_encrypted else ""

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= (self.expires_at - 60)  # 60s buffer


@dataclass
class APIKeyEntry:
    """A single API key in a pool."""
    key_encrypted: str
    label: str = ""
    requests_this_minute: int = 0
    last_used: float = 0.0
    is_disabled: bool = False

    @property
    def key(self) -> str:
        return _decrypt(self.key_encrypted)


# ---------------------------------------------------------------------------
# Credential Store
# ---------------------------------------------------------------------------

class CredentialStore:
    """Manage provider credentials: OAuth tokens + API key pools."""

    def __init__(self, storage_path: str = CREDENTIALS_DIR):
        self._storage_path = storage_path
        self._lock = threading.Lock()
        self._oauth: dict[str, OAuthCredential] = {}
        self._key_pools: dict[str, list[APIKeyEntry]] = {}
        self._pool_cursors: dict[str, int] = {}
        self._pending_oauth_states: dict[str, dict] = {}
        self._load()

    def _cred_file(self) -> str:
        return os.path.join(self._storage_path, "credentials.json")

    def _load(self):
        path = self._cred_file()
        if not os.path.exists(path):
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for name, odata in data.get("oauth", {}).items():
                self._oauth[name] = OAuthCredential(**odata)
            for name, keys in data.get("key_pools", {}).items():
                self._key_pools[name] = [APIKeyEntry(**k) for k in keys]
        except Exception as e:
            logger.warning("Failed to load credentials: %s", e)

    def _save(self):
        data = {
            "oauth": {n: asdict(c) for n, c in self._oauth.items()},
            "key_pools": {n: [asdict(k) for k in keys] for n, keys in self._key_pools.items()},
        }
        os.makedirs(self._storage_path, exist_ok=True)
        path = self._cred_file()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(path, 0o600)
        except Exception as e:
            logger.debug("Suppressed error in credentials: %s", e)

    def _prune_pending_oauth_states(self):
        now = time.time()
        expired = [state for state, data in self._pending_oauth_states.items() if data.get("expires_at", 0) <= now]
        for state in expired:
            self._pending_oauth_states.pop(state, None)

    def register_oauth(
        self,
        provider: str,
        client_id: str,
        client_secret: str,
        token_url: str,
        authorize_url: str = "",
        scopes: list[str] | None = None,
        grant_type: str = "client_credentials",
        extra: dict | None = None,
    ) -> dict:
        with self._lock:
            cred = OAuthCredential(
                provider=provider,
                client_id=client_id,
                client_secret_encrypted=_encrypt(client_secret),
                token_url=token_url,
                authorize_url=authorize_url,
                scopes=scopes or [],
                grant_type=grant_type,
                extra=extra or {},
            )
            self._oauth[provider] = cred
            self._save()
        return {"status": "registered", "provider": provider}

    def begin_oauth_authorization(
        self,
        provider: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
        extra_params: dict | None = None,
    ) -> dict:
        import urllib.parse

        with self._lock:
            self._prune_pending_oauth_states()
            cred = self._oauth.get(provider)
            if not cred:
                raise ValueError(f"OAuth provider '{provider}' not registered")
            if cred.grant_type != "authorization_code":
                raise ValueError(f"OAuth provider '{provider}' is not configured for authorization_code flow")
            if not cred.authorize_url:
                raise ValueError(f"OAuth provider '{provider}' has no authorize_url configured")
            final_redirect_uri = redirect_uri or str(cred.extra.get("redirect_uri", ""))
            if not final_redirect_uri:
                raise ValueError("redirect_uri is required for authorization_code flow")
            state = secrets.token_urlsafe(24)
            self._pending_oauth_states[state] = {
                "provider": provider,
                "redirect_uri": final_redirect_uri,
                "expires_at": time.time() + 900,
            }
            params = {
                "response_type": "code",
                "client_id": cred.client_id,
                "redirect_uri": final_redirect_uri,
                "state": state,
            }
            final_scopes = scopes or cred.scopes
            if final_scopes:
                params["scope"] = " ".join(final_scopes)
            authorize_params = cred.extra.get("authorize_params", {}) if isinstance(cred.extra, dict) else {}
            if isinstance(authorize_params, dict):
                params.update({str(k): str(v) for k, v in authorize_params.items()})
            if extra_params:
                params.update({str(k): str(v) for k, v in extra_params.items()})
            separator = "&" if "?" in cred.authorize_url else "?"
            authorization_url = cred.authorize_url + separator + urllib.parse.urlencode(params)
            return {
                "provider": provider,
                "authorization_url": authorization_url,
                "state": state,
                "redirect_uri": final_redirect_uri,
            }

    def _token_request(self, token_url: str, data: dict[str, str]) -> dict:
        import urllib.request
        import urllib.parse

        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(token_url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def exchange_authorization_code(
        self,
        provider: str,
        code: str,
        redirect_uri: str = "",
        extra_params: dict | None = None,
    ) -> dict:
        with self._lock:
            cred = self._oauth.get(provider)
            if not cred:
                raise ValueError(f"OAuth provider '{provider}' not registered")
            final_redirect_uri = redirect_uri or str(cred.extra.get("redirect_uri", ""))
            if not final_redirect_uri:
                raise ValueError("redirect_uri is required for token exchange")
            token_params = cred.extra.get("token_params", {}) if isinstance(cred.extra, dict) else {}
            client_id = cred.client_id
            client_secret = cred.client_secret
            token_url = cred.token_url
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": final_redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if isinstance(token_params, dict):
            data.update({str(k): str(v) for k, v in token_params.items()})
        if extra_params:
            data.update({str(k): str(v) for k, v in extra_params.items()})
        result = self._token_request(token_url, data)
        access_token = result.get("access_token")
        if not access_token:
            raise ValueError("Token response missing access_token")
        refresh_token = result.get("refresh_token", "")
        expires_in = int(result.get("expires_in", 3600) or 3600)
        with self._lock:
            cred = self._oauth.get(provider)
            if not cred:
                cred = OAuthCredential(provider=provider)
                self._oauth[provider] = cred
            cred.access_token_encrypted = _encrypt(access_token)
            if refresh_token:
                cred.refresh_token_encrypted = _encrypt(refresh_token)
            cred.expires_at = time.time() + expires_in
            token_meta = {k: result[k] for k in ("scope", "token_type") if k in result}
            if token_meta:
                cred.extra = {**cred.extra, "last_token_response": token_meta}
            self._save()
        return {
            "status": "authorized",
            "provider": provider,
            "expires_in": expires_in,
            "has_refresh_token": bool(refresh_token),
        }

    def complete_oauth_callback(self, state: str, code: str = "", error: str = "") -> dict:
        if error:
            raise ValueError(error)
        with self._lock:
            self._prune_pending_oauth_states()
            pending = self._pending_oauth_states.pop(state, None)
        if not pending:
            raise ValueError("OAuth state is invalid or expired")
        if not code:
            raise ValueError("OAuth callback missing code")
        return self.exchange_authorization_code(
            provider=pending["provider"],
            code=code,
            redirect_uri=pending["redirect_uri"],
        )

    def get_oauth_token(self, provider: str) -> Optional[str]:
        """Get a valid access token, refreshing if needed."""
        with self._lock:
            cred = self._oauth.get(provider)
            if not cred:
                return None
            if cred.is_expired and cred.token_url:
                self._refresh_token(cred)
            return cred.access_token or None

    def _refresh_token(self, cred: OAuthCredential):
        """Refresh OAuth2 token via token endpoint."""
        data: dict[str, str] = {}
        if cred.refresh_token:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": cred.refresh_token,
                "client_id": cred.client_id,
                "client_secret": cred.client_secret,
            }
        elif cred.grant_type == "client_credentials":
            data = {
                "grant_type": "client_credentials",
                "client_id": cred.client_id,
                "client_secret": cred.client_secret,
            }
            if cred.scopes:
                data["scope"] = " ".join(cred.scopes)
        else:
            logger.warning("Cannot refresh token for %s: no refresh_token and not client_credentials", cred.provider)
            return

        try:
            result = self._token_request(cred.token_url, data)

            cred.access_token_encrypted = _encrypt(result["access_token"])
            if "refresh_token" in result:
                cred.refresh_token_encrypted = _encrypt(result["refresh_token"])
            expires_in = result.get("expires_in", 3600)
            cred.expires_at = time.time() + int(expires_in)
            self._save()
            logger.info("Refreshed OAuth token for %s (expires_in=%s)", cred.provider, expires_in)
        except Exception as e:
            logger.error("Token refresh failed for %s: %s", cred.provider, e)

    def set_oauth_tokens(self, provider: str, access_token: str, refresh_token: str = "", expires_in: int = 3600):
        """Manually set tokens (after authorization_code callback)."""
        with self._lock:
            cred = self._oauth.get(provider)
            if not cred:
                cred = OAuthCredential(provider=provider)
                self._oauth[provider] = cred
            cred.access_token_encrypted = _encrypt(access_token)
            if refresh_token:
                cred.refresh_token_encrypted = _encrypt(refresh_token)
            cred.expires_at = time.time() + expires_in
            self._save()

    def list_oauth_providers(self) -> list[dict]:
        with self._lock:
            result = []
            for name, cred in self._oauth.items():
                result.append({
                    "provider": name,
                    "has_token": bool(cred.access_token_encrypted),
                    "is_expired": cred.is_expired,
                    "grant_type": cred.grant_type,
                    "scopes": cred.scopes,
                })
            return result

    def remove_oauth(self, provider: str) -> bool:
        with self._lock:
            if provider in self._oauth:
                del self._oauth[provider]
                self._save()
                return True
            return False

    # --- Key Pool ---

    def add_key(self, provider: str, api_key: str, label: str = "") -> dict:
        with self._lock:
            if provider not in self._key_pools:
                self._key_pools[provider] = []
            entry = APIKeyEntry(key_encrypted=_encrypt(api_key), label=label or f"key-{len(self._key_pools[provider])}")
            self._key_pools[provider].append(entry)
            self._save()
        return {"status": "added", "provider": provider, "label": entry.label}

    def get_next_key(self, provider: str) -> Optional[str]:
        """Round-robin key selection from the pool."""
        with self._lock:
            pool = self._key_pools.get(provider, [])
            available = [k for k in pool if not k.is_disabled]
            if not available:
                return None
            cursor = self._pool_cursors.get(provider, 0) % len(available)
            entry = available[cursor]
            entry.last_used = time.time()
            entry.requests_this_minute += 1
            self._pool_cursors[provider] = cursor + 1
            return entry.key

    def list_key_pool(self, provider: str) -> list[dict]:
        with self._lock:
            pool = self._key_pools.get(provider, [])
            return [{"label": k.label, "is_disabled": k.is_disabled, "last_used": k.last_used} for k in pool]

    def disable_key(self, provider: str, label: str) -> bool:
        with self._lock:
            pool = self._key_pools.get(provider, [])
            for k in pool:
                if k.label == label:
                    k.is_disabled = True
                    self._save()
                    return True
            return False

    def remove_key(self, provider: str, label: str) -> bool:
        with self._lock:
            pool = self._key_pools.get(provider, [])
            for i, k in enumerate(pool):
                if k.label == label:
                    pool.pop(i)
                    self._save()
                    return True
            return False

    # --- Unified get_api_key ---

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key: try key pool first, then OAuth token, then env var."""
        # 1. Key pool
        key = self.get_next_key(provider)
        if key:
            return key
        # 2. OAuth token
        token = self.get_oauth_token(provider)
        if token:
            return token
        # 3. Env var fallback
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "modelscope": "MODELSCOPE_API_KEY",
        }
        env_var = env_map.get(provider.lower(), f"{provider.upper()}_API_KEY")
        return os.environ.get(env_var)


# Singleton
credential_store = CredentialStore()
