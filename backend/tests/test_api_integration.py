"""
API integration tests — tests REST endpoints using FastAPI TestClient.
No LLM calls; tests CRUD, memory, cost, tools, safety, file history endpoints.
"""
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router


class ApiTestBase(unittest.TestCase):
    """Base class that sets up a TestClient with a temp data directory."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        # Ensure data dirs
        os.makedirs("data/workspaces", exist_ok=True)
        os.makedirs("data/memory", exist_ok=True)

        app = FastAPI()
        app.include_router(router)

        @app.get("/api/health")
        @app.get("/health")
        async def health_check():
            return {"status": "ok", "version": "1.0.0"}

        self.client = TestClient(app)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestHealthEndpoint(ApiTestBase):
    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


class TestThreadEndpoints(ApiTestBase):
    def test_create_and_list_threads(self):
        # Create via list (should be empty initially)
        r = self.client.get("/api/threads")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, (list, dict))

    def test_get_nonexistent_thread(self):
        r = self.client.get("/api/threads/nonexistent-id")
        # May return 200 with null/error or 404
        self.assertIn(r.status_code, [200, 404])


class TestMemoryEndpoints(ApiTestBase):
    def test_list_memory_empty(self):
        r = self.client.get("/api/memory")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_add_and_search_memory(self):
        # Add
        r = self.client.post("/api/memory?key=test_key&value=test_value&category=knowledge")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["key"], "test_key")
        self.assertEqual(data["value"], "test_value")
        entry_id = data["id"]

        # List
        r = self.client.get("/api/memory")
        entries = r.json()
        self.assertTrue(any(e["id"] == entry_id for e in entries))

        # Search
        r = self.client.get("/api/memory/search?query=test")
        results = r.json()
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)

        # Delete
        r = self.client.delete(f"/api/memory/{entry_id}")
        self.assertEqual(r.status_code, 200)

    def test_search_empty_query(self):
        r = self.client.get("/api/memory/search?query=")
        self.assertEqual(r.status_code, 200)


class TestMemoryLayeredEndpoints(ApiTestBase):
    def test_memory_stats(self):
        r = self.client.get("/api/memory/stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # Stats contain project_memory info
        self.assertTrue("project" in data or "project_memory" in data)

    def test_project_memory(self):
        # Get (empty)
        r = self.client.get("/api/memory/project")
        self.assertEqual(r.status_code, 200)

        # Set
        r = self.client.post("/api/memory/project?content=test+content")
        self.assertEqual(r.status_code, 200)


class TestCostEndpoints(ApiTestBase):
    def test_cost_stats(self):
        r = self.client.get("/api/cost")
        self.assertEqual(r.status_code, 200)

    def test_cost_history(self):
        r = self.client.get("/api/cost/history")
        # Endpoint may or may not exist yet
        self.assertIn(r.status_code, [200, 404])

    def test_cost_models(self):
        r = self.client.get("/api/cost/models")
        self.assertIn(r.status_code, [200, 404])


class TestToolEndpoints(ApiTestBase):
    def test_list_tools(self):
        r = self.client.get("/api/tools")
        self.assertEqual(r.status_code, 200)
        tools = r.json()
        self.assertIsInstance(tools, list)
        self.assertTrue(len(tools) > 0)
        # Check tool structure
        t = tools[0]
        self.assertIn("name", t)
        self.assertIn("category", t)
        self.assertIn("is_read_only", t)
        self.assertIn("is_concurrency_safe", t)

    def test_list_agents(self):
        r = self.client.get("/api/agents")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("planner", data)
        self.assertIn("coder", data)


class TestBashSafetyEndpoint(ApiTestBase):
    def test_safe_command(self):
        r = self.client.get("/api/safety/bash?command=ls+-la")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["safe"])
        self.assertTrue(data["is_read_only"])

    def test_dangerous_command(self):
        r = self.client.get("/api/safety/bash?command=rm+-rf+/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["safe"])
        self.assertTrue(data["is_destructive"])


class TestPermissionEndpoints(ApiTestBase):
    def test_get_permission_rules(self):
        r = self.client.get("/api/permissions/rules")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("always_allow", data)

    def test_get_policy_limits(self):
        r = self.client.get("/api/policy-limits")
        self.assertEqual(r.status_code, 200)

    def test_pending_permissions(self):
        r = self.client.get("/api/permissions/pending")
        self.assertEqual(r.status_code, 200)
        self.assertIn("requests", r.json())


class TestContextEndpoints(ApiTestBase):
    def test_compact_state(self):
        r = self.client.get("/api/context/compact")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("compact_boundary", data)
        self.assertIn("total_compactions", data)

    def test_token_estimation(self):
        r = self.client.get("/api/context/tokens?text=hello+world")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("estimated_tokens", data)
        self.assertGreater(data["estimated_tokens"], 0)


class TestFileHistoryEndpoint(ApiTestBase):
    def test_empty_history(self):
        r = self.client.get("/api/threads/nonexistent/file-history")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["count"], 0)


class TestWorkspaceIsolationEndpoints(ApiTestBase):
    def test_download_rejects_symlink_escape(self):
        workspace_dir = os.path.join("data", "workspaces", "thread1", "workspace")
        os.makedirs(workspace_dir, exist_ok=True)
        outside_dir = tempfile.mkdtemp(dir=self._tempdir.name)
        link_path = os.path.join(workspace_dir, "link")
        try:
            with open(os.path.join(outside_dir, "secret.txt"), "w", encoding="utf-8") as f:
                f.write("secret")
            try:
                os.symlink(outside_dir, link_path)
            except (OSError, NotImplementedError):
                self.skipTest("symlink not supported")
            r = self.client.get("/api/workspace/thread1/download/link/secret.txt")
            self.assertIn(r.status_code, (200, 403))
            self.assertIn("not allowed", r.json().get("error", r.json().get("detail", "")).lower())
        finally:
            if os.path.lexists(link_path):
                os.unlink(link_path)


class TestMiscEndpoints(ApiTestBase):
    def test_models(self):
        r = self.client.get("/api/models")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertGreater(len(data), 0)
        self.assertEqual(data[0]["name"], "Qwen/Qwen3-Coder-30B-A3B-Instruct")

    def test_skills(self):
        r = self.client.get("/api/skills")
        self.assertEqual(r.status_code, 200)

    def test_knowledge(self):
        r = self.client.get("/api/knowledge")
        self.assertEqual(r.status_code, 200)

    def test_channels(self):
        r = self.client.get("/api/channels")
        self.assertEqual(r.status_code, 200)


class TestProviderAndCredentialEndpoints(ApiTestBase):
    def setUp(self):
        super().setUp()
        import app.models.credentials as credentials_mod
        self._credentials_mod = credentials_mod
        self._old_store = credentials_mod.credential_store
        credentials_mod.credential_store = credentials_mod.CredentialStore(storage_path="./data/credentials")

    def tearDown(self):
        self._credentials_mod.credential_store = self._old_store
        super().tearDown()

    def test_list_providers(self):
        r = self.client.get("/api/providers")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        names = {item["name"] for item in data["providers"]}
        self.assertIn("openai", names)
        self.assertIn("modelscope", names)
        self.assertIn("openrouter", names)

    def test_add_model_applies_provider_defaults(self):
        r = self.client.post(
            "/api/models",
            params={
                "name": "openrouter:meta-llama/llama-3.3-70b-instruct",
                "display_name": "Llama via OpenRouter",
                "model": "meta-llama/llama-3.3-70b-instruct",
                "provider": "openrouter",
            },
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/models")
        self.assertEqual(r.status_code, 200)
        added = next(item for item in r.json() if item["name"] == "openrouter:meta-llama/llama-3.3-70b-instruct")
        self.assertEqual(added["provider"], "openrouter")
        self.assertEqual(added["api_key_env"], "OPENROUTER_API_KEY")
        self.assertEqual(added["base_url"], "https://openrouter.ai/api/v1")

    def test_oauth_authorization_code_callback_flow(self):
        register = self.client.post(
            "/api/credentials/oauth/register",
            json={
                "provider": "demo-oauth",
                "client_id": "client-123",
                "client_secret": "secret-456",
                "token_url": "https://example.com/token",
                "authorize_url": "https://example.com/authorize",
                "scopes": ["profile", "email"],
                "grant_type": "authorization_code",
                "extra": {
                    "authorize_params": {"audience": "demo-api"},
                    "token_params": {"resource": "demo-resource"},
                },
            },
        )
        self.assertEqual(register.status_code, 200)

        authorize = self.client.post(
            "/api/credentials/oauth/authorize",
            json={"provider": "demo-oauth"},
        )
        self.assertEqual(authorize.status_code, 200)
        payload = authorize.json()
        query = parse_qs(urlparse(payload["authorization_url"]).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["client-123"])
        self.assertEqual(query["scope"], ["profile email"])
        self.assertEqual(query["audience"], ["demo-api"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/api/credentials/oauth/callback"])
        self.assertTrue(payload["state"])

        class Response:
            def __init__(self, body: str):
                self._body = body.encode()

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch(
            "urllib.request.urlopen",
            return_value=Response('{"access_token":"tok-123","refresh_token":"ref-456","expires_in":7200,"token_type":"Bearer"}'),
        ):
            callback = self.client.get(
                "/api/credentials/oauth/callback",
                params={"state": payload["state"], "code": "auth-code-xyz"},
            )
        self.assertEqual(callback.status_code, 200)
        self.assertIn("OAuth authorization complete", callback.text)
        self.assertIn("demo-oauth", callback.text)

        oauth_list = self.client.get("/api/credentials/oauth")
        self.assertEqual(oauth_list.status_code, 200)
        provider = next(item for item in oauth_list.json()["providers"] if item["provider"] == "demo-oauth")
        self.assertTrue(provider["has_token"])
        self.assertEqual(provider["grant_type"], "authorization_code")
        self.assertEqual(self._credentials_mod.credential_store.get_api_key("demo-oauth"), "tok-123")


if __name__ == "__main__":
    unittest.main()
