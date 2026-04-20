from pydantic import BaseModel, Field
from typing import Optional, Any
import httpx
import json


class MCPTool(BaseModel):
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)


class MCPServerConfig(BaseModel):
    name: str
    url: str
    api_key: Optional[str] = None
    enabled: bool = True


class MCPServerClient:
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._tools: list[MCPTool] = []

    async def discover_tools(self) -> list[MCPTool]:
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
        except Exception:
            return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
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


class MCPRegistry:
    def __init__(self):
        self._servers: dict[str, MCPServerClient] = {}

    def register(self, config: MCPServerConfig):
        self._servers[config.name] = MCPServerClient(config)

    def unregister(self, name: str):
        if name in self._servers:
            del self._servers[name]

    def list_servers(self) -> list[dict]:
        return [
            {
                "name": s.config.name,
                "url": s.config.url,
                "enabled": s.config.enabled,
                "tools": [t.name for t in s.get_tools()],
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

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> Any:
        server = self._servers.get(server_name)
        if not server:
            return {"error": f"Server '{server_name}' not found"}
        if not server.config.enabled:
            return {"error": f"Server '{server_name}' is disabled"}
        return await server.call_tool(tool_name, arguments)

    async def discover_all(self):
        for server in self._servers.values():
            if server.config.enabled:
                await server.discover_tools()


mcp_registry = MCPRegistry()
