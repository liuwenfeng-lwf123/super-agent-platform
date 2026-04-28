import asyncio
import difflib
import tempfile
import os
import subprocess
import json
import shutil
import hashlib
import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)


class SandboxExecutor:
    # Sentinel line appended to commands to capture cwd after execution
    _CWD_SENTINEL = "__HERMES_CWD__"

    def __init__(self, timeout: int = 60, persist_bash_env: bool = False):
        self.timeout = timeout
        self.persist_bash_env = persist_bash_env
        self._thread_workspaces: dict[str, str] = {}
        self._shadow_workspaces: dict[str, dict] = {}
        self._thread_cwd: dict[str, str] = {}  # per-thread persistent cwd
        self._thread_env: dict[str, dict[str, str]] = {}  # per-thread persistent env vars

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

    def get_env_bridge_file(self, thread_id: Optional[str], work_dir: str) -> str:
        if thread_id:
            return os.path.abspath(os.path.join(self.get_thread_workspace(thread_id), "claude_env.sh"))
        return os.path.abspath(os.path.join(work_dir, ".claude_env.sh"))

    def os_sandbox_available(self) -> bool:
        if sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec"):
            return True
        if sys.platform == "linux" and shutil.which("bwrap"):
            return True
        return False

    def _canonical_path(self, path: str) -> str:
        return os.path.normcase(os.path.realpath(path))

    def _is_within_root(self, root_dir: str, candidate: str) -> bool:
        try:
            root_real = self._canonical_path(root_dir)
            candidate_real = self._canonical_path(candidate)
            return os.path.commonpath([root_real, candidate_real]) == root_real
        except Exception as e:
            logger.debug("Suppressed error in manager: %s", e)
            return False

    def _safe_root_path(self, root_dir: str, rel_path: str) -> str | None:
        base = os.path.realpath(root_dir)
        candidate = os.path.join(base, rel_path)
        resolved = os.path.realpath(candidate)
        if not self._is_within_root(base, resolved):
            return None
        return resolved

    def resolve_workspace_path(self, thread_id: str, rel_path: str) -> str | None:
        return self._safe_root_path(self.get_workspace_dir(thread_id), rel_path)

    def resolve_outputs_path(self, thread_id: str, rel_path: str) -> str | None:
        return self._safe_root_path(self.get_outputs_dir(thread_id), rel_path)

    def _sandbox_roots(self, work_dir: str, thread_id: Optional[str]) -> tuple[str, str, str]:
        if thread_id:
            thread_root = os.path.realpath(self.get_thread_workspace(thread_id))
        else:
            thread_root = os.path.realpath(os.path.dirname(work_dir))
        sandbox_tmp = os.path.join(thread_root, ".sandbox_tmp")
        cache_root = os.path.join(thread_root, ".cache")
        os.makedirs(sandbox_tmp, exist_ok=True)
        os.makedirs(cache_root, exist_ok=True)
        return thread_root, sandbox_tmp, cache_root

    def _build_isolated_env(self, base_env: dict[str, str], work_dir: str, thread_id: Optional[str]) -> dict[str, str]:
        env = dict(base_env)
        thread_root, sandbox_tmp, cache_root = self._sandbox_roots(work_dir, thread_id)
        env["HOME"] = thread_root
        env["TMPDIR"] = sandbox_tmp
        env["XDG_CACHE_HOME"] = cache_root
        env["PIP_CACHE_DIR"] = os.path.join(cache_root, "pip")
        env["PYTHONPYCACHEPREFIX"] = os.path.join(cache_root, "pycache")
        env["npm_config_cache"] = os.path.join(cache_root, "npm")
        env["npm_config_userconfig"] = os.path.join(thread_root, ".npmrc")
        return env

    def _wrap_with_os_sandbox(
        self,
        command_args: list[str],
        *,
        work_dir: Optional[str],
        thread_id: Optional[str],
    ) -> list[str]:
        if not work_dir or not self.os_sandbox_available():
            return command_args
        if sys.platform == "linux":
            return self._wrap_with_bwrap(command_args, work_dir=work_dir, thread_id=thread_id)
        return self._wrap_with_darwin_sandbox(command_args, work_dir=work_dir, thread_id=thread_id)

    def _wrap_with_darwin_sandbox(
        self,
        command_args: list[str],
        *,
        work_dir: Optional[str],
        thread_id: Optional[str],
    ) -> list[str]:
        thread_root, sandbox_tmp, cache_root = self._sandbox_roots(work_dir, thread_id)
        write_roots = sorted({
            os.path.realpath(work_dir),
            os.path.realpath(thread_root),
            os.path.realpath(sandbox_tmp),
            os.path.realpath(cache_root),
        })
        profile_lines = [
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(allow process*)",
            "(allow signal (target self))",
            "(allow file-read*)",
            "(allow network*)",
            "(allow file-write*",
        ]
        profile_lines.extend(f"    (subpath {json.dumps(root)})" for root in write_roots)
        profile_lines.append(")")
        return ["/usr/bin/sandbox-exec", "-p", "\n".join(profile_lines), *command_args]

    def _wrap_with_bwrap(
        self,
        command_args: list[str],
        *,
        work_dir: Optional[str],
        thread_id: Optional[str],
    ) -> list[str]:
        """Wrap command with bubblewrap (bwrap) for Linux sandboxing.

        Policy: read-only bind of /, read-write bind of work_dir and
        thread workspace dirs, isolated /tmp, access to /dev and /proc.
        Network access is allowed (--share-net).
        """
        thread_root, sandbox_tmp, cache_root = self._sandbox_roots(work_dir, thread_id)
        write_roots = sorted({
            os.path.realpath(work_dir),
            os.path.realpath(thread_root),
            os.path.realpath(sandbox_tmp),
            os.path.realpath(cache_root),
        })
        bwrap_args = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--share-net",
            "--die-with-parent",
            "--unshare-pid",
        ]
        for root in write_roots:
            bwrap_args.extend(["--bind", root, root])
        bwrap_args.extend(command_args)
        logger.debug("bwrap sandbox: write_roots=%s", write_roots)
        return bwrap_args

    def _file_hash(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _snapshot_workspace_dir(self, work_dir: str) -> dict[str, dict]:
        snapshot: dict[str, dict] = {}
        if not os.path.isdir(work_dir):
            return snapshot
        for root, _, files in os.walk(work_dir):
            for name in files:
                rel_path = os.path.relpath(os.path.join(root, name), work_dir)
                if rel_path in {"_sandbox_exec.py", "_sandbox_exec.js"}:
                    continue
                full_path = os.path.join(work_dir, rel_path)
                snapshot[rel_path] = {
                    "sha256": self._file_hash(full_path),
                    "size": os.path.getsize(full_path),
                }
        return snapshot

    def _safe_workspace_path(self, work_dir: str, rel_path: str) -> str | None:
        return self._safe_root_path(work_dir, rel_path)

    def _read_text_diff_source(self, full_path: str, max_bytes: int = 100_000) -> tuple[str, bool, bool]:
        if not full_path or not os.path.exists(full_path):
            return "", False, False
        try:
            with open(full_path, "rb") as f:
                data = f.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return "", True, truncated
            if "\x00" in text:
                return "", True, truncated
            return text, False, truncated
        except Exception as e:
            logger.debug("Suppressed error in manager: %s", e)
            return "", True, False

    def _read_full_text_file(self, full_path: str) -> tuple[str, bool]:
        if not full_path or not os.path.exists(full_path):
            return "", False
        try:
            with open(full_path, "rb") as f:
                data = f.read()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return "", True
            if "\x00" in text:
                return "", True
            return text, False
        except Exception as e:
            logger.debug("Suppressed error in manager: %s", e)
            return "", True

    def _format_unified_range(self, start: int, stop: int) -> str:
        length = stop - start
        if length == 1:
            return str(start + 1)
        if length == 0:
            return f"{start},0"
        return f"{start + 1},{length}"

    def _build_text_hunks(
        self,
        before_text: str,
        after_text: str,
        *,
        context_lines: int = 3,
        max_hunk_chars: int = 8_000,
    ) -> list[dict]:
        before_lines = before_text.splitlines(keepends=True)
        after_lines = after_text.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
        hunks: list[dict] = []

        for index, group in enumerate(matcher.get_grouped_opcodes(context_lines)):
            first, last = group[0], group[-1]
            old_start, old_end = first[1], last[2]
            new_start, new_end = first[3], last[4]
            header = (
                f"@@ -{self._format_unified_range(old_start, old_end)} "
                f"+{self._format_unified_range(new_start, new_end)} @@"
            )
            lines: list[dict] = []
            display_lines = [header]

            for tag, i1, i2, j1, j2 in group:
                if tag in {"equal", "replace", "delete"}:
                    for line in before_lines[i1:i2]:
                        content = line.rstrip("\r\n")
                        if tag == "equal":
                            lines.append({"type": "context", "content": content})
                            display_lines.append(f" {content}")
                        else:
                            lines.append({"type": "del", "content": content})
                            display_lines.append(f"-{content}")
                if tag in {"replace", "insert"}:
                    for line in after_lines[j1:j2]:
                        content = line.rstrip("\r\n")
                        lines.append({"type": "add", "content": content})
                        display_lines.append(f"+{content}")

            diff_text = "\n".join(display_lines)
            truncated = False
            if len(diff_text) > max_hunk_chars:
                diff_text = diff_text[:max_hunk_chars] + "\n... (hunk truncated)"
                truncated = True

            hunks.append(
                {
                    "id": f"hunk-{index}",
                    "header": header,
                    "old_start": old_start + 1,
                    "old_count": old_end - old_start,
                    "new_start": new_start + 1,
                    "new_count": new_end - new_start,
                    "diff": diff_text,
                    "lines": lines,
                    "truncated": truncated,
                }
            )

        return hunks

    def _normalize_hunk_selections(self, hunks: list[dict] | None) -> dict[str, set[str]]:
        selections: dict[str, set[str]] = {}
        if not hunks:
            return selections
        for entry in hunks:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", "")).strip().lstrip("./")
            raw_ids = entry.get("ids")
            if not path or not isinstance(raw_ids, list):
                continue
            ids = {str(value).strip() for value in raw_ids if str(value).strip()}
            if ids:
                selections[path] = ids
        return selections

    def _build_selected_hunk_content(
        self,
        before_text: str,
        after_text: str,
        selected_hunk_ids: set[str],
        *,
        context_lines: int = 3,
    ) -> str:
        before_lines = before_text.splitlines(keepends=True)
        after_lines = after_text.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
        grouped = list(matcher.get_grouped_opcodes(context_lines))
        result: list[str] = []
        cursor = 0

        for index, group in enumerate(grouped):
            old_start, old_end = group[0][1], group[-1][2]
            new_start, new_end = group[0][3], group[-1][4]
            result.extend(before_lines[cursor:old_start])
            if f"hunk-{index}" in selected_hunk_ids:
                result.extend(after_lines[new_start:new_end])
            else:
                result.extend(before_lines[old_start:old_end])
            cursor = old_end

        result.extend(before_lines[cursor:])
        return "".join(result)

    def is_shadow_thread(self, thread_id: str) -> bool:
        return thread_id in self._shadow_workspaces

    def create_shadow_workspace(self, base_thread_id: str, shadow_thread_id: str) -> dict:
        base_workspace = self.get_workspace_dir(base_thread_id)
        shadow_root = os.path.join("./data", "workspaces", shadow_thread_id)
        if os.path.isdir(shadow_root):
            shutil.rmtree(shadow_root)
        self._thread_workspaces.pop(shadow_thread_id, None)
        shadow_workspace = self.get_workspace_dir(shadow_thread_id)
        os.makedirs(shadow_workspace, exist_ok=True)
        if os.path.isdir(base_workspace):
            shutil.copytree(base_workspace, shadow_workspace, dirs_exist_ok=True)
        self._shadow_workspaces[shadow_thread_id] = {
            "shadow_thread_id": shadow_thread_id,
            "base_thread_id": base_thread_id,
            "base_snapshot": self._snapshot_workspace_dir(base_workspace),
        }
        return {
            "shadow_thread_id": shadow_thread_id,
            "base_thread_id": base_thread_id,
            "workspace_dir": shadow_workspace,
        }

    def get_shadow_info(self, shadow_thread_id: str) -> dict | None:
        record = self._shadow_workspaces.get(shadow_thread_id)
        if record is None:
            return None
        return dict(record)

    def list_shadow_changes(self, shadow_thread_id: str) -> dict:
        record = self._shadow_workspaces.get(shadow_thread_id)
        if record is None:
            return {"error": "Shadow workspace not found"}
        base_thread_id = record["base_thread_id"]
        base_workspace = self.get_workspace_dir(base_thread_id)
        shadow_workspace = self.get_workspace_dir(shadow_thread_id)
        base_snapshot = dict(record.get("base_snapshot", {}))
        current_base_snapshot = self._snapshot_workspace_dir(base_workspace)
        shadow_snapshot = self._snapshot_workspace_dir(shadow_workspace)

        changes: list[dict] = []
        conflicts: list[dict] = []

        for rel_path, meta in shadow_snapshot.items():
            original = base_snapshot.get(rel_path)
            if original == meta:
                continue
            status = "added" if original is None else "modified"
            entry = {"path": rel_path, "status": status, "size": meta.get("size", 0)}
            current_base = current_base_snapshot.get(rel_path)
            if current_base != original:
                entry["conflict"] = True
                conflicts.append({"path": rel_path, "status": status})
            changes.append(entry)

        for rel_path, original in base_snapshot.items():
            if rel_path in shadow_snapshot:
                continue
            entry = {"path": rel_path, "status": "deleted", "size": 0}
            current_base = current_base_snapshot.get(rel_path)
            if current_base != original:
                entry["conflict"] = True
                conflicts.append({"path": rel_path, "status": "deleted"})
            changes.append(entry)

        changes.sort(key=lambda item: (item["status"], item["path"]))
        return {
            "shadow_thread_id": shadow_thread_id,
            "base_thread_id": base_thread_id,
            "changes": changes,
            "conflicts": conflicts,
        }

    def _select_shadow_changes(self, diff: dict, paths: list[str] | None) -> tuple[list[dict], list[dict]]:
        if paths is None:
            return list(diff.get("changes", [])), list(diff.get("conflicts", []))
        selected = {str(path).strip().lstrip("./") for path in paths if str(path).strip()}
        changes = [change for change in diff.get("changes", []) if change.get("path") in selected]
        conflicts = [conflict for conflict in diff.get("conflicts", []) if conflict.get("path") in selected]
        return changes, conflicts

    def _update_shadow_base_snapshot(self, shadow_thread_id: str, applied_paths: list[str]):
        record = self._shadow_workspaces.get(shadow_thread_id)
        if record is None:
            return
        base_workspace = self.get_workspace_dir(record["base_thread_id"])
        current_base_snapshot = self._snapshot_workspace_dir(base_workspace)
        base_snapshot = dict(record.get("base_snapshot", {}))
        for rel_path in applied_paths:
            current = current_base_snapshot.get(rel_path)
            if current is None:
                base_snapshot.pop(rel_path, None)
            else:
                base_snapshot[rel_path] = current
        record["base_snapshot"] = base_snapshot

    def get_shadow_diff(
        self,
        shadow_thread_id: str,
        *,
        context_lines: int = 3,
        max_diff_chars: int = 20_000,
    ) -> dict:
        record = self._shadow_workspaces.get(shadow_thread_id)
        if record is None:
            return {"error": "Shadow workspace not found"}

        diff = self.list_shadow_changes(shadow_thread_id)
        if diff.get("error"):
            return diff

        base_workspace = self.get_workspace_dir(record["base_thread_id"])
        shadow_workspace = self.get_workspace_dir(shadow_thread_id)
        diff_entries: list[dict] = []

        for change in diff.get("changes", []):
            rel_path = change["path"]
            base_path = self._safe_workspace_path(base_workspace, rel_path)
            shadow_path = self._safe_workspace_path(shadow_workspace, rel_path)
            base_exists = bool(base_path and os.path.exists(base_path))
            shadow_exists = bool(shadow_path and os.path.exists(shadow_path))
            before_text, before_binary, before_truncated = self._read_text_diff_source(base_path or "")
            after_text, after_binary, after_truncated = self._read_text_diff_source(shadow_path or "")
            binary = before_binary or after_binary
            truncated = before_truncated or after_truncated
            diff_text = ""
            hunks: list[dict] = []

            if not binary:
                fromfile = f"a/{rel_path}" if base_exists else "/dev/null"
                tofile = f"b/{rel_path}" if shadow_exists else "/dev/null"
                unified = "\n".join(
                    difflib.unified_diff(
                        before_text.splitlines(),
                        after_text.splitlines(),
                        fromfile=fromfile,
                        tofile=tofile,
                        n=context_lines,
                        lineterm="",
                    )
                )
                if len(unified) > max_diff_chars:
                    diff_text = unified[:max_diff_chars] + "\n... (diff truncated)"
                    truncated = True
                else:
                    diff_text = unified
                hunks = self._build_text_hunks(before_text, after_text, context_lines=context_lines)

            diff_entries.append(
                {
                    **change,
                    "diff": diff_text,
                    "binary": binary,
                    "truncated": truncated,
                    "hunks": hunks,
                }
            )

        return {
            "shadow_thread_id": shadow_thread_id,
            "base_thread_id": record["base_thread_id"],
            "conflicts": diff.get("conflicts", []),
            "diffs": diff_entries,
        }

    def accept_shadow_workspace(
        self,
        shadow_thread_id: str,
        paths: list[str] | None = None,
        hunks: list[dict] | None = None,
    ) -> dict:
        record = self._shadow_workspaces.get(shadow_thread_id)
        if record is None:
            return {"error": "Shadow workspace not found"}
        diff = self.list_shadow_changes(shadow_thread_id)
        if diff.get("error"):
            return diff
        selected_hunks = self._normalize_hunk_selections(hunks)
        selected_changes, selected_conflicts = self._select_shadow_changes(
            diff,
            paths if paths is not None else ([] if selected_hunks else None),
        )
        if not selected_changes and not selected_hunks:
            return {
                "error": "No matching shadow changes or hunks selected",
                "shadow_thread_id": shadow_thread_id,
                "base_thread_id": record["base_thread_id"],
            }
        if selected_conflicts:
            return {
                "error": "Shadow workspace has conflicts",
                "shadow_thread_id": shadow_thread_id,
                "base_thread_id": record["base_thread_id"],
                "conflicts": selected_conflicts,
            }

        base_workspace = self.get_workspace_dir(record["base_thread_id"])
        shadow_workspace = self.get_workspace_dir(shadow_thread_id)
        applied: list[dict] = []
        selected_paths = {change["path"] for change in selected_changes}
        change_lookup = {change["path"]: change for change in diff.get("changes", [])}
        diff_lookup: dict[str, dict] = {}

        if selected_hunks:
            shadow_diff = self.get_shadow_diff(shadow_thread_id)
            if shadow_diff.get("error"):
                return shadow_diff
            diff_lookup = {entry["path"]: entry for entry in shadow_diff.get("diffs", [])}

        for change in selected_changes:
            rel_path = change["path"]
            base_path = os.path.join(base_workspace, rel_path)
            shadow_path = os.path.join(shadow_workspace, rel_path)
            if change["status"] == "deleted":
                if os.path.exists(base_path):
                    os.remove(base_path)
                applied.append({"path": rel_path, "status": "deleted"})
                continue
            os.makedirs(os.path.dirname(base_path), exist_ok=True)
            shutil.copy2(shadow_path, base_path)
            applied.append({"path": rel_path, "status": change["status"]})

        for rel_path, selected_ids in selected_hunks.items():
            if rel_path in selected_paths:
                continue
            change = change_lookup.get(rel_path)
            if change is None:
                return {
                    "error": "Selected hunk path not found in shadow changes",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            if change.get("conflict"):
                return {
                    "error": "Selected hunk path has conflicts",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            entry = diff_lookup.get(rel_path)
            if not entry:
                return {
                    "error": "Selected hunk diff not found",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            if entry.get("binary"):
                return {
                    "error": "Binary files do not support hunk acceptance",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            available_hunks = entry.get("hunks", [])
            valid_ids = {hunk.get("id", "") for hunk in available_hunks if hunk.get("id")}
            chosen_ids = {hunk_id for hunk_id in selected_ids if hunk_id in valid_ids}
            if not chosen_ids:
                return {
                    "error": "No matching hunk ids selected",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            base_path = self._safe_workspace_path(base_workspace, rel_path)
            shadow_path = self._safe_workspace_path(shadow_workspace, rel_path)
            shadow_exists = bool(shadow_path and os.path.exists(shadow_path))
            before_text, before_binary = self._read_full_text_file(base_path or "")
            after_text, after_binary = self._read_full_text_file(shadow_path or "")
            if before_binary or after_binary:
                return {
                    "error": "Selected hunk path is not a text file",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            merged_content = self._build_selected_hunk_content(before_text, after_text, chosen_ids)
            if not shadow_exists and chosen_ids == valid_ids:
                if base_path and os.path.exists(base_path):
                    os.remove(base_path)
                applied.append({"path": rel_path, "status": "deleted", "mode": "hunks", "hunks": sorted(chosen_ids)})
                continue
            if not base_path:
                return {
                    "error": "Invalid base path for selected hunk",
                    "shadow_thread_id": shadow_thread_id,
                    "base_thread_id": record["base_thread_id"],
                    "path": rel_path,
                }
            os.makedirs(os.path.dirname(base_path), exist_ok=True)
            with open(base_path, "w", encoding="utf-8") as f:
                f.write(merged_content)
            applied.append({"path": rel_path, "status": change["status"], "mode": "hunks", "hunks": sorted(chosen_ids)})

        self._update_shadow_base_snapshot(shadow_thread_id, [item["path"] for item in applied])
        remaining = self.list_shadow_changes(shadow_thread_id)
        return {
            "status": "accepted",
            "shadow_thread_id": shadow_thread_id,
            "base_thread_id": record["base_thread_id"],
            "applied": applied,
            "accepted_all": not remaining.get("changes"),
            "remaining_changes": remaining.get("changes", []),
            "remaining_conflicts": remaining.get("conflicts", []),
        }

    def discard_shadow_workspace(self, shadow_thread_id: str) -> bool:
        self._shadow_workspaces.pop(shadow_thread_id, None)
        shadow_root = os.path.join("./data", "workspaces", shadow_thread_id)
        self._thread_workspaces.pop(shadow_thread_id, None)
        if os.path.isdir(shadow_root):
            shutil.rmtree(shadow_root)
            return True
        return False

    async def execute_python(
        self, code: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else None
        return await self._run_code(code, "python3", timeout, work_dir, thread_id)

    async def execute_javascript(
        self, code: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else None
        return await self._run_code(code, "node", timeout, work_dir, thread_id)

    async def execute_bash(
        self, command: str, timeout: Optional[int] = None, thread_id: Optional[str] = None
    ) -> dict:
        timeout = timeout or self.timeout
        work_dir = self.get_workspace_dir(thread_id) if thread_id else "./data/workspaces/_default/workspace"
        os.makedirs(work_dir, exist_ok=True)
        env_bridge_file = self.get_env_bridge_file(thread_id, work_dir)

        # Restore persisted cwd if available (Claude Code: cwd persists across calls)
        effective_cwd = work_dir
        if thread_id and thread_id in self._thread_cwd:
            saved = self._thread_cwd[thread_id]
            if os.path.isdir(saved):
                effective_cwd = saved

        # Append pwd + env capture so we can persist cwd and env vars
        _ENV_SENTINEL = "__HERMES_ENV__"
        prelude = 'if [ -n "$CLAUDE_ENV_FILE" ] && [ -f "$CLAUDE_ENV_FILE" ]; then set -a; . "$CLAUDE_ENV_FILE" >/dev/null 2>&1; set +a; fi'
        if self.persist_bash_env:
            wrapped = (
                f'{prelude}\n'
                f'{command}\n'
                f'__hermes_rc=$?; '
                f'echo "\n{self._CWD_SENTINEL}$(pwd)"; '
                f'echo "{_ENV_SENTINEL}"; env -0 2>/dev/null || env; '
                f'echo "{_ENV_SENTINEL}"; '
                f'exit $__hermes_rc'
            )
        else:
            wrapped = (
                f'{prelude}\n'
                f'{command}\n'
                f'__hermes_rc=$?; '
                f'echo "\n{self._CWD_SENTINEL}$(pwd)"; '
                f'exit $__hermes_rc'
            )

        try:
            # Restore persisted env vars
            run_env = self._build_isolated_env(os.environ, work_dir, thread_id)
            run_env["CLAUDE_ENV_FILE"] = env_bridge_file
            if self.persist_bash_env and thread_id and thread_id in self._thread_env:
                run_env.update(self._thread_env[thread_id])

            command_args = self._wrap_with_os_sandbox(
                ["bash", "-c", wrapped],
                work_dir=work_dir,
                thread_id=thread_id,
            )
            proc = await asyncio.create_subprocess_exec(
                *command_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                env=run_env,
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

            raw_out = stdout.decode("utf-8", errors="replace")

            # Extract and persist env from sentinel
            _ENV_SENTINEL = "__HERMES_ENV__"
            if self.persist_bash_env and _ENV_SENTINEL in raw_out:
                parts = raw_out.split(_ENV_SENTINEL)
                raw_out = parts[0]  # everything before first env sentinel
                if len(parts) >= 3 and thread_id:
                    env_block = parts[1]
                    user_env: dict[str, str] = {}
                    # Try null-delimited first, fall back to newline
                    entries = env_block.split("\x00") if "\x00" in env_block else env_block.strip().split("\n")
                    for entry in entries:
                        if "=" in entry:
                            k, _, v = entry.partition("=")
                            k = k.strip()
                            if k and not k.startswith("_") and k not in (
                                "PWD", "OLDPWD", "SHLVL", "TERM", "HOME", "USER",
                                "SHELL", "PATH", "LANG", "LOGNAME", "TMPDIR",
                                "__hermes_rc", "PAGER",
                            ):
                                if k not in os.environ or os.environ[k] != v:
                                    user_env[k] = v
                    if user_env:
                        existing = self._thread_env.get(thread_id, {})
                        existing.update(user_env)
                        self._thread_env[thread_id] = existing

            # Extract and persist the final cwd from sentinel
            sentinel = self._CWD_SENTINEL
            if sentinel in raw_out:
                idx = raw_out.rfind(sentinel)
                cwd_line = raw_out[idx + len(sentinel):].strip().split("\n")[0].strip()
                raw_out = raw_out[:idx].rstrip("\n")
                if thread_id and cwd_line and os.path.isdir(cwd_line):
                    self._thread_cwd[thread_id] = cwd_line

            return {
                "success": proc.returncode == 0,
                "output": raw_out[:20000],
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

    def _record_file_history(self, thread_id: str, path: str, old_content: str | None, new_content: str):
        """Record a file change to the thread's history log."""
        from datetime import datetime
        history_dir = os.path.join(self.get_thread_workspace(thread_id), "history")
        os.makedirs(history_dir, exist_ok=True)
        log_path = os.path.join(history_dir, "file_history.json")
        entries = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    entries = json.load(f)
            except Exception as e:
                logger.debug("Suppressed error in manager: %s", e)
                entries = []
        # Generate unified diff
        old_lines = (old_content or "").splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
        diff_str = "\n".join(diff[:200])  # cap at 200 lines
        entry = {
            "timestamp": datetime.now().isoformat(),
            "path": path,
            "action": "modify" if old_content is not None else "create",
            "old_size": len(old_content) if old_content else 0,
            "new_size": len(new_content),
            "diff": diff_str,
        }
        entries.append(entry)
        # Keep last 500 entries
        entries = entries[-500:]
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("Suppressed error in manager: %s", e)

    def get_file_history(self, thread_id: str, path: str | None = None, limit: int = 50) -> list[dict]:
        """Get file change history for a thread, optionally filtered by path."""
        history_dir = os.path.join(self.get_thread_workspace(thread_id), "history")
        log_path = os.path.join(history_dir, "file_history.json")
        if not os.path.exists(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception as e:
            logger.debug("Suppressed error in manager: %s", e)
            return []
        if path:
            entries = [e for e in entries if e.get("path") == path]
        return entries[-limit:]

    async def write_file(self, path: str, content: str, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = self._safe_workspace_path(work_dir, path)
        if not full_path:
            return {"success": False, "error": "Path traversal not allowed"}

        # Capture old content for history
        old_content = None
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    old_content = f.read(50000)
            except Exception as e:
                logger.debug("Suppressed error in manager: %s", e)

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            # Record history
            try:
                self._record_file_history(thread_id, path, old_content, content)
            except Exception as e:
                logger.debug("Suppressed error in manager: %s", e)
            return {"success": True, "path": full_path, "size": len(content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_file(self, path: str, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = self._safe_workspace_path(work_dir, path)
        if not full_path:
            return {"success": False, "error": "Path traversal not allowed"}

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read(50000)
            return {"success": True, "content": content, "path": full_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_files(self, path: str = ".", thread_id: str = "") -> dict:
        work_dir = self.get_workspace_dir(thread_id) if thread_id else "./data/workspaces/_default/workspace"
        target = self._safe_workspace_path(work_dir, path)
        if not target:
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

    async def write_file_bytes(self, path: str, data: bytes, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = self._safe_workspace_path(work_dir, path)
        if not full_path:
            return {"success": False, "error": "Path traversal not allowed"}
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "wb") as f:
                f.write(data)
            return {"success": True, "path": full_path, "size": len(data)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_file_bytes(self, path: str, thread_id: str) -> dict:
        work_dir = self.get_workspace_dir(thread_id)
        full_path = self._safe_workspace_path(work_dir, path)
        if not full_path:
            return {"success": False, "error": "Path traversal not allowed"}
        try:
            with open(full_path, "rb") as f:
                data = f.read(50 * 1024 * 1024)  # max 50MB
            return {"success": True, "data": data, "path": full_path, "size": len(data)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def save_output(self, filename: str, content: str, thread_id: str) -> dict:
        outputs_dir = self.get_outputs_dir(thread_id)
        full_path = self._safe_root_path(outputs_dir, filename)
        if not full_path:
            return {"success": False, "error": "Path traversal not allowed"}
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": full_path, "filename": filename}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _run_code(
        self, code: str, runner: str, timeout: Optional[int], work_dir: Optional[str] = None, thread_id: Optional[str] = None
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
            run_env = self._build_isolated_env(os.environ, work_dir, thread_id) if work_dir else dict(os.environ)
            command_args = self._wrap_with_os_sandbox([runner, filepath], work_dir=work_dir, thread_id=thread_id)
            proc = await asyncio.create_subprocess_exec(
                *command_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=run_env,
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


    # ── Streaming diff support ──

    def track_file_before(self, work_dir: str, rel_path: str) -> dict | None:
        """Snapshot a file's content before modification.
        Call this BEFORE a tool writes to a file. Returns a token to pass to compute_file_diff."""
        full_path = self._safe_workspace_path(work_dir, rel_path)
        if not full_path:
            return None
        before_text, binary, _ = self._read_text_diff_source(full_path)
        return {
            "rel_path": rel_path,
            "full_path": full_path,
            "work_dir": work_dir,
            "before_text": before_text,
            "binary": binary,
        }

    def compute_file_diff(self, token: dict, context_lines: int = 3) -> dict | None:
        """Compute a diff for a single file after modification.
        Call this AFTER a tool writes to a file, passing the token from track_file_before.
        Returns a file_diff event payload or None if no changes."""
        if not token:
            return None
        rel_path = token["rel_path"]
        full_path = token["full_path"]
        before_text = token["before_text"]
        was_binary = token["binary"]

        after_text, after_binary, _ = self._read_text_diff_source(full_path)
        if was_binary or after_binary:
            if before_text == after_text:
                return None
            return {
                "path": rel_path,
                "status": "modified",
                "binary": True,
                "diff": "",
                "hunks": [],
            }

        if before_text == after_text:
            return None

        status = "added" if not before_text else "modified"
        hunks = self._build_text_hunks(before_text, after_text, context_lines=context_lines)
        unified = "\n".join(
            difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                n=context_lines,
                lineterm="",
            )
        )
        if len(unified) > 20_000:
            unified = unified[:20_000] + "\n... (diff truncated)"

        return {
            "path": rel_path,
            "status": status,
            "binary": False,
            "diff": unified,
            "hunks": hunks,
            "additions": sum(1 for h in hunks for l in h.get("lines", []) if l.get("type") == "add"),
            "deletions": sum(1 for h in hunks for l in h.get("lines", []) if l.get("type") == "del"),
        }


sandbox_executor = SandboxExecutor()
