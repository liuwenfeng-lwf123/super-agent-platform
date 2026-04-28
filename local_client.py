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
import logging
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import uuid
import argparse
from pathlib import Path

# ── Logging setup ──
LOG_DIR = os.path.join(os.path.expanduser("~"), ".tiangongflow")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "client.log")

logger = logging.getLogger("tiangongflow")
logger.setLevel(logging.DEBUG)
from logging.handlers import RotatingFileHandler
_fh = RotatingFileHandler(LOG_FILE, encoding="utf-8", maxBytes=5 * 1024 * 1024, backupCount=3)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setLevel(logging.WARNING)
logger.addHandler(_ch)

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


# Actions that are read-only and safe to auto-approve
AUTO_APPROVE_ACTIONS = {"get_system_info", "send_notification"}


class LocalClient:
    def __init__(self, server_url: str, auto_approve: bool = False, allowed_paths: list[str] = None):
        self.server_url = server_url
        self.client_id = f"local-{platform.node()}-{uuid.uuid4().hex[:8]}"
        self.auto_approve = auto_approve
        self.allowed_paths = allowed_paths or []
        self.running = True
        self._reconnect_delay = 3
        self._max_reconnect_delay = 60

    @staticmethod
    def _sanitize_applescript(s: str) -> str:
        """Escape a string for safe embedding in AppleScript double-quoted literals."""
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _is_path_allowed(self, path: str) -> bool:
        if not self.allowed_paths:
            return True
        abs_path = os.path.abspath(path)
        return any(
            abs_path == os.path.abspath(p) or abs_path.startswith(os.path.abspath(p) + os.sep)
            for p in self.allowed_paths
        )

    async def execute_bash(self, command: str, cwd: str = "", timeout: int = 120, ws=None, request_id: str = "") -> dict:
        try:
            exec_cwd = os.path.abspath(cwd) if cwd else None
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=exec_cwd,
            )

            stdout_chunks = []
            stderr_chunks = []

            async def _read_stream(stream, chunks, stream_name):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    chunks.append(text)
                    if ws and request_id:
                        try:
                            await ws.send(json.dumps({
                                "type": "stream_output",
                                "request_id": request_id,
                                "stream": stream_name,
                                "data": text,
                            }))
                        except Exception:
                            pass

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(proc.stdout, stdout_chunks, "stdout"),
                        _read_stream(proc.stderr, stderr_chunks, "stderr"),
                    ),
                    timeout=timeout,
                )
                await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {"success": False, "error": f"Command timed out ({timeout}s)"}

            stdout_str = "".join(stdout_chunks)[:50000]
            stderr_str = "".join(stderr_chunks)[:10000]

            return {
                "success": proc.returncode == 0,
                "output": stdout_str,
                "error": stderr_str,
                "exit_code": proc.returncode,
                "streamed": bool(ws and request_id),
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

    _MAX_BACKUPS_PER_FILE = 20

    @staticmethod
    def _cleanup_backups(backup_dir: str, filename_prefix: str, max_keep: int = 20):
        """Keep only the most recent `max_keep` backups for a given file."""
        try:
            baks = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(filename_prefix) and f.endswith(".bak")],
                reverse=True,
            )
            for old in baks[max_keep:]:
                os.unlink(os.path.join(backup_dir, old))
        except Exception:
            pass

    _BINARY_EXTENSIONS = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".tiff", ".heic",
        ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac", ".ogg",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".dat",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".sqlite", ".db", ".pkl", ".npy", ".npz", ".h5", ".hdf5",
    })

    @staticmethod
    def _is_binary(path: str) -> bool:
        ext = os.path.splitext(path)[1].lower()
        if ext in LocalClient._BINARY_EXTENSIONS:
            return True
        try:
            with open(path, "rb") as f:
                chunk = f.read(8192)
            return b"\x00" in chunk
        except Exception:
            return False

    async def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> dict:
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            if self._is_binary(abs_path):
                size = os.path.getsize(abs_path)
                ext = os.path.splitext(abs_path)[1].lower()
                return {"success": False, "error": f"Binary file ({ext}, {size} bytes). Cannot read as text. Use a specific tool for this file type."}
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            total = len(all_lines)
            if start_line > 0 or end_line > 0:
                sl = max(start_line, 1)
                el = end_line if end_line > 0 else total
                selected = all_lines[sl - 1 : el]
                numbered = "".join(
                    f"{sl + i:>6}\t{line}"
                    for i, line in enumerate(selected)
                )
                return {"success": True, "content": numbered, "path": abs_path, "total_lines": total, "showing": f"{sl}-{min(el, total)}"}
            else:
                content = "".join(all_lines[:5000])
                return {"success": True, "content": content, "path": abs_path, "total_lines": total}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def edit_file(self, path: str, old_string: str, new_string: str) -> dict:
        """Find-and-replace edit: replace old_string with new_string in file."""
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        if not old_string:
            return {"success": False, "error": "old_string cannot be empty"}
        if old_string == new_string:
            return {"success": False, "error": "old_string and new_string are identical — nothing to change"}
        try:
            abs_path = os.path.abspath(path)
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            count = content.count(old_string)
            if count == 0:
                return {"success": False, "error": "old_string not found in file"}
            if count > 1:
                return {"success": False, "error": f"old_string matches {count} locations — be more specific"}
            # Auto-backup before editing
            backup_dir = os.path.join(os.path.dirname(abs_path), ".super-agent-backups")
            os.makedirs(backup_dir, exist_ok=True)
            file_prefix = os.path.basename(abs_path) + "."
            backup_name = file_prefix + f"{time.time_ns()}.bak"
            with open(os.path.join(backup_dir, backup_name), "w", encoding="utf-8") as bf:
                bf.write(content)
            self._cleanup_backups(backup_dir, file_prefix, self._MAX_BACKUPS_PER_FILE)

            # Build diff preview: find the line number and show context
            lines = content.split("\n")
            match_pos = content.index(old_string)
            line_num = content[:match_pos].count("\n") + 1
            old_lines = old_string.split("\n")
            new_lines = new_string.split("\n")

            ctx_start = max(0, line_num - 3)
            ctx_end = min(len(lines), line_num + len(old_lines) + 2)
            context_before = [f"  {ctx_start+i+1:>4} | {lines[ctx_start+i]}" for i in range(line_num - 1 - ctx_start)]
            diff_old = [f"- {line_num+i:>4} | {l}" for i, l in enumerate(old_lines)]
            diff_new = [f"+ {line_num+i:>4} | {l}" for i, l in enumerate(new_lines)]
            context_after_start = line_num - 1 + len(old_lines)
            context_after = [f"  {context_after_start+i+1:>4} | {lines[context_after_start+i]}" for i in range(min(2, ctx_end - context_after_start))]
            diff_preview = "\n".join(context_before + diff_old + diff_new + context_after)

            # Write the file
            new_content = content.replace(old_string, new_string, 1)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return {"success": True, "path": abs_path, "replacements": 1, "line": line_num, "diff": diff_preview}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def undo_edit(self, path: str) -> dict:
        """Restore a file from the most recent backup in .super-agent-backups/."""
        try:
            abs_path = os.path.abspath(path)
            backup_dir = os.path.join(os.path.dirname(abs_path), ".super-agent-backups")
            if not os.path.isdir(backup_dir):
                return {"success": False, "error": "No backup directory found"}
            prefix = os.path.basename(abs_path) + "."
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(prefix) and f.endswith(".bak")],
                reverse=True,
            )
            if not backups:
                return {"success": False, "error": f"No backups found for {os.path.basename(abs_path)}"}
            latest = os.path.join(backup_dir, backups[0])
            with open(latest, "r", encoding="utf-8") as f:
                restored = f.read()
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(restored)
            os.unlink(latest)
            remaining = len(backups) - 1
            return {"success": True, "path": abs_path, "restored_from": backups[0], "remaining_backups": remaining}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def search_code(self, pattern: str, path: str = ".", include: str = "", max_results: int = 50) -> dict:
        """Search for a pattern in files using grep/ripgrep."""
        try:
            abs_path = os.path.abspath(path)
            cmd = []
            if shutil.which("rg"):
                cmd = ["rg", "--no-heading", "--line-number", "--max-count", "3",
                       "--max-filesize", "1M", "-m", str(max_results), "--color", "never", "-C", "2"]
                if include:
                    cmd += ["-g", include]
                cmd += [pattern, abs_path]
            else:
                cmd = ["grep", "-rn", "--max-count=3", "--color=never", "-C", "2"]
                if include:
                    cmd += ["--include", include]
                cmd += [pattern, abs_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            # rg/grep exit code 1 = no matches (not an error), 2+ = real error
            if proc.returncode and proc.returncode >= 2:
                return {"success": False, "error": stderr.decode("utf-8", errors="replace")[:5000]}
            output = stdout.decode("utf-8", errors="replace")[:50000]
            lines = output.strip().split("\n") if output.strip() else []
            return {"success": True, "matches": len(lines), "output": output, "tool": "rg" if shutil.which("rg") else "grep"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def git_command(self, args: str, cwd: str = ".") -> dict:
        """Run a git command."""
        if not self._is_path_allowed(cwd):
            return {"success": False, "error": f"Path not in allowed list: {cwd}"}
        try:
            import shlex
            abs_cwd = os.path.abspath(cwd)
            parsed_args = shlex.split(args)
            proc = await asyncio.create_subprocess_exec(
                "git", *parsed_args, cwd=abs_cwd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {
                "success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace")[:50000],
                "error": stderr.decode("utf-8", errors="replace")[:5000],
                "exit_code": proc.returncode,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    _index_cache: dict = {}  # {abs_path: {"result": ..., "ts": float}}
    _INDEX_CACHE_TTL = 300  # 5 minutes

    async def project_index(self, path: str = ".") -> dict:
        """Build a project structure index: file tree + language stats."""
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            cached = self._index_cache.get(abs_path)
            if cached and (time.time() - cached["ts"]) < self._INDEX_CACHE_TTL:
                return {**cached["result"], "cached": True}
            tree_lines = []
            ext_counts: dict[str, int] = {}
            total_files = 0
            for root, dirs, files in os.walk(abs_path):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build", ".tox"}]
                rel = os.path.relpath(root, abs_path)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > 4:
                    continue
                indent = "  " * depth
                dirname = os.path.basename(root)
                tree_lines.append(f"{indent}{dirname}/")
                for fname in sorted(files)[:30]:
                    total_files += 1
                    ext = os.path.splitext(fname)[1].lower()
                    if ext:
                        ext_counts[ext] = ext_counts.get(ext, 0) + 1
                    tree_lines.append(f"{indent}  {fname}")
                if total_files > 500:
                    tree_lines.append("... (truncated)")
                    break
            lang_stats = sorted(ext_counts.items(), key=lambda x: -x[1])[:15]
            lang_summary = ", ".join(f"{ext}({c})" for ext, c in lang_stats)
            result = {
                "success": True,
                "tree": "\n".join(tree_lines[:300]),
                "total_files": total_files,
                "languages": lang_summary,
                "path": abs_path,
            }
            self._index_cache[abs_path] = {"result": result, "ts": time.time()}
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def write_file(self, path: str, content: str) -> dict:
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            # Auto-backup existing file before overwrite
            if os.path.isfile(abs_path):
                backup_dir = os.path.join(os.path.dirname(abs_path), ".super-agent-backups")
                os.makedirs(backup_dir, exist_ok=True)
                file_prefix = os.path.basename(abs_path) + "."
                backup_name = file_prefix + f"{time.time_ns()}.bak"
                with open(abs_path, "r", encoding="utf-8", errors="replace") as rf:
                    old_content = rf.read()
                with open(os.path.join(backup_dir, backup_name), "w", encoding="utf-8") as bf:
                    bf.write(old_content)
                self._cleanup_backups(backup_dir, file_prefix, self._MAX_BACKUPS_PER_FILE)
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

    async def upload_file(self, path: str) -> dict:
        """Read a local file as base64 for transfer to the workspace."""
        import base64
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            file_size = os.path.getsize(abs_path)
            if file_size > 50 * 1024 * 1024:
                return {"success": False, "error": f"File too large ({file_size} bytes, max 50MB)"}
            with open(abs_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            return {
                "success": True,
                "data": data,
                "filename": os.path.basename(abs_path),
                "size": file_size,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def download_file(self, path: str, data: str) -> dict:
        """Write base64-encoded data to a local file."""
        import base64
        if not self._is_path_allowed(path):
            return {"success": False, "error": f"Path not in allowed list: {path}"}
        try:
            abs_path = os.path.abspath(path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            raw = base64.b64decode(data)
            with open(abs_path, "wb") as f:
                f.write(raw)
            return {"success": True, "path": abs_path, "size": len(raw)}
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

    async def read_clipboard(self) -> dict:
        system = platform.system()
        try:
            if system == "Darwin":
                proc = await asyncio.create_subprocess_exec(
                    "pbpaste", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                return {"success": True, "content": stdout.decode("utf-8", errors="replace")[:50000]}
            elif system == "Windows":
                result = await self.execute_python(
                    "import subprocess; r=subprocess.run(['powershell','-c','Get-Clipboard'],capture_output=True,text=True); print(r.stdout)"
                )
                return {"success": True, "content": result.get("output", "")}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "xclip", "-selection", "clipboard", "-o",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                return {"success": True, "content": stdout.decode("utf-8", errors="replace")[:50000]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def manage_window(self, app_name: str, action: str, params: dict | None = None) -> dict:
        system = platform.system()
        if system != "Darwin":
            return {"success": False, "error": "Window management is only supported on macOS"}
        params = params or {}
        safe_app = self._sanitize_applescript(app_name)
        try:
            if action == "move":
                x, y = int(params.get("x", 0)), int(params.get("y", 0))
                w, h = int(params.get("width", 800)), int(params.get("height", 600))
                script = f'''
                tell application "{safe_app}"
                    activate
                    set bounds of front window to {{{x}, {y}, {x + w}, {y + h}}}
                end tell'''
            elif action == "minimize":
                script = f'tell application "{safe_app}" to set miniaturized of front window to true'
            elif action == "maximize":
                script = f'''
                tell application "Finder" to set _b to bounds of window of desktop
                tell application "{safe_app}"
                    activate
                    set bounds of front window to _b
                end tell'''
            elif action == "close":
                script = f'tell application "{safe_app}" to close front window'
            elif action == "fullscreen":
                script = f'''
                tell application "System Events" to tell process "{safe_app}"
                    set frontmost to true
                    keystroke "f" using {{control down, command down}}
                end tell'''
            elif action == "list":
                script = f'''
                tell application "System Events" to tell process "{safe_app}"
                    set _names to name of every window
                end tell
                return _names'''
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e", script,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
                return {"success": True, "windows": stdout.decode("utf-8", errors="replace").strip()}
            else:
                return {"success": False, "error": f"Unknown window action: {action}"}
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
            if proc.returncode != 0:
                return {"success": False, "error": stderr.decode("utf-8", errors="replace")}
            return {"success": True, "app": app_name, "action": action}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_notification(self, title: str, message: str) -> dict:
        system = platform.system()
        try:
            if system == "Darwin":
                safe_title = self._sanitize_applescript(title)
                safe_message = self._sanitize_applescript(message)
                script = f'display notification "{safe_message}" with title "{safe_title}"'
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e", script,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                return {"success": True}
            elif system == "Windows":
                ps = f"""[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName('text')
$textNodes.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null
$textNodes.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TianGongFlow').Show($toast)"""
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-c", ps,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                return {"success": True}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "notify-send", title, message,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def write_clipboard(self, content: str) -> dict:
        system = platform.system()
        try:
            if system == "Darwin":
                proc = await asyncio.create_subprocess_exec(
                    "pbcopy", stdin=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(input=content.encode("utf-8")), timeout=5)
                return {"success": True, "length": len(content)}
            elif system == "Windows":
                import tempfile
                with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                    f.write(content)
                    path = f.name
                proc = await asyncio.create_subprocess_exec(
                    "powershell", "-c", f"Get-Content '{path}' | Set-Clipboard",
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                os.unlink(path)
                return {"success": True, "length": len(content)}
            else:
                proc = await asyncio.create_subprocess_exec(
                    "xclip", "-selection", "clipboard",
                    stdin=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(input=content.encode("utf-8")), timeout=5)
                return {"success": True, "length": len(content)}
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
        elif action == "upload_file":
            return f"  Upload to workspace: {params.get('path', '')}"
        elif action == "download_file":
            return f"  Download to local: {params.get('path', '')}"
        elif action == "get_system_info":
            return "  Get system info (read-only)"
        elif action == "read_clipboard":
            return "  Read clipboard contents"
        elif action == "write_clipboard":
            preview = params.get('content', '')[:80]
            return f"  Write to clipboard: {preview}..."
        elif action == "send_notification":
            return f"  Notify: {params.get('title', '')} - {params.get('message', '')[:60]}"
        elif action == "manage_window":
            return f"  Window: {params.get('action', '?')} {params.get('app_name', '?')}"
        elif action == "edit_file":
            return f"  Edit file: {params.get('path', '')} (replace {len(params.get('old_string', ''))} chars)"
        elif action == "search_code":
            return f"  Search: '{params.get('pattern', '')}' in {params.get('path', '.')}"
        elif action == "git_command":
            return f"  Git: git {params.get('args', '')[:80]}"
        elif action == "project_index":
            return f"  Index project: {params.get('path', '.')}"
        elif action == "undo_edit":
            return f"  Undo edit: {params.get('path', '')}"
        return f"  {action}: {json.dumps(params)[:100]}"

    async def ask_approval(self, action: str, params: dict, request_auto_approve: bool = False) -> bool:
        print(f"\n  [AI Request] {action}")
        print(self._format_request_for_display(action, params))

        if self.auto_approve or request_auto_approve or action in AUTO_APPROVE_ACTIONS:
            print("  [Auto-approved]")
            return True

        loop = asyncio.get_event_loop()
        while True:
            try:
                response = await loop.run_in_executor(
                    None, lambda: input("  Allow? [y/n/a(lways)/q(uit)]: ").strip().lower()
                )
            except EOFError:
                return False
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
        logger.info(f"Request {request_id}: action={action} params_keys={list(params.keys())}")

        approved = await self.ask_approval(action, params, bool(data.get("auto_approve")))
        if not approved:
            await ws.send(json.dumps({
                "type": "rejection",
                "request_id": request_id,
                "reason": "User denied",
            }))
            logger.info(f"Request {request_id}: DENIED by user")
            print("  [Denied]")
            return

        handlers = {
            "execute_bash": lambda: self.execute_bash(params.get("command", ""), cwd=params.get("cwd", ""), timeout=params.get("timeout", 120), ws=ws, request_id=request_id),
            "execute_python": lambda: self.execute_python(params.get("code", "")),
            "read_file": lambda: self.read_file(params.get("path", ""), params.get("start_line", 0), params.get("end_line", 0)),
            "write_file": lambda: self.write_file(params.get("path", ""), params.get("content", "")),
            "list_files": lambda: self.list_files(params.get("path", ".")),
            "open_app": lambda: self.open_app(params.get("app_name", "")),
            "upload_file": lambda: self.upload_file(params.get("path", "")),
            "download_file": lambda: self.download_file(params.get("path", ""), params.get("data", "")),
            "get_system_info": lambda: self.get_system_info(),
            "read_clipboard": lambda: self.read_clipboard(),
            "write_clipboard": lambda: self.write_clipboard(params.get("content", "")),
            "send_notification": lambda: self.send_notification(params.get("title", "天工流"), params.get("message", "")),
            "manage_window": lambda: self.manage_window(params.get("app_name", ""), params.get("action", ""), params.get("params", {})),
            "edit_file": lambda: self.edit_file(params.get("path", ""), params.get("old_string", ""), params.get("new_string", "")),
            "search_code": lambda: self.search_code(params.get("pattern", ""), params.get("path", "."), params.get("include", ""), params.get("max_results", 50)),
            "git_command": lambda: self.git_command(params.get("args", ""), params.get("cwd", ".")),
            "project_index": lambda: self.project_index(params.get("path", ".")),
            "undo_edit": lambda: self.undo_edit(params.get("path", "")),
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
        logger.info(f"Request {request_id}: {status} {output_preview}")
        print(f"  [{status}] {output_preview}")

    async def _backoff_sleep(self, reason: str):
        """Exponential backoff with jitter for reconnection."""
        jitter = random.uniform(0, self._reconnect_delay * 0.3)
        wait = self._reconnect_delay + jitter
        print(f"\n  {reason}. Reconnecting in {wait:.1f}s...")
        logger.warning(f"Reconnect: {reason} (wait={wait:.1f}s)")
        await asyncio.sleep(wait)
        self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

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

                    self._reconnect_delay = 3  # reset on successful connect
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            try:
                                await ws.send(json.dumps({"type": "ping"}))
                            except Exception:
                                break
                            continue

                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "request":
                            asyncio.create_task(self.handle_request(ws, data))
                        elif msg_type == "ping":
                            await ws.send(json.dumps({"type": "pong"}))
                        elif msg_type == "pong":
                            pass  # expected response to our ping

            except websockets.exceptions.ConnectionClosed:
                await self._backoff_sleep("⚡ Connection lost")
            except (ConnectionRefusedError, OSError) as e:
                await self._backoff_sleep(f"🔌 Server unavailable ({e})")
            except Exception as e:
                await self._backoff_sleep(f"❌ Error: {e}")

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
        "--yes",
        action="store_true",
        help="Skip the auto-approve startup confirmation",
    )
    parser.add_argument(
        "--allow",
        default="",
        help="Comma-separated list of allowed paths (default: all paths)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("API_SECRET_TOKEN", ""),
        help="API secret token for server auth (or set API_SECRET_TOKEN env var)",
    )
    args = parser.parse_args()

    allowed_paths = [p.strip() for p in args.allow.split(",") if p.strip()] if args.allow else []

    if args.auto_approve and not args.yes:
        print("  WARNING: Auto-approve is enabled. AI commands will execute without confirmation!")
        confirm = input("  Continue? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            sys.exit(0)

    server_url = args.server
    if args.token:
        sep = "&" if "?" in server_url else "?"
        server_url += f"{sep}token={args.token}"

    client = LocalClient(
        server_url=server_url,
        auto_approve=args.auto_approve,
        allowed_paths=allowed_paths,
    )

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\n  Shutting down...")


if __name__ == "__main__":
    main()
