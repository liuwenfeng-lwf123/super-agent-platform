from pydantic import BaseModel, Field
from typing import Optional, Any
import asyncio
import httpx
import json
import logging
import os

logger = logging.getLogger(__name__)


class MCPTool(BaseModel):
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)


class MCPPrompt(BaseModel):
    name: str
    description: str = ""
    arguments: Any = Field(default_factory=list)


class MCPResource(BaseModel):
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


class MCPServerConfig(BaseModel):
    name: str
    # Transport: "http" (legacy/custom) or "stdio" (real MCP JSON-RPC)
    transport: str = "http"
    # For HTTP transport
    url: str = ""
    api_key: Optional[str] = None
    # For stdio transport
    command: Optional[str] = None           # e.g. "node"  or  "uvx"
    args: list[str] = Field(default_factory=list)  # e.g. ["mcp-server-filesystem", "/tmp"]
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class MCPStdioClient:
    """Minimal JSON-RPC 2.0 client for MCP servers communicating over stdio.

    Implements the subset of the MCP protocol needed for tool discovery and invocation:
      - initialize
      - tools/list
      - tools/call
    See: https://spec.modelcontextprotocol.io/
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._initialized = False
        self._lock = asyncio.Lock()

    async def start(self):
        if self._proc and self._proc.returncode is None:
            return
        if not self.config.command:
            raise RuntimeError(f"MCP stdio server '{self.config.name}' missing command")
        env = {**os.environ, **self.config.env}
        self._proc = await asyncio.create_subprocess_exec(
            self.config.command, *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self._initialize()

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            except Exception as e:
                logger.debug("Suppressed error in mcp: %s", e)
        self._proc = None
        self._initialized = False

    async def _initialize(self):
        if self._initialized:
            return
        result = await self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "super-agent-platform", "version": "0.1"},
        })
        if "error" in result:
            raise RuntimeError(f"MCP initialize failed: {result['error']}")
        # Send the required "notifications/initialized" notification
        await self._send_notification("notifications/initialized", {})
        self._initialized = True

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        async with self._lock:
            if not self._proc or not self._proc.stdin or not self._proc.stdout:
                raise RuntimeError("MCP stdio process not running")
            self._req_id += 1
            req = {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": method,
                "params": params or {},
            }
            line = (json.dumps(req) + "\n").encode("utf-8")
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()

            # Read one response line (blocking-ish, but async)
            resp_line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=30)
            if not resp_line:
                raise RuntimeError("MCP server closed stdout")
            try:
                return json.loads(resp_line.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON from MCP server: {e}")

    async def _send_notification(self, method: str, params: dict):
        if not self._proc or not self._proc.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def list_tools(self) -> list[MCPTool]:
        await self.start()
        result = await self._rpc("tools/list", {})
        if "error" in result:
            logger.warning("MCP tools/list error: %s", result["error"])
            return []
        tools_raw = result.get("result", {}).get("tools", [])
        return [
            MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", t.get("input_schema", {})),
            )
            for t in tools_raw
        ]

    async def list_prompts(self) -> list[MCPPrompt]:
        await self.start()
        result = await self._rpc("prompts/list", {})
        if "error" in result:
            logger.warning("MCP prompts/list error: %s", result["error"])
            return []
        prompts_raw = result.get("result", {}).get("prompts", [])
        return [
            MCPPrompt(
                name=p.get("name", ""),
                description=p.get("description", ""),
                arguments=p.get("arguments", p.get("inputSchema", [])),
            )
            for p in prompts_raw
        ]

    async def get_prompt(self, prompt_name: str, arguments: dict | None = None) -> Any:
        await self.start()
        result = await self._rpc("prompts/get", {
            "name": prompt_name,
            "arguments": arguments or {},
        })
        if "error" in result:
            return {"error": result["error"]}
        return result.get("result", {})

    async def list_resources(self) -> list[MCPResource]:
        await self.start()
        result = await self._rpc("resources/list", {})
        if "error" in result:
            logger.warning("MCP resources/list error: %s", result["error"])
            return []
        resources_raw = result.get("result", {}).get("resources", [])
        return [
            MCPResource(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", r.get("mime_type", "")),
            )
            for r in resources_raw
        ]

    async def read_resource(self, uri: str) -> Any:
        await self.start()
        result = await self._rpc("resources/read", {"uri": uri})
        if "error" in result:
            return {"error": result["error"]}
        return result.get("result", {})

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        await self.start()
        result = await self._rpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if "error" in result:
            return {"error": result["error"]}
        return result.get("result", {})


class MCPServerClient:
    """Unified client supporting both HTTP and stdio MCP servers."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._tools: list[MCPTool] = []
        self._prompts: list[MCPPrompt] = []
        self._resources: list[MCPResource] = []
        self._stdio: MCPStdioClient | None = None
        if config.transport == "stdio":
            self._stdio = MCPStdioClient(config)

    async def discover_tools(self) -> list[MCPTool]:
        if self.config.transport == "stdio" and self._stdio:
            try:
                self._tools = await self._stdio.list_tools()
            except Exception as e:
                logger.warning("MCP stdio discover failed for %s: %s", self.config.name, e)
            return self._tools

        # HTTP transport (legacy)
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.config.url}/tools",
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._tools = [
                        MCPTool(
                            name=t.get("name", ""),
                            description=t.get("description", ""),
                            input_schema=t.get("inputSchema", t.get("input_schema", {})),
                        )
                        for t in data.get("tools", [])
                    ]
                return self._tools
        except Exception as e:
            logger.debug("Suppressed error in mcp: %s", e)
            return self._tools

    async def discover_prompts(self) -> list[MCPPrompt]:
        if self.config.transport == "stdio" and self._stdio:
            try:
                self._prompts = await self._stdio.list_prompts()
            except Exception as e:
                logger.warning("MCP stdio prompt discover failed for %s: %s", self.config.name, e)
            return self._prompts

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.config.url}/prompts", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    self._prompts = [
                        MCPPrompt(
                            name=p.get("name", ""),
                            description=p.get("description", ""),
                            arguments=p.get("arguments", p.get("inputSchema", [])),
                        )
                        for p in data.get("prompts", [])
                    ]
                return self._prompts
        except Exception as e:
            logger.debug("Suppressed error in mcp: %s", e)
            return self._prompts

    async def get_prompt(self, prompt_name: str, arguments: dict | None = None) -> Any:
        if self.config.transport == "stdio" and self._stdio:
            try:
                return await self._stdio.get_prompt(prompt_name, arguments)
            except Exception as e:
                return {"error": f"MCP stdio prompt failed: {e}"}

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.config.url}/prompts/{prompt_name}",
                    headers=headers,
                    json={"arguments": arguments or {}},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    async def discover_resources(self) -> list[MCPResource]:
        if self.config.transport == "stdio" and self._stdio:
            try:
                self._resources = await self._stdio.list_resources()
            except Exception as e:
                logger.warning("MCP stdio resource discover failed for %s: %s", self.config.name, e)
            return self._resources

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.config.url}/resources", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    self._resources = [
                        MCPResource(
                            uri=r.get("uri", ""),
                            name=r.get("name", ""),
                            description=r.get("description", ""),
                            mime_type=r.get("mimeType", r.get("mime_type", "")),
                        )
                        for r in data.get("resources", [])
                    ]
                return self._resources
        except Exception as e:
            logger.debug("Suppressed error in mcp: %s", e)
            return self._resources

    async def read_resource(self, uri: str) -> Any:
        if self.config.transport == "stdio" and self._stdio:
            try:
                return await self._stdio.read_resource(uri)
            except Exception as e:
                return {"error": f"MCP stdio resource read failed: {e}"}

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.config.url}/resources/read",
                    headers=headers,
                    json={"uri": uri},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        if self.config.transport == "stdio" and self._stdio:
            try:
                return await self._stdio.call_tool(tool_name, arguments)
            except Exception as e:
                return {"error": f"MCP stdio call failed: {e}"}

        # HTTP transport
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.config.url}/tools/{tool_name}",
                    headers=headers,
                    json={"arguments": arguments},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def get_tools(self) -> list[MCPTool]:
        return self._tools

    def get_prompts(self) -> list[MCPPrompt]:
        return self._prompts

    def get_resources(self) -> list[MCPResource]:
        return self._resources

    async def close(self):
        if self._stdio:
            await self._stdio.stop()


