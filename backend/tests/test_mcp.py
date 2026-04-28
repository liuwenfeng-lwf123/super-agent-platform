"""Tests for MCP (Model Context Protocol) integration."""
import asyncio
import os
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.skills.mcp import (
    MCPPrompt,
    MCPRegistry,
    MCPResource,
    MCPServerClient,
    MCPServerConfig,
    MCPStdioClient,
    MCPTool,
)


class TestMCPTool(unittest.TestCase):
    def test_create(self):
        t = MCPTool(name="test", description="A test tool")
        self.assertEqual(t.name, "test")
        self.assertEqual(t.description, "A test tool")
        self.assertEqual(t.input_schema, {})

    def test_with_schema(self):
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        t = MCPTool(name="search", description="Search", input_schema=schema)
        self.assertEqual(t.input_schema["type"], "object")


class TestMCPServerConfig(unittest.TestCase):
    def test_defaults(self):
        c = MCPServerConfig(name="test", url="http://localhost:8080")
        self.assertIsNone(c.api_key)
        self.assertTrue(c.enabled)

    def test_with_key(self):
        c = MCPServerConfig(name="test", url="http://localhost:8080", api_key="sk-123")
        self.assertEqual(c.api_key, "sk-123")


class TestMCPStdioClient(unittest.TestCase):
    def test_initialize_sends_initialized_notification(self):
        config = MCPServerConfig(name="stdio", transport="stdio", command="python")
        client = MCPStdioClient(config)

        with patch.object(client, "_rpc", new_callable=AsyncMock) as mock_rpc, patch.object(
            client, "_send_notification", new_callable=AsyncMock,
        ) as mock_notify:
            mock_rpc.return_value = {"result": {"serverInfo": {"name": "demo"}}}
            asyncio.run(client._initialize())

        self.assertTrue(client._initialized)
        mock_rpc.assert_awaited_once()
        mock_notify.assert_awaited_once_with("notifications/initialized", {})

    def test_list_prompts_and_resources_parse_payloads(self):
        config = MCPServerConfig(name="stdio", transport="stdio", command="python")
        client = MCPStdioClient(config)

        with patch.object(client, "start", new_callable=AsyncMock), patch.object(
            client, "_rpc", new_callable=AsyncMock,
        ) as mock_rpc:
            mock_rpc.side_effect = [
                {
                    "result": {
                        "prompts": [
                            {
                                "name": "summarize",
                                "description": "Summarize text",
                                "arguments": [{"name": "topic", "required": True}],
                            },
                        ],
                    },
                },
                {
                    "result": {
                        "resources": [
                            {
                                "uri": "file://demo.txt",
                                "name": "demo.txt",
                                "description": "Demo",
                                "mimeType": "text/plain",
                            },
                        ],
                    },
                },
            ]
            prompts = asyncio.run(client.list_prompts())
            resources = asyncio.run(client.list_resources())

        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0].name, "summarize")
        self.assertEqual(prompts[0].arguments[0]["name"], "topic")
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].uri, "file://demo.txt")
        self.assertEqual(resources[0].mime_type, "text/plain")


