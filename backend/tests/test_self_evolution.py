"""Tests for self-evolution engine (Hermes-inspired)."""
import os
import json
import tempfile
import unittest
from unittest.mock import patch

from app.agents.self_evolution import (
    TraceEntry, TraceCollector, EvalDatasetBuilder,
    MutationEngine, FitnessEvaluator, EvolutionController,
)


class SelfEvolutionTestBase(unittest.TestCase):
    def setUp(self):
        import app.agents.self_evolution as mod
        self._mod = mod
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._tempdir.name)
        # Save and override module-level paths
        self._old_dirs = (mod.TRACES_DIR, mod.EVAL_DIR, mod.CANDIDATES_DIR, mod.HISTORY_PATH)
        mod.TRACES_DIR = os.path.join(self._tempdir.name, "traces")
        mod.EVAL_DIR = os.path.join(self._tempdir.name, "eval")
        mod.CANDIDATES_DIR = os.path.join(self._tempdir.name, "candidates")
        mod.HISTORY_PATH = os.path.join(self._tempdir.name, "history.json")
        for d in [mod.TRACES_DIR, mod.EVAL_DIR, mod.CANDIDATES_DIR]:
            os.makedirs(d, exist_ok=True)

    def tearDown(self):
        self._mod.TRACES_DIR, self._mod.EVAL_DIR, self._mod.CANDIDATES_DIR, self._mod.HISTORY_PATH = self._old_dirs
        os.chdir(self._old_cwd)
        self._tempdir.cleanup()


class TestTraceCollector(SelfEvolutionTestBase):
    def test_record_and_get(self):
        tc = TraceCollector()
        trace = TraceEntry(
            timestamp="2025-01-01T00:00:00",
            thread_id="t1",
            skill_name="test_skill",
            user_input="hello",
            agent_output="world",
            success=True,
            score=0.9,
        )
        tc.record(trace)
        traces = tc.get_traces("test_skill")
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["user_input"], "hello")

    def test_get_failure_traces(self):
        tc = TraceCollector()
        for i in range(5):
            tc.record(TraceEntry(
                timestamp=f"2025-01-0{i+1}T00:00:00",
                thread_id="t1", skill_name="sk",
                user_input=f"q{i}", agent_output=f"a{i}",
                success=i > 2, score=0.2 if i <= 2 else 0.8,
            ))
        failures = tc.get_failure_traces("sk")
        self.assertEqual(len(failures), 3)

    def test_get_success_traces(self):
        tc = TraceCollector()
        for i in range(5):
            tc.record(TraceEntry(
                timestamp=f"2025-01-0{i+1}T00:00:00",
                thread_id="t1", skill_name="sk2",
                user_input=f"q{i}", agent_output=f"a{i}",
                success=i >= 3, score=0.8 if i >= 3 else 0.2,
            ))
        successes = tc.get_success_traces("sk2")
        self.assertEqual(len(successes), 2)

    def test_skill_stats(self):
        tc = TraceCollector()
        for i in range(10):
            tc.record(TraceEntry(
                timestamp=f"2025-01-{i+1:02d}T00:00:00",
                thread_id="t1", skill_name="stats_skill",
                user_input=f"q{i}", agent_output=f"a{i}",
                success=i >= 5, score=0.3 if i < 5 else 0.9,
            ))
        stats = tc.get_skill_stats()
        self.assertIn("stats_skill", stats)
        self.assertEqual(stats["stats_skill"]["total_traces"], 10)
        self.assertEqual(stats["stats_skill"]["successes"], 5)
        self.assertEqual(stats["stats_skill"]["failures"], 5)

    def test_trim(self):
        tc = TraceCollector()
        tc.MAX_TRACES_PER_SKILL = 10
        for i in range(20):
            tc.record(TraceEntry(
                timestamp=f"2025-01-01T{i:02d}:00:00",
                thread_id="t1", skill_name="trim_test",
                user_input=f"q{i}", agent_output=f"a{i}",
            ))
        traces = tc.get_traces("trim_test", limit=100)
        self.assertLessEqual(len(traces), 10)


