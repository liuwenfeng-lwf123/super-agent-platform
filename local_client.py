#!/usr/bin/env python3
"""
TianGongFlow Local Client - Runs on the user's computer
Connects to the TianGongFlow backend via WebSocket and executes AI commands locally.

Usage:
  python local_client.py [--server URL] [--auto-approve] [--allow PATH,...]

Examples:
  python local_client.py
  python local_client.py --server ws://192.168.1.100:8001/ws/local-client
  python local_client.py --auto-approve
  python local_client.py --allow /home/user/projects,/tmp
"""

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
import argparse
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets


BANNER = r"""
  _____ _   _    ____ ___  _   _  ____  ____   ___  ____ _____
 |_   _| | | |  / ___/ _ \| \ | |/ ___|/ ___| / _ \|  _ \_   _|
   | | | |_| | | |  | | | |  \| | |  _| |    | | | | |_) || |
   | | |  _  | | |__| |_| | |\  | |_| | |___ | |_| |  _ < | |
   |_| |_| |_|  \____\___/|_| \_|\____|\____| \___/|_| \_\|_|

  Local Mode Client v1.0
"""


class LocalClient:
    def __init__(self, server_url: str, auto_approve: bool = False, allowed_paths: list[str] = None):
        self.server_url = server_url
        self.client_id = f"local-{platform.node()}-{uuid.uuid4().hex[:8]}"
        self.auto_approve = auto_approve
        self.allowed_paths = allowed_paths or []
        self.running = True

    def _is_path_allowed(self, path: str) -> bool:
        if not self.allowed_paths:
            return True
        abs_path = os.path.abspath(path)
        return any(abs_path.startswith(os.path.abspath(p)) for p in self.allowed_paths)

    async def execute_bash(self, command: str) -> dict:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"success": False, "error": "Command timed out (120s)"}

            return {
                "success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace")[:50000],
                "error": stderr.decode("utf-8", errors="replace")[:10000],
                "exit_code": proc.returncode,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def execute_python(self, code: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            filepath = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"success": False, "error": "Execution timed out (120s)"}

            return {
                "success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace")[:50000],
                "error": stderr.decode("utf-8", errors="replace")[:10000],
                "exit_code": proc.returncode,
            }
        finally:
            try:
                os.unlink(filepath)
            except OSError:
                pass

    async def read_file(self, path: str) -> dict:
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(100000)
            return {"success": True, "content": content, "path": abs_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def write_file(self, path: str, content: str) -> dict:
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": abs_path, "size": len(content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_files(self, path: str = ".") -> dict:
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            entries = []
            for entry in os.scandir(abs_path):
                try:
                    entries.append({
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": entry.stat().st_size if entry.is_file() else 0,
                    })
                except (PermissionError, OSError):
                    entries.append({"name": entry.name, "is_dir": False, "size": 0})
            entries.sort(key=lambda x: (not x["is_dir"], x["name"]))
            return {"success": True, "path": path, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def open_app(self, app_name: str) -> dict:
        system = platform.system()
        try:
            if system == "Darwin":
                proc = await asyncio.create_subprocess_exec("open", "-a", app_name)
            elif system == "Windows":
                proc = await asyncio.create_subprocess_exec("start", "", app_name, shell=True)
            else:
                proc = await asyncio.create_subprocess_exec("xdg-open", app_name)
            await proc.wait()
            return {"success": True, "app": app_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_system_info(self) -> dict:
        try:
            disk = shutil.disk_usage("/")
            info = {
                "os": platform.system(),
                "os_version": platform.version(),
                "hostname": platform.node(),
                "arch": platform.machine(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "disk_total_gb": round(disk.total / (1024**3), 1),
                "disk_used_gb": round(disk.used / (1024**3), 1),
                "disk_free_gb": round(disk.free / (1024**3), 1),
                "home_dir": str(Path.home()),
                "cwd": os.getcwd(),
            }
            return {"success": True, "info": json.dumps(info, indent=2)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _format_request_for_display(self, action: str, params: dict) -> str:
        if action == "execute_bash":
            return f"  $ {params.get('command', '')}"
        elif action == "read_file":
            return f"  Read: {params.get('path', '')}"
        elif action == "write_file":
            content = params.get('content', '')
            preview = content[:80] + '...' if len(content) > 80 else content
            return f"  Write: {params.get('path', '')}\n  Content: {preview}"
        elif action == "list_files":
            return f"  List: {params.get('path', '.')}"
        elif action == "execute_python":
            code = params.get('code', '')
            preview = code[:100] + '...' if len(code) > 100 else code
            return f"  Python:\n  {preview}"
        elif action == "open_app":
            return f"  Open app: {params.get('app_name', '')}"
        elif action == "get_system_info":
            return "  Get system info (read-only)"
        return f"  {action}: {json.dumps(params)[:100]}"

    async def ask_approval(self, action: str, params: dict) -> bool:
        print(f"\n  [AI Request] {action}")
        print(self._format_request_for_display(action, params))

        if self.auto_approve:
            print("  [Auto-approved]")
            return True

        while True:
            response = input("  Allow? [y/n/a(lways)/q(uit)]: ").strip().lower()
            if response in ("y", "yes"):
                return True
            elif response in ("n", "no"):
                return False
            elif response in ("a", "always"):
                self.auto_approve = True
                return True
            elif response in ("q", "quit"):
                self.running = False
                return False
            print("  Please enter y/n/a/q")

    async def handle_request(self, ws, data: dict):
        action = data.get("action", "")
        params = data.get("params", {})
        request_id = data.get("request_id")

        approved = await self.ask_approval(action, params)
        if not approved:
            await ws.send(json.dumps({
                "type": "rejection",
                "request_id": request_id,
                "reason": "User denied",
            }))
            print("  [Denied]")
            return

        handlers = {
            "execute_bash": lambda: self.execute_bash(params.get("command", "")),
            "execute_python": lambda: self.execute_python(params.get("code", "")),
            "read_file": lambda: self.read_file(params.get("path", "")),
            "write_file": lambda: self.write_file(params.get("path", ""), params.get("content", "")),
            "list_files": lambda: self.list_files(params.get("path", ".")),
            "open_app": lambda: self.open_app(params.get("app_name", "")),
            "get_system_info": lambda: self.get_system_info(),
        }

        handler = handlers.get(action)
        if not handler:
            result = {"success": False, "error": f"Unknown action: {action}"}
        else:
            result = await handler()

        await ws.send(json.dumps({
            "type": "response",
            "request_id": request_id,
            "result": result,
        }))

        status = "OK" if result.get("success") else "FAIL"
        output_preview = ""
        if result.get("output"):
            output_preview = result["output"][:60].replace("\n", " ")
        elif result.get("error"):
            output_preview = f"Error: {result['error'][:60]}"
        print(f"  [{status}] {output_preview}")

    async def run(self):
        print(BANNER)
        print(f"  Client ID: {self.client_id}")
        print(f"  Server: {self.server_url}")
        print(f"  Auto-approve: {self.auto_approve}")
        if self.allowed_paths:
            print(f"  Allowed paths: {', '.join(self.allowed_paths)}")
        print()

        while self.running:
            try:
                async with websockets.connect(self.server_url) as ws:
                    await ws.send(json.dumps({
                        "type": "register",
                        "client_id": self.client_id,
                        "info": {
                            "hostname": platform.node(),
                            "os": platform.system(),
                            "os_version": platform.version(),
                            "arch": platform.machine(),
                            "python": platform.python_version(),
                            "home": str(Path.home()),
                        },
                    }))

                    response = await ws.recv()
                    reg_data = json.loads(response)
                    if reg_data.get("type") == "registered":
                        print(f"  Connected! {reg_data.get('message', '')}")
                        print()
                        print("  Waiting for AI commands... (Ctrl+C to disconnect)")
                        print("  ─" * 40)
                    else:
                        print(f"  Registration failed: {reg_data}")
                        return

                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({"type": "ping"}))
                            continue

                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "request":
                            asyncio.create_task(self.handle_request(ws, data))
                        elif msg_type == "ping":
                            await ws.send(json.dumps({"type": "pong"}))

            except websockets.exceptions.ConnectionClosed:
                print("\n  Connection lost. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except ConnectionRefusedError:
                print("  Cannot connect to server. Retrying in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"  Error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

        print("\n  Disconnected. Goodbye!")


def main():
    parser = argparse.ArgumentParser(description="TianGongFlow Local Client")
    parser.add_argument(
        "--server",
        default="ws://localhost:8001/ws/local-client",
        help="WebSocket server URL (default: ws://localhost:8001/ws/local-client)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve all AI commands (use with caution!)",
    )
    parser.add_argument(
        "--allow",
        default="",
        help="Comma-separated list of allowed paths (default: all paths)",
    )
    args = parser.parse_args()

    allowed_paths = [p.strip() for p in args.allow.split(",") if p.strip()] if args.allow else []

    if args.auto_approve:
        print("  WARNING: Auto-approve is enabled. AI commands will execute without confirmation!")
        confirm = input("  Continue? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            sys.exit(0)

    client = LocalClient(
        server_url=args.server,
        auto_approve=args.auto_approve,
        allowed_paths=allowed_paths,
    )

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\n  Shutting down...")


if __name__ == "__main__":
    main()