class TestMCPServerClient(unittest.TestCase):
    def test_get_tools_empty(self):
        config = MCPServerConfig(name="test", url="http://localhost:9999")
        client = MCPServerClient(config)
        self.assertEqual(client.get_tools(), [])

    def test_discover_failure_graceful(self):
        config = MCPServerConfig(name="test", url="http://nonexistent:9999")
        client = MCPServerClient(config)
        tools = asyncio.run(client.discover_tools())
        self.assertEqual(tools, [])

    def test_call_tool_failure_graceful(self):
        config = MCPServerConfig(name="test", url="http://nonexistent:9999")
        client = MCPServerClient(config)
        result = asyncio.run(client.call_tool("some_tool", {"arg": "val"}))
        self.assertIn("error", result)

    def test_http_prompt_and_resource_roundtrip(self):
        calls = []

        class FakeResponse:
            def __init__(self, status_code, payload, text=None):
                self.status_code = status_code
                self._payload = payload
                self.text = text if text is not None else json.dumps(payload)

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers=None):
                calls.append(("get", url, headers))
                if url.endswith("/prompts"):
                    return FakeResponse(200, {
                        "prompts": [
                            {
                                "name": "summarize",
                                "description": "Summarize text",
                                "arguments": [{"name": "topic", "required": True}],
                            },
                        ],
                    })
                if url.endswith("/resources"):
                    return FakeResponse(200, {
                        "resources": [
                            {
                                "uri": "file://demo.txt",
                                "name": "demo.txt",
                                "description": "Demo",
                                "mimeType": "text/plain",
                            },
                        ],
                    })
                raise AssertionError(url)

            async def post(self, url, headers=None, json=None):
                calls.append(("post", url, headers, json))
                if url.endswith("/prompts/summarize"):
                    return FakeResponse(200, {
                        "messages": [
                            {"role": "assistant", "content": json["arguments"]["topic"]},
                        ],
                    })
                if url.endswith("/resources/read"):
                    return FakeResponse(200, {
                        "contents": [
                            {"uri": json["uri"], "text": "demo"},
                        ],
                    })
                raise AssertionError(url)

        config = MCPServerConfig(name="http", url="http://localhost:8080", api_key="sk-123")
        client = MCPServerClient(config)

        with patch("app.skills.mcp.httpx.AsyncClient", FakeAsyncClient):
            prompts = asyncio.run(client.discover_prompts())
            prompt_result = asyncio.run(client.get_prompt("summarize", {"topic": "demo"}))
            resources = asyncio.run(client.discover_resources())
            resource_result = asyncio.run(client.read_resource("file://demo.txt"))

        self.assertEqual(prompts[0].name, "summarize")
        self.assertEqual(prompts[0].arguments[0]["name"], "topic")
        self.assertEqual(prompt_result["messages"][0]["content"], "demo")
        self.assertEqual(resources[0].uri, "file://demo.txt")
        self.assertEqual(resources[0].mime_type, "text/plain")
        self.assertEqual(resource_result["contents"][0]["text"], "demo")
        auth_headers = [entry[2]["Authorization"] for entry in calls]
        self.assertTrue(all(value == "Bearer sk-123" for value in auth_headers))

    def test_stdio_prompt_and_resource_roundtrip(self):
        config = MCPServerConfig(name="stdio", transport="stdio", command="python")
        client = MCPServerClient(config)

        with patch.object(client._stdio, "list_prompts", new_callable=AsyncMock) as mock_list_prompts, patch.object(
            client._stdio, "get_prompt", new_callable=AsyncMock,
        ) as mock_get_prompt, patch.object(
            client._stdio, "list_resources", new_callable=AsyncMock,
        ) as mock_list_resources, patch.object(
            client._stdio, "read_resource", new_callable=AsyncMock,
        ) as mock_read_resource:
            mock_list_prompts.return_value = [
                MCPPrompt(name="summarize", description="Summarize text", arguments=[{"name": "topic"}]),
            ]
            mock_get_prompt.return_value = {"messages": [{"role": "assistant", "content": "demo"}]}
            mock_list_resources.return_value = [
                MCPResource(uri="file://demo.txt", name="demo.txt", mime_type="text/plain"),
            ]
            mock_read_resource.return_value = {"contents": [{"uri": "file://demo.txt", "text": "demo"}]}

            prompts = asyncio.run(client.discover_prompts())
            prompt_result = asyncio.run(client.get_prompt("summarize", {"topic": "demo"}))
            resources = asyncio.run(client.discover_resources())
            resource_result = asyncio.run(client.read_resource("file://demo.txt"))

        self.assertEqual(prompts[0].name, "summarize")
        self.assertEqual(prompt_result["messages"][0]["content"], "demo")
        self.assertEqual(resources[0].mime_type, "text/plain")
        self.assertEqual(resource_result["contents"][0]["uri"], "file://demo.txt")
        mock_get_prompt.assert_awaited_once_with("summarize", {"topic": "demo"})
        mock_read_resource.assert_awaited_once_with("file://demo.txt")