class MCPRegistry:
    CONFIG_PATH = "./data/mcp_servers.json"

    def __init__(self):
        self._servers: dict[str, MCPServerClient] = {}
        self._load_config()

    def _load_config(self):
        import os
        if os.path.exists(self.CONFIG_PATH):
            try:
                with open(self.CONFIG_PATH, "r") as f:
                    configs = json.load(f)
                for c in configs:
                    config = MCPServerConfig(**c)
                    self._servers[config.name] = MCPServerClient(config)
            except Exception as e:
                logger.debug("Suppressed error in mcp: %s", e)

    def _save_config(self):
        import os
        os.makedirs(os.path.dirname(self.CONFIG_PATH), exist_ok=True)
        configs = [s.config.model_dump() for s in self._servers.values()]
        with open(self.CONFIG_PATH, "w") as f:
            json.dump(configs, f, indent=2)

    def register(self, config: MCPServerConfig):
        self._servers[config.name] = MCPServerClient(config)
        self._save_config()

    def unregister(self, name: str):
        if name in self._servers:
            del self._servers[name]
            self._save_config()

    def list_servers(self) -> list[dict]:
        return [
            {
                "name": s.config.name,
                "url": s.config.url,
                "enabled": s.config.enabled,
                "tools": [t.name for t in s.get_tools()],
                "prompts": [p.name for p in s.get_prompts()],
                "resources": [r.uri for r in s.get_resources()],
            }
            for s in self._servers.values()
        ]

    def get_server(self, name: str) -> MCPServerClient | None:
        return self._servers.get(name)

    def list_all_tools(self) -> list[dict]:
        tools = []
        for server in self._servers.values():
            if not server.config.enabled:
                continue
            for tool in server.get_tools():
                tools.append({
                    "server": server.config.name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                })
        return tools

    def list_all_prompts(self) -> list[dict]:
        prompts = []
        for server in self._servers.values():
            if not server.config.enabled:
                continue
            for prompt in server.get_prompts():
                prompts.append({
                    "server": server.config.name,
                    "name": prompt.name,
                    "description": prompt.description,
                    "arguments": prompt.arguments,
                })
        return prompts

    def list_all_resources(self) -> list[dict]:
        resources = []
        for server in self._servers.values():
            if not server.config.enabled:
                continue
            for resource in server.get_resources():
                resources.append({
                    "server": server.config.name,
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mime_type": resource.mime_type,
                })
        return resources

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        server = self._servers.get(server_name)
        if not server:
            return {"error": f"Server '{server_name}' not found"}
        if not server.config.enabled:
            return {"error": f"Server '{server_name}' is disabled"}
        return await server.call_tool(tool_name, arguments)

    async def call_prompt(self, server_name: str, prompt_name: str, arguments: dict | None = None) -> Any:
        server = self._servers.get(server_name)
        if not server:
            return {"error": f"Server '{server_name}' not found"}
        if not server.config.enabled:
            return {"error": f"Server '{server_name}' is disabled"}
        return await server.get_prompt(prompt_name, arguments)

    async def read_resource(self, server_name: str, uri: str) -> Any:
        server = self._servers.get(server_name)
        if not server:
            return {"error": f"Server '{server_name}' not found"}
        if not server.config.enabled:
            return {"error": f"Server '{server_name}' is disabled"}
        return await server.read_resource(uri)

    async def discover_all(self):
        for server in self._servers.values():
            if server.config.enabled:
                await server.discover_tools()
                await server.discover_prompts()
                await server.discover_resources()

    def get_langchain_tools(self) -> list:
        """Convert all enabled MCP tools into LangChain tool functions."""
        from langchain_core.tools import StructuredTool

        lc_tools = []
        for server in self._servers.values():
            if not server.config.enabled:
                continue
            for mcp_tool in server.get_tools():
                server_name = server.config.name
                tool_name = mcp_tool.name

                async def _call(
                    _server=server_name, _tool=tool_name, **kwargs
                ) -> str:
                    result = await self.call_tool(_server, _tool, kwargs)
                    if isinstance(result, dict) and "error" in result:
                        return f"Error: {result['error']}"
                    return json.dumps(result) if isinstance(result, (dict, list)) else str(result)

                lc_tools.append(
                    StructuredTool.from_function(
                        coroutine=_call,
                        name=f"mcp_{server_name}_{tool_name}",
                        description=mcp_tool.description or f"MCP tool: {tool_name} from {server_name}",
                    )
                )
        return lc_tools


mcp_registry = MCPRegistry()
