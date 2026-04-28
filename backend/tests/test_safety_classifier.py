"""Tests for ML safety classifier (yoloClassifier)."""
import unittest
from app.agents.safety_classifier import (
    classify_tool_call, should_auto_approve, get_risk_summary,
    _extract_features, _sigmoid, ClassificationResult,
)


class TestSigmoid(unittest.TestCase):
    def test_zero(self):
        self.assertAlmostEqual(_sigmoid(0), 0.5, places=4)

    def test_positive(self):
        self.assertGreater(_sigmoid(5), 0.99)

    def test_negative(self):
        self.assertLess(_sigmoid(-5), 0.01)

    def test_clamp(self):
        self.assertAlmostEqual(_sigmoid(100), 1.0, places=4)
        self.assertAlmostEqual(_sigmoid(-100), 0.0, places=4)


class TestFeatureExtraction(unittest.TestCase):
    def test_read_only_tool(self):
        features, reasons = _extract_features("web_search", {"query": "hello"})
        self.assertEqual(features["is_read_only"], 1.0)
        self.assertEqual(features["is_destructive"], 0.0)

    def test_destructive_tool(self):
        features, reasons = _extract_features("execute_bash", {"command": "rm -rf /tmp/test"})
        self.assertEqual(features["is_exec_op"], 1.0)

    def test_path_traversal(self):
        features, reasons = _extract_features("write_file", {"path": "../../etc/passwd", "content": "x"})
        self.assertEqual(features["has_path_traversal"], 1.0)

    def test_sensitive_path(self):
        features, reasons = _extract_features("read_file", {"path": "~/.ssh/id_rsa"})
        self.assertEqual(features["has_sensitive_path"], 1.0)

    def test_shell_metachar(self):
        features, reasons = _extract_features("execute_bash", {"command": "echo $(whoami)"})
        self.assertEqual(features["has_shell_metachar"], 1.0)

    def test_url_detection(self):
        features, reasons = _extract_features("http_request", {"url": "https://evil.com"})
        self.assertEqual(features["has_url"], 1.0)

    def test_long_content(self):
        features, _ = _extract_features("write_file", {"content": "x" * 3000})
        self.assertEqual(features["has_long_content"], 1.0)


class TestClassification(unittest.TestCase):
    def test_safe_tool(self):
        result = classify_tool_call("web_search", {"query": "weather"})
        self.assertIsInstance(result, ClassificationResult)
        self.assertEqual(result.risk_level, "safe")
        self.assertTrue(result.auto_approve)
        self.assertFalse(result.requires_confirm)

    def test_read_file_safe(self):
        result = classify_tool_call("read_file", {"path": "README.md"})
        self.assertEqual(result.risk_level, "safe")
        self.assertTrue(result.auto_approve)

    def test_bash_rm_high_risk(self):
        result = classify_tool_call("execute_bash", {"command": "rm -rf /"})
        self.assertIn(result.risk_level, ("high_risk", "critical"))
        self.assertFalse(result.auto_approve)

    def test_bash_ls_lower_risk(self):
        result = classify_tool_call("execute_bash", {"command": "ls -la"})
        # bash has a prior but ls is not destructive
        self.assertLess(result.risk_score, 0.9)

    def test_write_file_moderate(self):
        result = classify_tool_call("write_file", {"path": "test.py", "content": "print('hi')"})
        self.assertIn(result.risk_level, ("low_risk", "medium_risk"))

    def test_write_sensitive_path_high(self):
        result = classify_tool_call("write_file", {"path": "/etc/passwd", "content": "x"})
        self.assertGreater(result.risk_score, 0.5)

    def test_local_bash_high(self):
        result = classify_tool_call("local_execute_bash", {"command": "sudo rm -rf /"})
        self.assertIn(result.risk_level, ("high_risk", "critical"))

    def test_calculate_safe(self):
        result = classify_tool_call("calculate", {"expression": "2+2"})
        self.assertEqual(result.risk_level, "safe")

    def test_context_awareness(self):
        ctx1 = {"turn_count": 0}
        ctx2 = {"turn_count": 25, "prev_tool": "execute_bash"}
        r1 = classify_tool_call("execute_bash", {"command": "echo hi"}, ctx1)
        r2 = classify_tool_call("execute_bash", {"command": "echo hi"}, ctx2)
        # High turn count slightly increases risk
        self.assertGreaterEqual(r2.risk_score, r1.risk_score - 0.1)


class TestShouldAutoApprove(unittest.TestCase):
    def test_safe(self):
        self.assertTrue(should_auto_approve("web_search", {"query": "test"}))

    def test_dangerous(self):
        self.assertFalse(should_auto_approve("execute_bash", {"command": "rm -rf /"}))


class TestGetRiskSummary(unittest.TestCase):
    def test_structure(self):
        summary = get_risk_summary("write_file", {"path": "test.txt", "content": "hello"})
        self.assertIn("tool_name", summary)
        self.assertIn("risk_level", summary)
        self.assertIn("risk_score", summary)
        self.assertIn("auto_approve", summary)
        self.assertIn("requires_confirm", summary)
        self.assertIn("reasons", summary)
        self.assertIn("features", summary)
        self.assertIsInstance(summary["reasons"], list)
        self.assertIsInstance(summary["features"], dict)

    def test_features_only_nonzero(self):
        summary = get_risk_summary("web_search", {"query": "test"})
        for v in summary["features"].values():
            self.assertNotEqual(v, 0.0)


if __name__ == "__main__":
    unittest.main()