class TestMCPRegistry(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        os.makedirs("data", exist_ok=True)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()

    def test_register_and_list(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="test_server", url="http://localhost:8080")
        reg.register(config)
        servers = reg.list_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "test_server")
        self.assertEqual(servers[0]["url"], "http://localhost:8080")

    def test_unregister(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="temp", url="http://localhost:8080")
        reg.register(config)
        reg.unregister("temp")
        self.assertEqual(len(reg.list_servers()), 0)

    def test_unregister_nonexistent(self):
        reg = MCPRegistry()
        reg.unregister("nope")  # should not raise

    def test_get_server(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="s1", url="http://localhost:8080")
        reg.register(config)
        server = reg.get_server("s1")
        self.assertIsNotNone(server)
        self.assertIsNone(reg.get_server("nope"))

    def test_list_all_tools_empty(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="s1", url="http://localhost:8080")
        reg.register(config)
        self.assertEqual(reg.list_all_tools(), [])

    def test_disabled_server_excluded(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="dis", url="http://localhost:8080", enabled=False)
        reg.register(config)
        self.assertEqual(reg.list_all_tools(), [])

    def test_call_tool_server_not_found(self):
        reg = MCPRegistry()
        result = asyncio.run(reg.call_tool("nope", "tool", {}))
        self.assertIn("error", result)

    def test_call_tool_disabled_server(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="dis", url="http://localhost:8080", enabled=False)
        reg.register(config)
        result = asyncio.run(reg.call_tool("dis", "tool", {}))
        self.assertIn("error", result)
        self.assertIn("disabled", result["error"])

    def test_config_persistence(self):
        reg = MCPRegistry()
        reg.register(MCPServerConfig(name="persist", url="http://localhost:8080"))
        # Check file exists
        self.assertTrue(os.path.exists(reg.CONFIG_PATH))
        # Load new registry
        reg2 = MCPRegistry()
        servers = reg2.list_servers()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "persist")

    def test_get_langchain_tools_empty(self):
        reg = MCPRegistry()
        self.assertEqual(reg.get_langchain_tools(), [])

    def test_get_langchain_tools_with_mock(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="mock_srv", url="http://localhost:9999")
        reg.register(config)
        # Inject mock tools
        server = reg.get_server("mock_srv")
        server._tools = [
            MCPTool(name="search", description="Search the web", input_schema={"type": "object"}),
            MCPTool(name="calc", description="Calculator"),
        ]
        lc_tools = reg.get_langchain_tools()
        self.assertEqual(len(lc_tools), 2)
        names = [t.name for t in lc_tools]
        self.assertIn("mcp_mock_srv_search", names)
        self.assertIn("mcp_mock_srv_calc", names)

    def test_multi_server_tools(self):
        reg = MCPRegistry()
        reg.register(MCPServerConfig(name="srv_a", url="http://localhost:8081"))
        reg.register(MCPServerConfig(name="srv_b", url="http://localhost:8082"))
        sa = reg.get_server("srv_a")
        sb = reg.get_server("srv_b")
        sa._tools = [MCPTool(name="tool1", description="Tool 1")]
        sb._tools = [MCPTool(name="tool2", description="Tool 2")]
        all_tools = reg.list_all_tools()
        self.assertEqual(len(all_tools), 2)
        servers = {t["server"] for t in all_tools}
        self.assertIn("srv_a", servers)
        self.assertIn("srv_b", servers)

    def test_list_all_prompts_and_resources(self):
        reg = MCPRegistry()
        reg.register(MCPServerConfig(name="srv", url="http://localhost:8080"))
        server = reg.get_server("srv")
        server._prompts = [MCPPrompt(name="summarize", description="Summarize text")]
        server._resources = [MCPResource(uri="file://demo.txt", name="demo.txt", mime_type="text/plain")]

        prompts = reg.list_all_prompts()
        resources = reg.list_all_resources()

        self.assertEqual(prompts, [{
            "server": "srv",
            "name": "summarize",
            "description": "Summarize text",
            "arguments": [],
        }])
        self.assertEqual(resources, [{
            "server": "srv",
            "uri": "file://demo.txt",
            "name": "demo.txt",
            "description": "",
            "mime_type": "text/plain",
        }])

    def test_call_prompt_and_read_resource_disabled_server(self):
        reg = MCPRegistry()
        reg.register(MCPServerConfig(name="dis", url="http://localhost:8080", enabled=False))

        prompt_result = asyncio.run(reg.call_prompt("dis", "summarize"))
        resource_result = asyncio.run(reg.read_resource("dis", "file://demo.txt"))

        self.assertIn("error", prompt_result)
        self.assertIn("disabled", prompt_result["error"])
        self.assertIn("error", resource_result)
        self.assertIn("disabled", resource_result["error"])

    def test_discover_all_calls_enabled(self):
        reg = MCPRegistry()
        reg.register(MCPServerConfig(name="en", url="http://localhost:8080"))
        reg.register(MCPServerConfig(name="dis", url="http://localhost:8081", enabled=False))
        with patch.object(MCPServerClient, "discover_tools", new_callable=AsyncMock) as mock_tools, patch.object(
            MCPServerClient, "discover_prompts", new_callable=AsyncMock,
        ) as mock_prompts, patch.object(
            MCPServerClient, "discover_resources", new_callable=AsyncMock,
        ) as mock_resources:
            asyncio.run(reg.discover_all())
            self.assertEqual(mock_tools.call_count, 1)
            self.assertEqual(mock_prompts.call_count, 1)
            self.assertEqual(mock_resources.call_count, 1)

    def test_langchain_tool_description_fallback(self):
        reg = MCPRegistry()
        config = MCPServerConfig(name="s", url="http://localhost:9999")
        reg.register(config)
        server = reg.get_server("s")
        server._tools = [MCPTool(name="t", description="")]
        lc_tools = reg.get_langchain_tools()
        self.assertEqual(len(lc_tools), 1)
        self.assertIn("MCP tool", lc_tools[0].description)


if __name__ == "__main__":
    unittest.main()
