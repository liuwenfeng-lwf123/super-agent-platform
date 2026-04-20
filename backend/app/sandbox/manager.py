import asyncio
import tempfile
import os
import subprocess
import json
import shutil
from typing import Optional


class SandboxExecutor:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self._thread_workspaces: dict[str, str] = {}

    def get_thread_workspace(self, thread_id: str) -> str:
        if thread_id not in self._thread_workspaces:
            ws = os.path.join("./data", "workspaces", thread_id)
            for sub in ("uploads", "workspace", "outputs"):
                os.makedirs(os.path.join(ws, sub), exist_ok=True)
            self._thread_workspaces[thread_id] = ws
        return self._thread_workspaces[thread_id]

    def get_workspace_dir(self, thread_id: str) -> str:
        return os.path.join(self.get_thread_workspace(thread_id), "workspace")

    def get_outputs_dir(self, thread_id: str) -> str:
        return os.path.join(self.get_thread_workspace(thread_id), "outputs")

    def get_uploads_dir(self, thread_id: str) -> str:
        return os.path.join(self.get_thread_workspace(thread_id), "uploads")

    async def execute_python(
        self, code: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else None
        return await self._run_code(code, "python3", timeout, work_dir)

    async def execute_javascript(
        self, code: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else None
        return await self._run_code(code, "node", timeout, work_dir)

    async def execute_bash(
        self, command: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        timeout = timeout or self.timeout
        work_dir = self.get_workspace_dir(thread_id) if thread_id else "./data/workspaces/_default/workspace"
        os.makedirs(work_dir, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "success": False,
                    "output": "",
                    "error": f"Execution timed out after {timeout}s",
                    "exit_code": -1,
                }

            return {
                "success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace")[:20000],
                "error": stderr.decode("utf-8", errors="replace")[:5000],
                "exit_code": proc.returncode,
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "exit_code": -1,
            }

    async def write_file(self, path: str, content: str, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = os.path.normpath(os.path.join(work_dir, path))
        if not full_path.startswith(os.path.normpath(work_dir)):
            return {"success": False, "error": "Path traversal not allowed"}

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": full_path, "size": len(content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_file(self, path: str, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = os.path.normpath(os.path.join(work_dir, path))
        if not full_path.startswith(os.path.normpath(work_dir)):
            return {"success": False, "error": "Path traversal not allowed"}

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read(50000)
            return {"success": True, "content": content, "path": full_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_files(self, path: str = ".", thread_id: str = "") -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else "./data/workspaces/_default/workspace"
        target = os.path.normpath(os.path.join(work_dir, path))
        if not target.startswith(os.path.normpath(work_dir)):
            return {"success": False, "error": "Path traversal not allowed"}

        try:
            entries = []
            for entry in os.scandir(target):
                entries.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            entries.sort(key=lambda x: (not x["is_dir"], x["name"]))
            return {"success": True, "path": path, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def save_output(self, filename: str, content: str, thread_id: str) -> dict:
        outputs_dir = self.get_outputs_dir(thread_id)
        full_path = os.path.join(outputs_dir, filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": full_path, "filename": filename}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _run_code(
        self, code: str, runner: str, timeout: Optional[int], work_dir: Optional[str] = None
    ) -> dict:
        timeout = timeout or self.timeout
        suffix = ".py" if runner == "python3" else ".js"

        if work_dir:
            os.makedirs(work_dir, exist_ok=True)
            exec_filename = f"_sandbox_exec{suffix}"
            filepath = os.path.join(work_dir, exec_filename)
            with open(filepath, "w") as f:
                f.write(code)
            run_args = [runner, exec_filename]
        else:
            f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
            f.write(code)
            filepath = f.name
            f.close()
            run_args = [runner, filepath]

        try:
            proc = await asyncio.create_subprocess_exec(
                *run_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "success": False,
                    "output": "",
                    "error": f"Execution timed out after {timeout}s",
                    "exit_code": -1,
                }

            return {
                "success": proc.returncode == 0,
                "output": stdout.decode("utf-8", errors="replace")[:20000],
                "error": stderr.decode("utf-8", errors="replace")[:5000],
                "exit_code": proc.returncode,
            }
        except FileNotFoundError:
            return {
                "success": False,
                "output": "",
                "error": f"Runner '{runner}' not found",
                "exit_code": -1,
            }
        finally:
            if not work_dir:
                try:
                    os.unlink(filepath)
                except OSError:
                    pass


sandbox_executor = SandboxExecutor()
