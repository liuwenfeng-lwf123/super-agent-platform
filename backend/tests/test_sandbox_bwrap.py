"""Unit tests for sandbox manager — bwrap + darwin sandbox wrapping."""
import os
import sys
import unittest
from unittest.mock import patch

from app.sandbox.manager import SandboxExecutor


class TestOsSandboxAvailable(unittest.TestCase):
    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=True)
    def test_darwin_available(self, _mock_exists):
        se = SandboxExecutor.__new__(SandboxExecutor)
        self.assertTrue(se.os_sandbox_available())

    @patch("sys.platform", "darwin")
    @patch("os.path.exists", return_value=False)
    def test_darwin_missing_binary(self, _mock_exists):
        se = SandboxExecutor.__new__(SandboxExecutor)
        self.assertFalse(se.os_sandbox_available())

    @patch("sys.platform", "linux")
    @patch("shutil.which", return_value="/usr/bin/bwrap")
    def test_linux_bwrap_available(self, _mock_which):
        se = SandboxExecutor.__new__(SandboxExecutor)
        self.assertTrue(se.os_sandbox_available())

    @patch("sys.platform", "linux")
    @patch("shutil.which", return_value=None)
    def test_linux_bwrap_missing(self, _mock_which):
        se = SandboxExecutor.__new__(SandboxExecutor)
        self.assertFalse(se.os_sandbox_available())

    @patch("sys.platform", "win32")
    def test_windows_not_supported(self):
        se = SandboxExecutor.__new__(SandboxExecutor)
        self.assertFalse(se.os_sandbox_available())


class TestWrapWithDarwinSandbox(unittest.TestCase):
    def setUp(self):
        self.se = SandboxExecutor(timeout=10)

    def test_darwin_sandbox_returns_sandbox_exec(self):
        with patch("os.makedirs"):
            result = self.se._wrap_with_darwin_sandbox(
                ["echo", "hi"],
                work_dir="/tmp/test_work",
                thread_id="t1",
            )
        self.assertEqual(result[0], "/usr/bin/sandbox-exec")
        self.assertIn("-p", result)
        self.assertIn("echo", result)
        self.assertIn("hi", result)

    def test_darwin_sandbox_profile_has_deny_default(self):
        with patch("os.makedirs"):
            result = self.se._wrap_with_darwin_sandbox(
                ["ls"],
                work_dir="/tmp/test_work",
                thread_id="t1",
            )
        profile = result[result.index("-p") + 1]
        self.assertIn("(deny default)", profile)
        self.assertIn("(allow file-read*)", profile)


class TestWrapWithBwrap(unittest.TestCase):
    def setUp(self):
        self.se = SandboxExecutor(timeout=10)

    def test_bwrap_returns_correct_binary(self):
        with patch("os.makedirs"):
            result = self.se._wrap_with_bwrap(
                ["echo", "hi"],
                work_dir="/tmp/test_work",
                thread_id="t1",
            )
        self.assertEqual(result[0], "bwrap")
        self.assertIn("--ro-bind", result)
        self.assertIn("--share-net", result)
        self.assertIn("--die-with-parent", result)
        self.assertIn("--unshare-pid", result)
        self.assertIn("echo", result)
        self.assertIn("hi", result)

    def test_bwrap_binds_work_dir(self):
        with patch("os.makedirs"):
            result = self.se._wrap_with_bwrap(
                ["ls"],
                work_dir="/tmp/test_work",
                thread_id="t1",
            )
        # Find all --bind pairs
        bind_targets = []
        for i, arg in enumerate(result):
            if arg == "--bind" and i + 2 < len(result):
                bind_targets.append(result[i + 1])
        real_work = os.path.realpath("/tmp/test_work")
        self.assertIn(real_work, bind_targets)


class TestWrapWithOsSandboxDispatch(unittest.TestCase):
    def setUp(self):
        self.se = SandboxExecutor(timeout=10)

    def test_returns_passthrough_when_unavailable(self):
        with patch.object(self.se, "os_sandbox_available", return_value=False):
            result = self.se._wrap_with_os_sandbox(
                ["echo", "hi"], work_dir="/tmp/w", thread_id="t1"
            )
        self.assertEqual(result, ["echo", "hi"])

    def test_returns_passthrough_when_no_work_dir(self):
        result = self.se._wrap_with_os_sandbox(
            ["echo", "hi"], work_dir=None, thread_id="t1"
        )
        self.assertEqual(result, ["echo", "hi"])

    @patch("sys.platform", "linux")
    def test_dispatches_to_bwrap_on_linux(self):
        with patch.object(self.se, "os_sandbox_available", return_value=True), \
             patch.object(self.se, "_wrap_with_bwrap", return_value=["bwrap", "echo"]) as mock_bwrap:
            result = self.se._wrap_with_os_sandbox(
                ["echo"], work_dir="/tmp/w", thread_id="t1"
            )
            mock_bwrap.assert_called_once()
            self.assertEqual(result[0], "bwrap")

    @patch("sys.platform", "darwin")
    def test_dispatches_to_darwin_on_macos(self):
        with patch.object(self.se, "os_sandbox_available", return_value=True), \
             patch.object(self.se, "_wrap_with_darwin_sandbox", return_value=["/usr/bin/sandbox-exec", "echo"]) as mock_darwin:
            result = self.se._wrap_with_os_sandbox(
                ["echo"], work_dir="/tmp/w", thread_id="t1"
            )
            mock_darwin.assert_called_once()


if __name__ == "__main__":
    unittest.main()
