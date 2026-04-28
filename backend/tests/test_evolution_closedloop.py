"""Closed-loop self-evolution integration tests.

Validates the full pipeline:
  trace recording → auto-triage → evolution → auto-deploy back to SkillRegistry
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestTraceWiringInSuperAgent(unittest.TestCase):
    """Verify that _shared_flow and _flash_flow record traces."""

    def test_shared_flow_records_trace(self):
        """_shared_flow should call trace_collector.record at the end."""
        import asyncio
        from app.agents.self_evolution import evolution_controller, TraceEntry

        original_record = evolution_controller.trace_collector.record
        recorded = []

        def spy_record(trace):
            recorded.append(trace)
            return original_record(trace)

        with patch.object(evolution_controller.trace_collector, "record", side_effect=spy_record):
            # We can't run _shared_flow fully without LLM, but we can verify
            # the import and record call exists by checking the source
            import inspect
            from app.agents.super_agent import SuperAgent
            source = inspect.getsource(SuperAgent._shared_flow)
            self.assertIn("evolution_controller.trace_collector.record", source)
            self.assertIn("TraceEntry", source)

    def test_flash_flow_records_trace(self):
        """_flash_flow should call trace_collector.record."""
        import inspect
        from app.agents.super_agent import SuperAgent
        source = inspect.getsource(SuperAgent._flash_flow)
        self.assertIn("evolution_controller.trace_collector.record", source)
        self.assertIn("TraceEntry", source)


class TestAutoDeployOnEvolve(unittest.TestCase):
    """Verify that evolve_skill auto-deploys improved results to SkillRegistry."""

    def test_evolve_deploys_to_registry(self):
        """When evolution improves a skill, it should update SkillRegistry."""
        from app.agents.self_evolution import EvolutionController, TraceCollector, TraceEntry
        from app.agents.evolution import SkillRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            # Setup isolated registries
            with patch("app.agents.self_evolution.TRACES_DIR", os.path.join(tmpdir, "traces")), \
                 patch("app.agents.self_evolution.EVAL_DIR", os.path.join(tmpdir, "eval")), \
                 patch("app.agents.self_evolution.CANDIDATES_DIR", os.path.join(tmpdir, "candidates")), \
                 patch("app.agents.self_evolution.HISTORY_PATH", os.path.join(tmpdir, "history.json")), \
                 patch("app.agents.evolution.CUSTOM_SKILLS_DIR", os.path.join(tmpdir, "skills")):
                os.makedirs(os.path.join(tmpdir, "traces"), exist_ok=True)
                os.makedirs(os.path.join(tmpdir, "eval"), exist_ok=True)
                os.makedirs(os.path.join(tmpdir, "candidates"), exist_ok=True)
                os.makedirs(os.path.join(tmpdir, "skills"), exist_ok=True)
                os.makedirs(os.path.join(tmpdir, "skills", "_versions"), exist_ok=True)

                sr = SkillRegistry()
                ok, msg = sr.create_skill(
                    name="test_evolve_skill",
                    display_name="Test Skill",
                    description="A test skill",
                    system_prompt="You are a basic assistant.",
                )
                self.assertTrue(ok, msg)

                # Create controller and seed traces
                ctrl = EvolutionController()
                for i in range(10):
                    ctrl.trace_collector.record(TraceEntry(
                        timestamp=datetime.now().isoformat(),
                        thread_id=f"t-{i}",
                        skill_name="test_evolve_skill",
                        user_input=f"q{i}",
                        agent_output=f"a{i}",
                        success=i > 5,
                        score=0.3 if i <= 5 else 0.8,
                    ))

                original_prompt = sr.get_skill("test_evolve_skill")["system_prompt"]

                # Run evolution with auto-deploy
                with patch.object(ctrl, "_auto_deploy", wraps=ctrl._auto_deploy) as mock_deploy:
                    with patch("app.agents.self_evolution.evolution_controller", ctrl):
                        result = ctrl.evolve_skill("test_evolve_skill", original_prompt, iterations=3)

                self.assertEqual(result["status"], "completed")

                # If improvement > 0.02, _auto_deploy should have been called
                if result["improvement"] > 0.02:
                    mock_deploy.assert_called_once()

    def test_no_deploy_below_threshold(self):
        """Evolution with tiny improvement should NOT deploy."""
        from app.agents.self_evolution import EvolutionController
        ctrl = EvolutionController()

        # Mock _auto_deploy to verify it's called correctly
        with patch.object(ctrl, "_auto_deploy") as mock:
            # Simulate improvement below threshold
            ctrl._auto_deploy("fake_skill", "content", "run123", 0.01)

        # Direct call with low improvement should skip
        ctrl_real = EvolutionController()
        with patch("app.agents.evolution.skill_registry") as mock_sr:
            mock_sr.get_skill.return_value = {"name": "x", "system_prompt": "old"}
            ctrl_real._auto_deploy("x", "new content", "run1", 0.01)
            mock_sr.edit_skill.assert_not_called()


class TestCronAutoEvolution(unittest.TestCase):
    """Verify the cron-based auto-evolution pipeline."""

    def test_auto_evolve_job_seeded(self):
        """A _auto_evolve cron job should exist by default."""
        from app.agents.self_evolution import cron_manager
        jobs = cron_manager.list_jobs()
        names = [j["name"] for j in jobs]
        self.assertIn("_auto_evolve", names)

        job = cron_manager.get_job("_auto_evolve")
        self.assertEqual(job["action_type"], "evolution")
        self.assertEqual(job["action"], "auto_triage")
        self.assertTrue(job["enabled"])

    def test_evolution_action_type_no_triage_candidates(self):
        """When no skills need evolution, auto_triage should succeed with empty result."""
        import asyncio
        from app.agents.self_evolution import CronManager, evolution_controller

        with patch.object(evolution_controller, "auto_triage", return_value=[]):
            result = CronManager._run_auto_evolution("auto_triage")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evolved"], [])

    def test_evolution_action_type_with_candidates(self):
        """When skills need evolution, auto_triage should evolve them."""
        from app.agents.self_evolution import CronManager, EvolutionController

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.agents.self_evolution.TRACES_DIR", os.path.join(tmpdir, "traces")), \
                 patch("app.agents.self_evolution.EVAL_DIR", os.path.join(tmpdir, "eval")), \
                 patch("app.agents.self_evolution.CANDIDATES_DIR", os.path.join(tmpdir, "candidates")), \
                 patch("app.agents.self_evolution.HISTORY_PATH", os.path.join(tmpdir, "history.json")), \
                 patch("app.agents.evolution.CUSTOM_SKILLS_DIR", os.path.join(tmpdir, "skills")):
                for d in ["traces", "eval", "candidates", "skills", "skills/_versions"]:
                    os.makedirs(os.path.join(tmpdir, d), exist_ok=True)

                from app.agents.evolution import SkillRegistry
                sr = SkillRegistry()
                sr.create_skill("weak_skill", "Weak", "desc", "You are weak.")

                ctrl = EvolutionController()
                # Fake triage result
                with patch.object(ctrl, "auto_triage", return_value=[
                    {"skill_name": "weak_skill", "success_rate": 0.3, "total_traces": 10, "priority": 7.0}
                ]):
                    with patch("app.agents.self_evolution.evolution_controller", ctrl), \
                         patch("app.agents.self_evolution.CronManager._run_auto_evolution.__wrapped__", None, create=True):
                        result = CronManager._run_auto_evolution("auto_triage")

                self.assertEqual(result["status"], "success")
                self.assertIn("evolved", result)

    def test_run_job_evolution_type(self):
        """CronManager.run_job should handle action_type='evolution'."""
        import asyncio
        from app.agents.self_evolution import CronManager, CronJob

        cm = CronManager()
        cm._jobs["test_evo"] = CronJob(
            name="test_evo", schedule="daily", action="auto_triage",
            action_type="evolution", created_at=datetime.now().isoformat(),
        )

        with patch.object(CronManager, "_run_auto_evolution", return_value={"status": "success", "output": "ok"}):
            result = asyncio.run(cm.run_job("test_evo"))

        self.assertEqual(result["status"], "success")


class TestFullClosedLoop(unittest.TestCase):
    """End-to-end: traces → triage → evolve → deploy."""

    def test_full_loop(self):
        """Simulate full closed loop: collect traces, triage, evolve, verify deployment."""
        from app.agents.self_evolution import (
            EvolutionController, TraceCollector, TraceEntry,
        )
        from app.agents.evolution import SkillRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.agents.self_evolution.TRACES_DIR", os.path.join(tmpdir, "traces")), \
                 patch("app.agents.self_evolution.EVAL_DIR", os.path.join(tmpdir, "eval")), \
                 patch("app.agents.self_evolution.CANDIDATES_DIR", os.path.join(tmpdir, "candidates")), \
                 patch("app.agents.self_evolution.HISTORY_PATH", os.path.join(tmpdir, "history.json")), \
                 patch("app.agents.evolution.CUSTOM_SKILLS_DIR", os.path.join(tmpdir, "skills")):
                for d in ["traces", "eval", "candidates", "skills", "skills/_versions"]:
                    os.makedirs(os.path.join(tmpdir, d), exist_ok=True)

                # Step 1: Create a skill
                sr = SkillRegistry()
                ok, _ = sr.create_skill(
                    name="search_helper",
                    display_name="Search Helper",
                    description="Helps with searches",
                    system_prompt="You are a search assistant. Help users find info.",
                )
                self.assertTrue(ok)
                original = sr.get_skill("search_helper")["system_prompt"]

                # Step 2: Record traces (simulate bad performance)
                ctrl = EvolutionController()
                for i in range(20):
                    ctrl.trace_collector.record(TraceEntry(
                        timestamp=datetime.now().isoformat(),
                        thread_id=f"t-{i}",
                        skill_name="search_helper",
                        user_input=f"search for topic {i}",
                        agent_output="no result" if i < 14 else f"found info about {i}",
                        tool_calls=[{"name": "web_search"}],
                        success=i >= 14,  # only 30% success
                        score=0.2 if i < 14 else 0.9,
                    ))

                # Step 3: Triage should find this skill
                triage = ctrl.auto_triage()
                self.assertTrue(len(triage) > 0, "Triage should find underperforming skill")
                self.assertEqual(triage[0]["skill_name"], "search_helper")

                # Step 4: Evolve with auto-deploy
                with patch("app.agents.self_evolution.evolution_controller", ctrl), \
                     patch("app.agents.evolution.skill_registry", sr):
                    result = ctrl.evolve_skill("search_helper", original, iterations=5)

                self.assertEqual(result["status"], "completed")
                self.assertGreater(result["improvement"], 0, "Evolution should improve the skill")

                # Step 5: Verify deployment happened
                updated = sr.get_skill("search_helper")
                if result["improvement"] > 0.02:
                    self.assertNotEqual(updated["system_prompt"], original,
                                       "Evolved skill should be deployed back")
                    self.assertGreater(len(updated["system_prompt"]), len(original),
                                       "Evolved prompt should be longer (expanded)")

                # Step 6: History should be recorded
                history = ctrl.get_history()
                self.assertTrue(len(history) > 0)
                self.assertEqual(history[-1]["target_name"], "search_helper")


if __name__ == "__main__":
    unittest.main()