class TestEvalDatasetBuilder(SelfEvolutionTestBase):
    def test_build_from_traces(self):
        builder = EvalDatasetBuilder()
        traces = [
            {"user_input": "q1", "agent_output": "a1", "score": 0.9},
            {"user_input": "q2", "agent_output": "a2", "score": 0.3, "user_feedback": "bad"},
        ]
        cases = builder.build_from_traces("sk", traces)
        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0]["source"], "session")

    def test_build_synthetic(self):
        builder = EvalDatasetBuilder()
        cases = builder.build_synthetic("A skill that reviews code")
        self.assertGreater(len(cases), 0)
        self.assertEqual(cases[0]["source"], "synthetic")

    def test_save_and_load(self):
        builder = EvalDatasetBuilder()
        cases = [{"input_text": "q", "expected_behavior": "r", "source": "test"}]
        builder.save_dataset("test_sk", cases)
        loaded = builder.load_dataset("test_sk")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["input_text"], "q")


class TestMutationEngine(SelfEvolutionTestBase):
    def test_generate_mutations(self):
        engine = MutationEngine()
        mutations = engine.generate_mutations("A simple skill that does things.\nStep 1\nStep 2\nStep 3\nStep 4\nStep 5\nStep 6")
        self.assertGreater(len(mutations), 0)
        for m in mutations:
            self.assertIn("id", m)
            self.assertIn("content", m)
            self.assertIn("mutation_type", m)
            self.assertNotEqual(m["content"], "")

    def test_mutations_with_failures(self):
        engine = MutationEngine()
        mutations = engine.generate_mutations(
            "Do stuff",
            failure_examples=["Failed on edge case X", "Timeout on large input"],
        )
        has_fix = any(m["mutation_type"] == "fix_failure" for m in mutations)
        self.assertTrue(has_fix)

    def test_add_structure(self):
        engine = MutationEngine()
        text = "\n".join([f"Line {i}" for i in range(10)])
        structured = engine._add_structure(text)
        self.assertIn("##", structured)


class TestFitnessEvaluator(SelfEvolutionTestBase):
    def test_evaluate_rule_based(self):
        evaluator = FitnessEvaluator()
        text = """## Code Review Skill
        
步骤:
1. 分析代码结构
2. 检查错误
3. 给出建议

规则:
- 必须检查安全问题
- 不要忽略性能
- 始终给出可操作的建议"""
        scores = evaluator.evaluate_rule_based(text, [])
        self.assertIn("total", scores)
        self.assertGreater(scores["total"], 0)
        self.assertLessEqual(scores["total"], 1.0)
        self.assertGreater(scores["structure_score"], 0.5)  # has ## and -
        self.assertGreater(scores["specificity_score"], 0.5)  # has 必须, 不要

    def test_length_score(self):
        evaluator = FitnessEvaluator()
        self.assertAlmostEqual(evaluator._length_score("x" * 500), 1.0)
        self.assertLess(evaluator._length_score("x" * 50), 1.0)
        self.assertLess(evaluator._length_score("x" * 10000), 1.0)

    def test_coverage_score(self):
        evaluator = FitnessEvaluator()
        text = "This covers accuracy and error handling"
        cases = [
            {"category": "accuracy"},
            {"category": "error"},
            {"category": "speed"},
        ]
        score = evaluator._coverage_score(text, cases)
        self.assertGreater(score, 0)


class TestEvolutionController(SelfEvolutionTestBase):
    def test_evolve_skill(self):
        ctrl = EvolutionController()
        skill_content = """## Debugging Skill
步骤:
1. 分析错误日志
2. 定位根因
3. 提出修复方案
规则:
- 必须先复现问题
- 不要猜测原因"""

        result = ctrl.evolve_skill("debug", skill_content, iterations=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["target_name"], "debug")
        self.assertGreater(result["total_candidates"], 1)
        self.assertIsNotNone(result["best_candidate"])

    def test_auto_triage_empty(self):
        ctrl = EvolutionController()
        triage = ctrl.auto_triage()
        self.assertIsInstance(triage, list)

    def test_get_stats(self):
        ctrl = EvolutionController()
        stats = ctrl.get_stats()
        self.assertIn("total_runs", stats)
        self.assertIn("skill_stats", stats)
        self.assertIn("pending_triage", stats)

    def test_get_history_empty(self):
        ctrl = EvolutionController()
        self.assertEqual(ctrl.get_history(), [])


if __name__ == "__main__":
    unittest.main()
