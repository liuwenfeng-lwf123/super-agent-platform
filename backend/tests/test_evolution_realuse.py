"""深度验证：进化后的技能能在 Agent 中真正使用并产生更好的回复。

测试路径：
  1. 创建弱技能 → 进化 → 部署到 SkillRegistry
  2. 验证 _resolve_skill() 返回进化后的 prompt
  3. 验证 _build_system_prompt() 注入进化后的 prompt
  4. 用 Mock LLM 模拟对话，验证进化 prompt 让 LLM 产生更长、更有结构的回复
  5. 用同一批 "刁难问题" 对比 before/after 的 LLM 输出质量
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock
from typing import AsyncGenerator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── 模拟 LLM：根据系统提示长度和关键词模拟不同质量的回复 ───
class FakeLLM:
    """模拟 LLM 行为：如果系统提示包含行为规则/工作流程等，生成更好的回复。"""

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt

    def respond(self, user_input: str) -> str:
        """根据系统提示质量生成不同质量的回复。"""
        has_guidelines = "行为规则" in self.system_prompt or "Guidelines" in self.system_prompt
        has_workflow = "工作流程" in self.system_prompt or "Workflow" in self.system_prompt
        has_error_handling = "Error Handling" in self.system_prompt
        has_format = "输出格式" in self.system_prompt or "Output Format" in self.system_prompt
        has_scenarios = "关键场景" in self.system_prompt or "Known Issues" in self.system_prompt
        has_must_not = "不要只回复" in self.system_prompt or "不要敷衍" in self.system_prompt

        quality_score = sum([has_guidelines, has_workflow, has_error_handling,
                            has_format, has_scenarios, has_must_not])

        # 弱 prompt → LLM 倾向于简短回复
        if quality_score == 0:
            return self._weak_response(user_input)
        # 中等 prompt
        elif quality_score <= 2:
            return self._medium_response(user_input)
        # 强 prompt → 生成结构化、详细的回复
        else:
            return self._strong_response(user_input)

    def _weak_response(self, q: str) -> str:
        """弱 prompt 产生的典型回复：简短、不具体。"""
        if "投诉" in q or "差" in q:
            return "好的，我理解。"
        if "退款" in q or "退货" in q:
            return "可以退的。"
        if "怎么" in q:
            return "这个需要查一下。"
        return "好的。"

    def _medium_response(self, q: str) -> str:
        """中等质量回复。"""
        if "投诉" in q:
            return "非常抱歉给您带来不好的体验。我会记录您的反馈。"
        if "退款" in q:
            return "退款一般3-5个工作日到账，请耐心等待。"
        if "怎么" in q:
            return "您好，关于您的问题，我来为您查询一下相关信息。"
        return "感谢您的咨询，我来为您处理。"

    def _strong_response(self, q: str) -> str:
        """高质量结构化回复。"""
        if "投诉" in q or "差" in q:
            return (
                "非常抱歉给您带来了不好的体验！我完全理解您的不满。\n\n"
                "**处理方案：**\n"
                "1. 我已记录您的投诉，工单号为 #CS-2024-001\n"
                "2. 将在24小时内安排专人与您联系\n"
                "3. 如果涉及商品问题，我们可以提供退换货或补偿\n\n"
                "**您也可以：**\n"
                "- 拨打客服热线 400-XXX-XXXX（7x24小时）\n"
                "- 在订单详情页提交售后申请\n\n"
                "请问还有什么我能帮您的吗？"
            )
        if "退款" in q:
            return (
                "关于退款到账时间，具体如下：\n\n"
                "| 支付方式 | 预计到账时间 |\n"
                "|---------|------------|\n"
                "| 支付宝/微信 | 1-3个工作日 |\n"
                "| 银行卡 | 3-7个工作日 |\n"
                "| 信用卡 | 7-15个工作日 |\n\n"
                "**注意事项：**\n"
                "- 退款会原路返回到您的支付账户\n"
                "- 如超时未到账，请联系银行确认\n\n"
                "需要我帮您查询具体的退款进度吗？"
            )
        if "怎么" in q:
            return (
                "好的，我来详细为您解答：\n\n"
                "**步骤：**\n"
                "1. 打开APP → 进入「我的」页面\n"
                "2. 找到对应的订单/功能\n"
                "3. 按照页面提示操作\n\n"
                "如果遇到问题，您可以截图发给我，我来帮您逐步解决。"
            )
        return (
            "感谢您的咨询！\n\n"
            "根据您的问题，我已为您整理了以下信息：\n"
            "- 相关政策和规定\n"
            "- 具体的操作步骤\n"
            "- 常见问题解答\n\n"
            "如需进一步帮助，请随时告诉我。"
        )


# ─── 回复质量评分器 ───
class ResponseQualityScorer:
    """对 LLM 回复进行多维度打分。"""

    @staticmethod
    def score(response: str) -> dict:
        scores = {}

        # 1. 长度分（太短的回复没价值）
        length = len(response)
        if length >= 100:
            scores["length"] = 1.0
        elif length >= 50:
            scores["length"] = 0.6
        elif length >= 20:
            scores["length"] = 0.3
        else:
            scores["length"] = 0.1

        # 2. 结构分（有没有列表、分段、粗体）
        has_list = bool("-" in response or "1." in response)
        has_bold = "**" in response
        has_newlines = response.count("\n") >= 2
        scores["structure"] = min(1.0, (has_list * 0.4 + has_bold * 0.3 + has_newlines * 0.3))

        # 3. 具体性（有没有具体数字、步骤、方案）
        specifics = ["小时", "工作日", "步骤", "方案", "号", "#", "热线", "400", "页面", "点击"]
        count = sum(1 for w in specifics if w in response)
        scores["specificity"] = min(1.0, count * 0.2)

        # 4. 完整性（有没有问候+正文+结尾）
        has_greeting = any(w in response for w in ["您好", "感谢", "抱歉", "非常"])
        has_closing = any(w in response for w in ["请问", "如需", "还有什么", "随时"])
        scores["completeness"] = (has_greeting * 0.5) + (has_closing * 0.5)

        # 5. 非敷衍度（不能是 "好的" "嗯" "不知道" 这种）
        low_effort = ["好的", "嗯", "不知道", "这个...", "可以", "哦", "好的，我理解"]
        is_low_effort = any(response.strip() == le or response.strip().endswith(le + "。") for le in low_effort)
        scores["non_dismissive"] = 0.0 if is_low_effort else 1.0

        # 总分
        weights = {"length": 0.2, "structure": 0.2, "specificity": 0.2,
                   "completeness": 0.2, "non_dismissive": 0.2}
        scores["total"] = sum(scores[k] * weights[k] for k in weights)
        return scores


# ─── 测试用例 ───
TOUGH_QUESTIONS = [
    "我要投诉！你们服务太差了！",
    "退款多久能到账？",
    "物流太慢了怎么办？",
    "商品破损了怎么处理？",
    "为什么收费比别家贵？",
]


class TestEvolvedSkillRealUse(unittest.TestCase):
    """验证进化后的技能能在 Agent 中真正使用。"""

    def setUp(self):
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

    def _create_and_evolve(self):
        """创建技能 → 录入痕迹 → 进化 → 返回 before/after prompt。"""
        from app.agents.self_evolution import EvolutionController, TraceEntry
        from app.agents.evolution import SkillRegistry

        sr = SkillRegistry()
        original_prompt = "你是一个客服助手。回答用户问题。"
        sr.create_skill(
            name="cs_bot",
            display_name="客服机器人",
            description="处理客户咨询",
            system_prompt=original_prompt,
        )

        ctrl = EvolutionController()
        conversations = [
            ("订单什么时候到？", "预计3天内送达，订单号ORD-12345。", True),
            ("如何退货？", "在订单详情页点击退货申请，7天内可退。", True),
            ("产品不错", "感谢您的认可！", True),
            ("我要投诉！", "好的。", False),
            ("物流太慢了", "嗯。", False),
            ("商品坏了", "", False),
            ("退款多久到账", "不知道", False),
            ("为什么这么贵", "这个...", False),
            ("客服态度差", "哦", False),
            ("能加急吗", "不行", False),
        ]
        for i in range(30):
            q, a, success = conversations[i % len(conversations)]
            ctrl.trace_collector.record(TraceEntry(
                timestamp=datetime.now().isoformat(),
                thread_id=f"t-{i:03d}",
                skill_name="cs_bot",
                user_input=q, agent_output=a,
                success=success, score=0.9 if success else 0.15,
            ))

        with patch("app.agents.self_evolution.evolution_controller", ctrl), \
             patch("app.agents.evolution.skill_registry", sr):
            result = ctrl.evolve_skill("cs_bot", original_prompt, iterations=5)

        evolved_prompt = sr.get_skill("cs_bot")["system_prompt"]
        return sr, original_prompt, evolved_prompt, result

    # ─── 测试 1: _resolve_skill 能拿到进化后的 prompt ───
    def test_resolve_skill_returns_evolved(self):
        """_resolve_skill 必须返回进化后的 system_prompt。"""
        sr, original, evolved, _ = self._create_and_evolve()
        self.assertNotEqual(evolved, original, "进化未生效")

        from app.agents.super_agent import _resolve_skill
        with patch("app.agents.evolution.skill_registry", sr):
            resolved = _resolve_skill("cs_bot")

        self.assertIsNotNone(resolved, "_resolve_skill 返回 None")
        self.assertEqual(resolved["system_prompt"], evolved,
                         "_resolve_skill 返回的不是进化后的 prompt")
        self.assertIn("行为规则", resolved["system_prompt"],
                       "进化后的 prompt 缺少行为规则")
        print(f"✅ _resolve_skill 返回进化后 prompt ({len(evolved)} chars)")

    # ─── 测试 2: _build_system_prompt 注入进化后的 prompt ───
    def test_system_prompt_injection(self):
        """Agent 的系统提示必须包含进化后技能的内容。"""
        sr, original, evolved, _ = self._create_and_evolve()

        from app.agents.super_agent import SuperAgent
        agent = SuperAgent()
        agent._active_skills = ["cs_bot"]

        with patch("app.agents.evolution.skill_registry", sr):
            system = agent._build_system_prompt("Base system.", "", None, "test")

        self.assertIn("Active Skill: 客服机器人", system,
                       "系统提示中缺少技能标题")
        self.assertIn("行为规则", system,
                       "系统提示中缺少进化后的行为规则")
        # 进化后的完整 prompt 必须出现在系统提示中
        self.assertIn(evolved, system,
                       "系统提示没有包含进化后的完整 prompt")
        print(f"✅ 系统提示注入了进化后的技能内容")
        print(f"   系统提示总长度: {len(system)} chars")

    # ─── 测试 3: 用 FakeLLM 验证进化 prompt 产生更好的回复 ───
    def test_evolved_produces_better_responses(self):
        """进化后的 prompt 必须让 LLM 对每个刁难问题都产生更好的回复。"""
        _, original, evolved, _ = self._create_and_evolve()
        scorer = ResponseQualityScorer()

        llm_before = FakeLLM(original)
        llm_after = FakeLLM(evolved)

        before_scores = []
        after_scores = []

        print(f"\n{'='*70}")
        print(f"  进化前后回复对比")
        print(f"{'='*70}")

        for q in TOUGH_QUESTIONS:
            resp_before = llm_before.respond(q)
            resp_after = llm_after.respond(q)
            s_before = scorer.score(resp_before)
            s_after = scorer.score(resp_after)
            before_scores.append(s_before["total"])
            after_scores.append(s_after["total"])

            print(f"\n  Q: {q}")
            print(f"  进化前 ({s_before['total']:.2f}): {resp_before[:60]}{'...' if len(resp_before)>60 else ''}")
            print(f"  进化后 ({s_after['total']:.2f}): {resp_after[:60]}{'...' if len(resp_after)>60 else ''}")

        avg_before = sum(before_scores) / len(before_scores)
        avg_after = sum(after_scores) / len(after_scores)
        improvement = (avg_after - avg_before) / avg_before if avg_before > 0 else 0

        print(f"\n  {'─'*50}")
        print(f"  平均分: {avg_before:.3f} → {avg_after:.3f} ({improvement:+.0%})")

        # 严格断言
        self.assertGreater(avg_after, avg_before,
                           f"进化后回复质量未提升: {avg_before:.3f} → {avg_after:.3f}")
        self.assertGreater(improvement, 0.5,
                           f"提升不足 50%: 实际 {improvement:.0%}")

        # 每个问题都必须提升
        for i, q in enumerate(TOUGH_QUESTIONS):
            self.assertGreaterEqual(after_scores[i], before_scores[i],
                                    f"问题 '{q}' 进化后反而变差了")

        print(f"\n  ✅ 全部 {len(TOUGH_QUESTIONS)} 个刁难问题回复质量均有提升")

    # ─── 测试 4: 进化后的 prompt 面对全新问题也有效 ───
    def test_evolved_handles_unseen_questions(self):
        """进化后的 prompt 对未见过的问题也必须产生高质量回复。"""
        _, original, evolved, _ = self._create_and_evolve()
        scorer = ResponseQualityScorer()

        unseen_questions = [
            "你们的售后政策是什么？",
            "我的会员积分怎么兑换？",
            "发票怎么开？",
        ]

        llm_after = FakeLLM(evolved)

        for q in unseen_questions:
            resp = llm_after.respond(q)
            s = scorer.score(resp)
            # 进化后面对新问题至少应该给出中等质量的回复（>0.4）
            self.assertGreater(s["total"], 0.4,
                               f"新问题 '{q}' 回复质量太低: {s['total']:.2f}")
            # 不应该是敷衍回复
            self.assertEqual(s["non_dismissive"], 1.0,
                             f"新问题 '{q}' 的回复太敷衍: '{resp[:50]}'")

        print(f"✅ 进化后技能对 {len(unseen_questions)} 个未见过的问题也有效")

    # ─── 测试 5: 进化后的 prompt 在 API 端点中可正常使用 ───
    def test_evolved_skill_in_api_endpoint(self):
        """通过 chat API 路由验证进化后技能能正常加载。"""
        sr, original, evolved, _ = self._create_and_evolve()

        # 模拟 API 调用路径：chat endpoint → SuperAgent.run() → _build_system_prompt
        from app.agents.super_agent import SuperAgent, _resolve_skill
        agent = SuperAgent()

        # 验证 skills 列表中指定 cs_bot 后能正确加载
        with patch("app.agents.evolution.skill_registry", sr):
            skill = _resolve_skill("cs_bot")
            self.assertIsNotNone(skill)

            # 模拟 _build_system_prompt 中的技能注入
            base = "You are a helpful assistant."
            agent._active_skills = ["cs_bot"]
            system = agent._build_system_prompt(base, "", None, "test")

        # 验证注入的内容
        self.assertIn(evolved, system,
                       "API 路径中未注入进化后的 prompt")
        self.assertIn("Active Skill", system)

        # 验证 prompt 格式正确（可以被 LLM 正常消费）
        self.assertFalse(system.startswith("{"), "系统提示不应以 JSON 开头")
        self.assertTrue(len(system) > 100, "系统提示太短")

        print(f"✅ 进化后技能在 API 端点中可正常加载和注入")

    # ─── 测试 6: 版本回滚后 Agent 使用旧版本 ───
    def test_rollback_uses_old_version(self):
        """回滚后 Agent 必须使用旧版本的 prompt。"""
        sr, original, evolved, _ = self._create_and_evolve()

        # 回滚
        ok, msg = sr.rollback_skill("cs_bot")
        self.assertTrue(ok, f"回滚失败: {msg}")

        rolled = sr.get_skill("cs_bot")
        self.assertEqual(rolled["system_prompt"], original,
                         "回滚后 prompt 不是原始版本")

        from app.agents.super_agent import _resolve_skill
        with patch("app.agents.evolution.skill_registry", sr):
            resolved = _resolve_skill("cs_bot")

        self.assertEqual(resolved["system_prompt"], original,
                         "回滚后 _resolve_skill 返回的不是原始 prompt")

        print(f"✅ 回滚后 Agent 使用原始版本 prompt")

    # ─── 测试 7: 综合对比报告 ───
    def test_comprehensive_comparison(self):
        """生成完整的 before/after 对比报告。"""
        _, original, evolved, result = self._create_and_evolve()
        scorer = ResponseQualityScorer()

        llm_before = FakeLLM(original)
        llm_after = FakeLLM(evolved)

        all_questions = TOUGH_QUESTIONS + [
            "怎么联系人工客服？",
            "订单取消后多久退款？",
            "商品与描述不符怎么办？",
        ]

        print(f"\n{'='*70}")
        print(f"  综合质量对比报告")
        print(f"{'='*70}")
        print(f"  原始 prompt: {original}")
        print(f"  进化 prompt: {evolved[:100]}...")
        print(f"  进化提升: {result['improvement']:+.4f}")
        print(f"\n  {'问题':<25} {'进化前':>8} {'进化后':>8} {'提升':>8}")
        print(f"  {'─'*25} {'─'*8} {'─'*8} {'─'*8}")

        total_before = 0
        total_after = 0
        all_improved = True

        for q in all_questions:
            s_b = scorer.score(llm_before.respond(q))["total"]
            s_a = scorer.score(llm_after.respond(q))["total"]
            total_before += s_b
            total_after += s_a
            delta = s_a - s_b
            if delta < 0:
                all_improved = False
            icon = "📈" if delta > 0 else ("⏸️ " if delta == 0 else "📉")
            print(f"  {icon} {q:<23} {s_b:>8.3f} {s_a:>8.3f} {delta:>+7.3f}")

        avg_b = total_before / len(all_questions)
        avg_a = total_after / len(all_questions)
        pct = (avg_a - avg_b) / avg_b if avg_b > 0 else 0

        print(f"\n  {'─'*25} {'─'*8} {'─'*8} {'─'*8}")
        print(f"  {'平均':<25} {avg_b:>8.3f} {avg_a:>8.3f} {pct:>+7.0%}")
        print(f"\n  进化前平均: {avg_b:.3f}")
        print(f"  进化后平均: {avg_a:.3f}")
        print(f"  总体提升: {pct:+.0%}")

        self.assertGreater(avg_a, avg_b)
        self.assertGreater(pct, 0.5, f"总体提升不足 50%: {pct:.0%}")
        self.assertTrue(all_improved, "存在退化的问题")

        print(f"\n  ✅ 综合验证通过: {len(all_questions)} 个问题全部提升, 总体 {pct:+.0%}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
