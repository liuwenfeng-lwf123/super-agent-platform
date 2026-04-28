"""严格进化质量测试 — 验证进化系统产出真实可用的改进结果。

评估标准：
  1. 进化必须产生显著提升（>15%）
  2. 进化后的 prompt 必须结构完整（有标题、列表、无乱码）
  3. 进化后的 prompt 必须保留原始意图（语义保持）
  4. 进化后的 prompt 不能有重复段落
  5. 进化后的 prompt 可以直接作为系统提示使用
  6. 3 个不同领域的技能都必须通过验证
"""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── 严格质量评估器 ───
class StrictQualityChecker:
    """Multi-dimensional quality checker with strict pass/fail criteria."""

    @staticmethod
    def check_no_duplicate_sections(text: str) -> tuple[bool, str]:
        """No section header should appear more than once."""
        headers = re.findall(r'^##\s+(.+)$', text, re.MULTILINE)
        seen = {}
        for h in headers:
            h_norm = h.strip().lower()
            seen[h_norm] = seen.get(h_norm, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        if dupes:
            return False, f"Duplicate sections: {dupes}"
        return True, "No duplicates"

    @staticmethod
    def check_no_garbage(text: str) -> tuple[bool, str]:
        """No garbled text, truncated content, or encoding issues."""
        if not text.strip():
            return False, "Empty content"
        # Check for common garbage patterns
        if text.count("\\n") > 5:
            return False, "Contains literal \\n (escaped newlines)"
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
            return False, "Contains control characters"
        # Check for senseless repetition (same line 3+ times)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for line in set(lines):
            if lines.count(line) >= 3 and len(line) > 10:
                return False, f"Line repeated 3+ times: '{line[:50]}...'"
        return True, "Clean content"

    @staticmethod
    def check_structure_quality(text: str) -> tuple[bool, float, str]:
        """Must have proper structure: headers, lists, organized content."""
        score = 0.0
        details = []

        # Has at least one section header
        if re.search(r'^##\s+', text, re.MULTILINE):
            score += 0.25
            details.append("has headers")

        # Has bullet points or numbered lists
        if re.search(r'^[\-\*]\s+', text, re.MULTILINE) or re.search(r'^\d+\.\s+', text, re.MULTILINE):
            score += 0.25
            details.append("has lists")

        # Has actionable keywords
        action_words = ["必须", "不要", "始终", "优先", "如果", "当", "should", "must", "always", "never", "步骤", "规则"]
        action_count = sum(1 for w in action_words if w in text)
        if action_count >= 3:
            score += 0.25
            details.append(f"{action_count} action keywords")
        elif action_count >= 1:
            score += 0.1
            details.append(f"only {action_count} action keywords")

        # Reasonable length (200-3000 chars)
        length = len(text)
        if 200 <= length <= 3000:
            score += 0.25
            details.append(f"good length ({length})")
        elif length > 3000:
            score += 0.1
            details.append(f"too long ({length})")

        passes = score >= 0.5
        return passes, score, "; ".join(details)

    @staticmethod
    def check_intent_preserved(original: str, evolved: str) -> tuple[bool, str]:
        """Evolved text must contain the original's core content or its key phrases."""
        # The original text or its first sentence should be present
        first_sentence = original.split("。")[0] if "。" in original else original.split(".")[0]
        first_sentence = first_sentence.strip()

        if first_sentence and first_sentence in evolved:
            return True, "Original first sentence preserved"

        # Or at least 50% of original words should appear
        orig_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', original.lower()))
        evolved_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', evolved.lower()))
        if not orig_words:
            return True, "No meaningful original words"
        overlap = len(orig_words & evolved_words) / len(orig_words)
        if overlap >= 0.5:
            return True, f"Word overlap: {overlap:.0%}"
        return False, f"Low word overlap: {overlap:.0%}"

    @staticmethod
    def check_usable_as_system_prompt(text: str) -> tuple[bool, str]:
        """The evolved text must be directly usable as a system prompt."""
        issues = []
        if len(text) < 20:
            issues.append("too short to be useful (<20 chars)")
        if len(text) > 10000:
            issues.append("too long for system prompt (>10000 chars)")
        if text.count("```") % 2 != 0:
            issues.append("unclosed code blocks")
        # Should not start with markdown meta or JSON
        if text.strip().startswith("{") or text.strip().startswith("["):
            issues.append("starts with JSON, not a prompt")
        if issues:
            return False, "; ".join(issues)
        return True, "Usable as system prompt"


# ─── 测试场景定义 ───
SKILL_SCENARIOS = [
    {
        "name": "customer_support",
        "display_name": "客服助手",
        "description": "处理客户咨询、投诉和售后问题",
        "system_prompt": "你是一个客服助手。回答用户问题。",
        "conversations": [
            # (question, answer, success)
            ("订单什么时候到？", "根据系统查询，您的订单预计3天内送达。订单号为ORD-12345。", True),
            ("如何退货？", "您可以在订单详情页点击退货申请，7天内可退。需要提供退货原因和照片。", True),
            ("优惠券怎么用？", "在结算页面底部有优惠码输入框，输入后点击应用即可抵扣。", True),
            ("产品质量很好", "感谢您的认可！如果满意请给我们五星好评。", True),
            ("帮我查下物流", "好的，正在为您查询。您的包裹已经到达本地分拣中心，预计明天派送。", True),
            ("我要投诉！太差了！", "好的。", False),
            ("物流太慢了怎么办", "嗯。", False),
            ("商品破损了", "", False),
            ("退款多久到账", "不知道", False),
            ("为什么收费比别家贵？", "这个...", False),
            ("客服态度太差了", "哦", False),
            ("能不能加急发货", "不行", False),
        ],
    },
    {
        "name": "code_review",
        "display_name": "代码审查助手",
        "description": "审查代码质量，提供改进建议",
        "system_prompt": "你是一个代码审查助手。帮用户审查代码。",
        "conversations": [
            ("帮我审查这个函数", "函数结构清晰，但建议：1. 添加类型注解 2. 处理空输入边界 3. 加docstring", True),
            ("这段代码有安全问题吗", "发现SQL注入风险：第15行直接拼接用户输入。建议使用参数化查询。", True),
            ("性能怎么优化", "主要瓶颈在N+1查询，建议使用批量查询替代循环查询，预计提升10x。", True),
            ("这个代码风格对吗", "基本符合PEP8，建议改进：变量命名更具描述性，函数拆分为更小的单元。", True),
            ("帮我review下PR", "可以", False),
            ("这个有bug吗", "看看", False),
            ("代码能跑吗", "应该能", False),
            ("怎么重构这段", "不清楚", False),
            ("测试覆盖率够吗", "嗯", False),
        ],
    },
    {
        "name": "data_analyst",
        "display_name": "数据分析师",
        "description": "帮助用户分析数据、生成报告",
        "system_prompt": "你是数据分析助手。帮用户分析数据。",
        "conversations": [
            ("分析上月销售数据", "上月总销售额125万，环比增长15%。TOP3品类：电子产品(40%)、服饰(25%)、食品(15%)。建议关注电子品类增长。", True),
            ("做个用户画像", "基于数据分析：核心用户25-35岁女性，集中在一二线城市，月均消费800元，复购率65%。", True),
            ("这个图表说明什么", "柱状图显示Q1-Q4趋势上升，Q3有明显拐点，可能与暑期促销相关。建议深入分析Q3活动效果。", True),
            ("预测下季度趋势", "差不多吧", False),
            ("数据异常怎么办", "检查下", False),
            ("怎么做A/B测试", "百度一下", False),
            ("转化率为什么下降", "不知道", False),
            ("报告怎么写", "随便写", False),
        ],
    },
]


class TestEvolutionStrictQuality(unittest.TestCase):
    """严格测试进化系统的真实效果。"""

    def setUp(self):
        self.checker = StrictQualityChecker()
        self.tmpdir = tempfile.mkdtemp()
        self.patches = [
            patch("app.agents.self_evolution.TRACES_DIR", os.path.join(self.tmpdir, "traces")),
            patch("app.agents.self_evolution.EVAL_DIR", os.path.join(self.tmpdir, "eval")),
            patch("app.agents.self_evolution.CANDIDATES_DIR", os.path.join(self.tmpdir, "candidates")),
            patch("app.agents.self_evolution.HISTORY_PATH", os.path.join(self.tmpdir, "history.json")),
            patch("app.agents.evolution.CUSTOM_SKILLS_DIR", os.path.join(self.tmpdir, "skills")),
        ]
        for p in self.patches:
            p.start()
        for d in ["traces", "eval", "candidates", "skills", "skills/_versions"]:
            os.makedirs(os.path.join(self.tmpdir, d), exist_ok=True)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_scenario(self, scenario: dict) -> dict:
        """Run a full evolution scenario and return detailed results."""
        from app.agents.self_evolution import EvolutionController, TraceEntry, FitnessEvaluator
        from app.agents.evolution import SkillRegistry

        sr = SkillRegistry()
        ok, msg = sr.create_skill(
            name=scenario["name"],
            display_name=scenario["display_name"],
            description=scenario["description"],
            system_prompt=scenario["system_prompt"],
        )
        self.assertTrue(ok, f"Failed to create skill: {msg}")

        # Record traces
        ctrl = EvolutionController()
        for i, (q, a, success) in enumerate(scenario["conversations"] * 3):  # 3x replay
            ctrl.trace_collector.record(TraceEntry(
                timestamp=datetime.now().isoformat(),
                thread_id=f"t-{scenario['name']}-{i:03d}",
                skill_name=scenario["name"],
                user_input=q,
                agent_output=a,
                tool_calls=[],
                success=success,
                score=0.9 if success else 0.15,
            ))

        original = scenario["system_prompt"]
        fe = FitnessEvaluator()

        # Baseline scores
        from app.agents.self_evolution import EvalDatasetBuilder
        edb = EvalDatasetBuilder()
        traces = ctrl.trace_collector.get_traces(scenario["name"])
        eval_cases = edb.build_from_traces(scenario["name"], traces)
        baseline_scores = fe.evaluate_rule_based(original, eval_cases)

        # Evolve
        with patch("app.agents.self_evolution.evolution_controller", ctrl), \
             patch("app.agents.evolution.skill_registry", sr):
            result = ctrl.evolve_skill(scenario["name"], original, iterations=5)

        evolved = result["best_candidate"]["content"]
        evolved_scores = fe.evaluate_rule_based(evolved, eval_cases)

        return {
            "scenario": scenario["name"],
            "original": original,
            "evolved": evolved,
            "baseline_scores": baseline_scores,
            "evolved_scores": evolved_scores,
            "improvement": result["improvement"],
            "total_candidates": result["total_candidates"],
            "generations": result["generations"],
            "skill_registry": sr,
        }

    def test_scenario_customer_support(self):
        """客服助手场景必须通过所有严格检查。"""
        self._assert_scenario_passes(SKILL_SCENARIOS[0])

    def test_scenario_code_review(self):
        """代码审查助手场景必须通过所有严格检查。"""
        self._assert_scenario_passes(SKILL_SCENARIOS[1])

    def test_scenario_data_analyst(self):
        """数据分析师场景必须通过所有严格检查。"""
        self._assert_scenario_passes(SKILL_SCENARIOS[2])

    def _assert_scenario_passes(self, scenario: dict):
        """Run all strict quality checks on a scenario."""
        r = self._run_scenario(scenario)
        name = r["scenario"]
        original = r["original"]
        evolved = r["evolved"]

        print(f"\n{'='*60}")
        print(f"  严格测试: {name}")
        print(f"{'='*60}")
        print(f"  原始 ({len(original)} chars): {original[:80]}...")
        print(f"  进化 ({len(evolved)} chars): {evolved[:80]}...")
        print(f"  候选数: {r['total_candidates']}, 代数: {r['generations']}")

        # ── Check 1: Significant improvement (>15%) ──
        baseline_total = r["baseline_scores"]["total"]
        evolved_total = r["evolved_scores"]["total"]
        if baseline_total > 0:
            improvement_pct = (evolved_total - baseline_total) / baseline_total
        else:
            improvement_pct = 1.0 if evolved_total > 0 else 0.0
        print(f"\n  [Check 1] 显著提升: {baseline_total:.4f} → {evolved_total:.4f} ({improvement_pct:+.0%})")
        self.assertGreater(improvement_pct, 0.15,
                           f"{name}: 提升不足15% (实际 {improvement_pct:.0%})")

        # ── Check 2: No duplicate sections ──
        ok, msg = self.checker.check_no_duplicate_sections(evolved)
        print(f"  [Check 2] 无重复段落: {'PASS' if ok else 'FAIL'} — {msg}")
        self.assertTrue(ok, f"{name}: {msg}")

        # ── Check 3: No garbage content ──
        ok, msg = self.checker.check_no_garbage(evolved)
        print(f"  [Check 3] 无乱码/垃圾: {'PASS' if ok else 'FAIL'} — {msg}")
        self.assertTrue(ok, f"{name}: {msg}")

        # ── Check 4: Good structure ──
        ok, struct_score, msg = self.checker.check_structure_quality(evolved)
        print(f"  [Check 4] 结构质量: {'PASS' if ok else 'FAIL'} (score={struct_score:.2f}) — {msg}")
        self.assertTrue(ok, f"{name}: 结构质量不足 ({struct_score:.2f}) — {msg}")

        # ── Check 5: Intent preserved ──
        ok, msg = self.checker.check_intent_preserved(original, evolved)
        print(f"  [Check 5] 意图保留: {'PASS' if ok else 'FAIL'} — {msg}")
        self.assertTrue(ok, f"{name}: {msg}")

        # ── Check 6: Usable as system prompt ──
        ok, msg = self.checker.check_usable_as_system_prompt(evolved)
        print(f"  [Check 6] 可用性: {'PASS' if ok else 'FAIL'} — {msg}")
        self.assertTrue(ok, f"{name}: {msg}")

        # ── Check 7: Every sub-score improved or maintained ──
        print(f"\n  Sub-score comparison:")
        for key in ["length_score", "structure_score", "specificity_score", "coverage_score"]:
            before = r["baseline_scores"][key]
            after = r["evolved_scores"][key]
            delta = after - before
            status = "📈" if delta > 0 else ("⏸️ " if delta == 0 else "📉")
            print(f"    {status} {key}: {before:.3f} → {after:.3f} ({delta:+.3f})")
        # At least 2 sub-scores must improve
        improved_sub_count = sum(
            1 for key in ["length_score", "structure_score", "specificity_score", "coverage_score"]
            if r["evolved_scores"][key] > r["baseline_scores"][key]
        )
        print(f"  [Check 7] ≥2个子维度提升: {'PASS' if improved_sub_count >= 2 else 'FAIL'} ({improved_sub_count}/4)")
        self.assertGreaterEqual(improved_sub_count, 2,
                                f"{name}: 只有 {improved_sub_count}/4 个子维度提升")

        # ── Check 8: Evolved content is substantially longer (actual content added) ──
        len_ratio = len(evolved) / max(1, len(original))
        print(f"  [Check 8] 内容丰富度: {len(original)} → {len(evolved)} chars ({len_ratio:.1f}x)")
        self.assertGreater(len_ratio, 2.0,
                           f"{name}: 进化后内容增长不足 ({len_ratio:.1f}x, 需要>2x)")

        # ── Check 9: Auto-deployed to SkillRegistry ──
        sr = r["skill_registry"]
        deployed = sr.get_skill(name)
        if r["improvement"] > 0.02:
            self.assertIsNotNone(deployed, f"{name}: 技能未找到")
            self.assertNotEqual(deployed["system_prompt"], original,
                               f"{name}: 进化结果未部署回 SkillRegistry")
            print(f"  [Check 9] 自动部署: PASS (v{deployed.get('version', '?')})")
        else:
            print(f"  [Check 9] 自动部署: SKIP (improvement {r['improvement']:.4f} < 0.02)")

        print(f"\n  ✅ {name}: 全部严格检查通过")

    def test_evolution_idempotent(self):
        """Running evolution twice should not produce duplicate sections."""
        from app.agents.self_evolution import EvolutionController, TraceEntry
        from app.agents.evolution import SkillRegistry

        sr = SkillRegistry()
        sr.create_skill("idem_test", "Idem", "test", "你是测试助手。")

        ctrl = EvolutionController()
        for i in range(15):
            ctrl.trace_collector.record(TraceEntry(
                timestamp=datetime.now().isoformat(),
                thread_id=f"t-idem-{i}",
                skill_name="idem_test",
                user_input=f"q{i}",
                agent_output="short" if i < 10 else f"good answer {i}" * 5,
                success=i >= 10,
                score=0.2 if i < 10 else 0.8,
            ))

        # First evolution
        with patch("app.agents.self_evolution.evolution_controller", ctrl), \
             patch("app.agents.evolution.skill_registry", sr):
            r1 = ctrl.evolve_skill("idem_test", "你是测试助手。", iterations=5)

        evolved_v1 = r1["best_candidate"]["content"]

        # Second evolution on the evolved result
        with patch("app.agents.self_evolution.evolution_controller", ctrl), \
             patch("app.agents.evolution.skill_registry", sr):
            r2 = ctrl.evolve_skill("idem_test", evolved_v1, iterations=5)

        evolved_v2 = r2["best_candidate"]["content"]

        # No duplicate sections
        ok, msg = self.checker.check_no_duplicate_sections(evolved_v2)
        self.assertTrue(ok, f"After 2nd evolution: {msg}")

        ok, msg = self.checker.check_no_garbage(evolved_v2)
        self.assertTrue(ok, f"After 2nd evolution: {msg}")

    def test_all_scenarios_summary(self):
        """Run all 3 scenarios and print a comparison table."""
        results = []
        for scenario in SKILL_SCENARIOS:
            r = self._run_scenario(scenario)
            results.append(r)

        print(f"\n{'='*70}")
        print("  进化效果汇总")
        print(f"{'='*70}")
        print(f"  {'技能':<20} {'基线':>8} {'进化后':>8} {'提升':>8} {'候选':>6} {'长度变化':>12}")
        print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*12}")
        for r in results:
            b = r["baseline_scores"]["total"]
            e = r["evolved_scores"]["total"]
            pct = (e - b) / b if b > 0 else 0
            print(f"  {r['scenario']:<20} {b:>8.4f} {e:>8.4f} {pct:>+7.0%} {r['total_candidates']:>6} "
                  f"{len(r['original']):>5}→{len(r['evolved']):<5}")

        # All must have improved
        for r in results:
            self.assertGreater(r["improvement"], 0,
                               f"{r['scenario']}: no improvement")


if __name__ == "__main__":
    unittest.main(verbosity=2)
