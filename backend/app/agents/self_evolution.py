"""
Self-Evolution Engine — inspired by Hermes Agent Self-Evolution (NousResearch).

Implements the evolution loop:
  1. Trace Collection    — record execution traces (tool calls, results, scores)
  2. Eval Dataset Build  — mine traces + synthetic generation for test cases
  3. Mutation Engine      — LLM-based prompt/skill/tool mutation with reflection
  4. Fitness Evaluation   — score candidates on eval dataset
  5. Selection & Deploy   — Pareto-aware selection, constraint gates, version control

No external ML library needed. Uses LLM API calls for mutation and evaluation.
"""
import json
import os
import math
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field, fields, asdict
from typing import Optional, Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "evolution")
TRACES_DIR = os.path.join(DATA_DIR, "traces")
EVAL_DIR = os.path.join(DATA_DIR, "eval_datasets")
CANDIDATES_DIR = os.path.join(DATA_DIR, "candidates")
HISTORY_PATH = os.path.join(DATA_DIR, "evolution_history.json")

for d in [DATA_DIR, TRACES_DIR, EVAL_DIR, CANDIDATES_DIR]:
    os.makedirs(d, exist_ok=True)


async def _aclose_langchain_model(model: Any) -> None:
    """Best-effort close of langchain chat model internals.

    langchain_openai / langchain_anthropic lazily build an ``httpx.AsyncClient``
    bound to the first event loop that invokes them. When we drive them via
    short-lived ``asyncio.run`` calls the client lingers past loop shutdown and
    triggers ``ResourceWarning`` noise. Close any known internals we can find.
    """
    if model is None:
        return
    for attr in ("async_client", "_async_client"):
        client = getattr(model, attr, None)
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception as e:
                logger.debug("Suppressed error in self_evolution: %s", e)


def _run_coro_isolated(coro: Any) -> Any:
    """Run a coroutine on a fresh event loop and fully drain transports.

    ``asyncio.run`` closes the loop eagerly; internal httpx/anyio transports
    can linger past that point and trigger ``ResourceWarning``. Here we run
    the coro, then deliberately give the loop a beat to let close callbacks
    fire before tearing it down.
    """
    import asyncio as _asyncio

    loop = _asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
        # Cancel anything stragglers, then let transports close cleanly.
        pending = _asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                _asyncio.gather(*pending, return_exceptions=True)
            )
        loop.run_until_complete(_asyncio.sleep(0))
        loop.run_until_complete(loop.shutdown_asyncgens())
        try:
            loop.run_until_complete(loop.shutdown_default_executor())
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
        return result
    finally:
        try:
            loop.close()
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)


# ---------------------------------------------------------------------------
# 1. Execution Trace Collection
# ---------------------------------------------------------------------------
@dataclass
class TraceEntry:
    timestamp: str
    thread_id: str
    skill_name: str
    user_input: str
    agent_output: str
    tool_calls: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    success: Optional[bool] = None
    user_feedback: Optional[str] = None
    score: Optional[float] = None


class TraceCollector:
    """Collects execution traces for evolution analysis."""

    MAX_TRACES_PER_SKILL = 500

    def record(self, trace: TraceEntry):
        skill_dir = os.path.join(TRACES_DIR, trace.skill_name or "_default")
        os.makedirs(skill_dir, exist_ok=True)
        log_path = os.path.join(skill_dir, "traces.jsonl")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")

        # Cap file size
        self._trim(log_path)

    def get_traces(self, skill_name: str, limit: int = 100) -> list[dict]:
        log_path = os.path.join(TRACES_DIR, skill_name, "traces.jsonl")
        if not os.path.exists(log_path):
            return []
        lines = Path(log_path).read_text().strip().split("\n")
        traces = []
        for line in lines[-limit:]:
            try:
                traces.append(json.loads(line))
            except Exception as e:
                logger.debug("Suppressed error in self_evolution: %s", e)
        return traces

    def get_failure_traces(self, skill_name: str, limit: int = 50) -> list[dict]:
        traces = self.get_traces(skill_name, limit=self.MAX_TRACES_PER_SKILL)
        return [t for t in traces if t.get("success") is False or (t.get("score") or 1.0) < 0.5][-limit:]

    def get_success_traces(self, skill_name: str, limit: int = 50) -> list[dict]:
        traces = self.get_traces(skill_name, limit=self.MAX_TRACES_PER_SKILL)
        return [t for t in traces if t.get("success") is True or (t.get("score") or 0.0) >= 0.7][-limit:]

    def _trim(self, path: str):
        try:
            lines = Path(path).read_text().strip().split("\n")
            if len(lines) > self.MAX_TRACES_PER_SKILL:
                Path(path).write_text("\n".join(lines[-self.MAX_TRACES_PER_SKILL:]) + "\n")
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)

    def get_skill_stats(self) -> dict:
        stats = {}
        if not os.path.exists(TRACES_DIR):
            return stats
        for d in Path(TRACES_DIR).iterdir():
            if d.is_dir():
                traces = self.get_traces(d.name, limit=1000)
                total = len(traces)
                successes = sum(1 for t in traces if t.get("success") is True)
                failures = sum(1 for t in traces if t.get("success") is False)
                avg_score = sum(t.get("score", 0) for t in traces if t.get("score") is not None) / max(1, sum(1 for t in traces if t.get("score") is not None))
                stats[d.name] = {
                    "total_traces": total,
                    "successes": successes,
                    "failures": failures,
                    "success_rate": round(successes / max(1, successes + failures), 3),
                    "avg_score": round(avg_score, 3),
                }
        return stats


# ---------------------------------------------------------------------------
# 2. Eval Dataset Builder
# ---------------------------------------------------------------------------
@dataclass
class EvalCase:
    input_text: str
    expected_behavior: str       # rubric, not exact match
    category: str = "general"    # e.g. "accuracy", "conciseness", "tool_usage"
    source: str = "synthetic"    # synthetic / session / golden


class EvalDatasetBuilder:
    """Build evaluation datasets from traces or synthetic generation."""

    def build_from_traces(self, skill_name: str, traces: list[dict], max_cases: int = 30) -> list[dict]:
        """Convert execution traces into eval cases."""
        cases = []
        for t in traces[:max_cases]:
            case = {
                "input_text": t.get("user_input", ""),
                "expected_behavior": self._infer_rubric(t),
                "category": "session",
                "source": "session",
                "reference_output": t.get("agent_output", "")[:500],
                "reference_score": t.get("score"),
            }
            if case["input_text"]:
                cases.append(case)
        return cases

    def build_synthetic(self, skill_description: str, num_cases: int = 15) -> list[dict]:
        """Generate synthetic eval cases from a skill description (no LLM call — template-based)."""
        cases = []
        templates = [
            ("基础功能测试", "应正确执行核心功能并返回有用结果"),
            ("边界情况", "应优雅处理异常输入而不崩溃"),
            ("效率测试", "应在合理的工具调用次数内完成"),
            ("输出质量", "输出应清晰、结构化、可操作"),
            ("错误处理", "遇到问题时应给出有帮助的错误信息"),
        ]
        for i, (cat, rubric) in enumerate(templates):
            if i >= num_cases:
                break
            cases.append({
                "input_text": f"[{cat}] 使用技能: {skill_description[:100]}",
                "expected_behavior": rubric,
                "category": cat,
                "source": "synthetic",
            })
        return cases

    def _infer_rubric(self, trace: dict) -> str:
        score = trace.get("score", 0.5)
        if score and score >= 0.7:
            return "应产出与参考输出质量相当或更好的结果"
        elif trace.get("user_feedback"):
            return f"应避免: {trace['user_feedback'][:200]}"
        else:
            return "应正确完成任务并返回有用结果"

    def save_dataset(self, skill_name: str, cases: list[dict]):
        path = os.path.join(EVAL_DIR, f"{skill_name}.json")
        Path(path).write_text(json.dumps(cases, ensure_ascii=False, indent=2))

    def load_dataset(self, skill_name: str) -> list[dict]:
        path = os.path.join(EVAL_DIR, f"{skill_name}.json")
        if os.path.exists(path):
            return json.loads(Path(path).read_text())
        return []


# ---------------------------------------------------------------------------
# 3. Mutation Engine (LLM-based prompt evolution)
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    id: str
    content: str            # mutated skill/prompt/tool text
    parent_id: str          # which candidate it evolved from
    mutation_type: str      # "rephrase" / "expand" / "simplify" / "fix_failure" / "merge"
    generation: int
    fitness: Optional[float] = None
    eval_scores: dict = field(default_factory=dict)


class MutationEngine:
    """Generate candidate variants through mutation strategies."""

    MUTATION_PROMPTS = {
        "rephrase": "重写以下内容，保持语义不变但表述更清晰、更具体:\n\n{content}",
        "expand": "扩展以下内容，增加更多细节和边界情况处理:\n\n{content}",
        "simplify": "精简以下内容，去掉冗余，保留核心逻辑:\n\n{content}",
        "fix_failure": "以下技能/提示在某些场景下失败了。根据失败案例改进它:\n\n原始内容:\n{content}\n\n失败案例:\n{failures}",
        "merge": "合并以下两个版本的优点:\n\n版本A:\n{content}\n\n版本B:\n{other}",
    }

    def generate_mutations(
        self,
        original: str,
        failure_examples: list[str] | None = None,
        other_candidate: str | None = None,
        num_variants: int = 3,
        use_llm: bool = True,
    ) -> list[dict]:
        """Generate mutation candidates. Tries LLM-based mutation first, falls back to rule-based."""
        candidates = []
        gen_id = hashlib.md5(original.encode()).hexdigest()[:8]

        # --- LLM-based mutations (primary path) ---
        if use_llm:
            llm_candidates = self._try_llm_mutations(
                original, failure_examples, other_candidate, num_variants, gen_id
            )
            if llm_candidates:
                return llm_candidates[:num_variants]

        # --- Rule-based fallback ---
        # Strategy 1: Add structure markers
        structured = self._add_structure(original)
        if structured != original:
            candidates.append({
                "id": f"{gen_id}_struct",
                "content": structured,
                "mutation_type": "rephrase",
                "description": "Added structural markers for clarity",
            })

        # Strategy 2: Add error handling guidance
        with_errors = original + "\n\n## Error Handling\n- 遇到错误时，先诊断根因再尝试修复\n- 如果连续失败3次，停止并报告问题\n- 保留错误上下文以便调试"
        candidates.append({
            "id": f"{gen_id}_errh",
            "content": with_errors,
            "mutation_type": "expand",
            "description": "Added error handling guidance",
        })

        # Strategy 3: Failure-aware fix
        if failure_examples:
            failure_text = "\n".join(f"- {ex[:200]}" for ex in failure_examples[:5])
            fix = original + f"\n\n## Known Issues (auto-detected)\n以下场景需要特别注意:\n{failure_text}"
            candidates.append({
                "id": f"{gen_id}_fix",
                "content": fix,
                "mutation_type": "fix_failure",
                "description": "Added failure-aware guidance",
            })

        # Strategy 4: Concise version
        lines = original.split("\n")
        if len(lines) > 10:
            concise = "\n".join(line for line in lines if line.strip() and not line.strip().startswith("#") or len(line.strip()) > 5)
            candidates.append({
                "id": f"{gen_id}_concise",
                "content": concise,
                "mutation_type": "simplify",
                "description": "Simplified to essential content",
            })

        return candidates[:num_variants]

    def _try_llm_mutations(self, original: str, failures: list[str] | None,
                           other: str | None, num_variants: int, gen_id: str) -> list[dict]:
        """Try to generate mutations via LLM. Returns empty list on failure."""
        # Short-circuit under pytest so we never issue real network calls from
        # unit tests (keeps them fast and free of socket/transport leaks).
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return []
        try:
            import asyncio
            from app.models.provider import llm_provider
            from langchain_core.messages import HumanMessage, SystemMessage

            model = llm_provider.get_chat_model(streaming=False)

            mutation_types = ["rephrase", "expand", "simplify"]
            if failures:
                mutation_types.insert(0, "fix_failure")
            if other:
                mutation_types.append("merge")

            selected_types = list(mutation_types[:num_variants])

            async def _run_all_mutations() -> list[dict]:
                produced: list[dict] = []
                try:
                    for mtype in selected_types:
                        prompt_template = self.MUTATION_PROMPTS.get(
                            mtype, self.MUTATION_PROMPTS["rephrase"]
                        )
                        prompt = prompt_template.format(
                            content=original[:3000],
                            failures="\n".join(failures[:3]) if failures else "",
                            other=other[:1500] if other else "",
                        )
                        sys_msg = SystemMessage(content=(
                            "You are a prompt evolution engine. Generate an improved "
                            "version of the given content. Output ONLY the improved "
                            "content, no explanations or meta-commentary."
                        ))
                        try:
                            result = await asyncio.wait_for(
                                model.ainvoke(
                                    [sys_msg, HumanMessage(content=prompt)]
                                ),
                                timeout=30,
                            )
                        except Exception as e:
                            logger.debug("Suppressed error in self_evolution: %s", e)
                            continue
                        content = result.content if hasattr(result, "content") else str(result)
                        if content and content.strip() and len(content) > 20:
                            produced.append({
                                "id": f"{gen_id}_llm_{mtype}",
                                "content": content.strip(),
                                "mutation_type": mtype,
                                "description": f"LLM-generated {mtype} mutation",
                            })
                finally:
                    await _aclose_langchain_model(model)
                return produced

            try:
                asyncio.get_running_loop()
                import concurrent.futures
                total_timeout = 30 * max(len(selected_types), 1) + 10
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        _run_coro_isolated, _run_all_mutations()
                    ).result(timeout=total_timeout)
            except RuntimeError:
                return _run_coro_isolated(_run_all_mutations())
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
            return []

    def _add_structure(self, text: str) -> str:
        lines = text.split("\n")
        if any(line.startswith("##") for line in lines):
            return text
        # Add section headers if text is long enough
        if len(lines) > 5:
            sections = []
            for i, line in enumerate(lines):
                if i == 0:
                    sections.append(f"## Overview\n{line}")
                elif i == len(lines) // 2:
                    sections.append(f"\n## Details\n{line}")
                else:
                    sections.append(line)
            return "\n".join(sections)
        return text

    def crossover(self, parent_a: str, parent_b: str) -> str:
        """GEPA-style crossover: merge best sections from two parents."""
        lines_a = parent_a.split("\n")
        lines_b = parent_b.split("\n")
        # Section-based crossover: take first half from A, second half from B
        mid = min(len(lines_a), len(lines_b)) // 2
        child_lines = lines_a[:mid] + lines_b[mid:]
        return "\n".join(child_lines)

    async def generate_mutations_llm(
        self,
        original: str,
        mutation_type: str,
        failures: list[str] | None = None,
        other: str | None = None,
    ) -> str:
        """LLM-based mutation (requires model access)."""
        prompt_template = self.MUTATION_PROMPTS.get(mutation_type, self.MUTATION_PROMPTS["rephrase"])
        prompt = prompt_template.format(
            content=original[:3000],
            failures="\n".join(failures[:3]) if failures else "",
            other=other[:1500] if other else "",
        )
        model = None
        try:
            from app.models.provider import llm_provider
            model = llm_provider.get_chat_model(streaming=False)
            from langchain_core.messages import HumanMessage
            result = await model.ainvoke([HumanMessage(content=prompt)])
            return result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.warning(f"LLM mutation failed: {e}")
            return original
        finally:
            await _aclose_langchain_model(model)


# ---------------------------------------------------------------------------
# 4. Fitness Evaluation (LLM-as-judge)
# ---------------------------------------------------------------------------
class FitnessEvaluator:
    """Score candidates against eval dataset."""

    def evaluate_rule_based(self, candidate_text: str, eval_cases: list[dict]) -> dict:
        """Rule-based fitness (no LLM needed). Measures structural quality."""
        scores = {
            "length_score": self._length_score(candidate_text),
            "structure_score": self._structure_score(candidate_text),
            "specificity_score": self._specificity_score(candidate_text),
            "coverage_score": self._coverage_score(candidate_text, eval_cases),
        }
        # Weighted average
        weights = {"length_score": 0.15, "structure_score": 0.25, "specificity_score": 0.30, "coverage_score": 0.30}
        total = sum(scores[k] * weights[k] for k in scores)
        scores["total"] = round(total, 4)
        return scores

    def _length_score(self, text: str) -> float:
        """Optimal length: 200-2000 chars for skills."""
        length = len(text)
        if 200 <= length <= 2000:
            return 1.0
        elif length < 200:
            return length / 200
        else:
            return max(0.3, 1.0 - (length - 2000) / 5000)

    def _structure_score(self, text: str) -> float:
        """Reward structured text with headers, lists, etc."""
        score = 0.3  # base
        if "##" in text:
            score += 0.2
        if "- " in text or "* " in text:
            score += 0.15
        if "```" in text:
            score += 0.15
        if any(kw in text.lower() for kw in ["步骤", "step", "guidelines", "规则"]):
            score += 0.2
        return min(1.0, score)

    def _specificity_score(self, text: str) -> float:
        """Reward specific, actionable instructions over vague ones."""
        specifics = ["必须", "不要", "始终", "优先", "如果", "当", "always", "never", "must", "should", "if", "when"]
        count = sum(1 for kw in specifics if kw in text.lower())
        return min(1.0, 0.3 + count * 0.1)

    def _coverage_score(self, text: str, eval_cases: list[dict]) -> float:
        """How many eval case categories are addressed."""
        if not eval_cases:
            return 0.5
        categories = set(c.get("category", "") for c in eval_cases)
        text_lower = text.lower()
        covered = sum(1 for cat in categories if cat.lower() in text_lower or any(kw in text_lower for kw in cat.lower().split()))
        return covered / max(1, len(categories))

    async def evaluate_llm_judge(self, candidate_text: str, eval_case: dict) -> float:
        """LLM-as-judge scoring (0.0 ~ 1.0). For advanced usage."""
        prompt = f"""评估以下技能/提示的质量（0-10分）:

技能内容:
{candidate_text[:2000]}

评估标准:
{eval_case.get('expected_behavior', '应正确完成任务')}

只输出一个数字（0-10）:"""
        model = None
        try:
            from app.models.provider import llm_provider
            model = llm_provider.get_chat_model(streaming=False)
            from langchain_core.messages import HumanMessage
            result = await model.ainvoke([HumanMessage(content=prompt)])
            text = result.content if hasattr(result, "content") else str(result)
            import re
            nums = re.findall(r'\d+\.?\d*', text)
            if nums:
                return min(1.0, float(nums[0]) / 10.0)
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
        finally:
            await _aclose_langchain_model(model)
        return 0.5


# ---------------------------------------------------------------------------
# 5. Selection & Evolution Controller
# ---------------------------------------------------------------------------
@dataclass
class EvolutionRun:
    run_id: str
    target_name: str
    target_type: str  # "skill" / "prompt" / "tool_desc"
    baseline_content: str
    best_candidate: Optional[dict] = None
    generations: int = 0
    total_candidates: int = 0
    improvement: float = 0.0
    status: str = "pending"  # pending / running / completed / failed
    started_at: str = ""
    finished_at: str = ""


class EvolutionController:
    """Orchestrates the full evolution loop."""

    def __init__(self):
        self.trace_collector = TraceCollector()
        self.dataset_builder = EvalDatasetBuilder()
        self.mutation_engine = MutationEngine()
        self.evaluator = FitnessEvaluator()
        self._history: list[dict] = self._load_history()

    def _load_history(self) -> list:
        if os.path.exists(HISTORY_PATH):
            try:
                return json.loads(Path(HISTORY_PATH).read_text())
            except Exception as e:
                logger.debug("Suppressed error in self_evolution: %s", e)
        return []

    def _save_history(self):
        self._history = self._history[-100:]
        Path(HISTORY_PATH).write_text(json.dumps(self._history, ensure_ascii=False, indent=2))

    def evolve_skill(self, skill_name: str, skill_content: str, iterations: int = 5) -> dict:
        """Run evolution loop on a skill (synchronous, rule-based)."""
        run_id = hashlib.md5(f"{skill_name}:{datetime.now().isoformat()}".encode()).hexdigest()[:10]
        run = EvolutionRun(
            run_id=run_id,
            target_name=skill_name,
            target_type="skill",
            baseline_content=skill_content,
            started_at=datetime.now().isoformat(),
        )

        # Build eval dataset
        traces = self.trace_collector.get_traces(skill_name, limit=100)
        if traces:
            eval_cases = self.dataset_builder.build_from_traces(skill_name, traces)
        else:
            eval_cases = self.dataset_builder.build_synthetic(skill_content[:200])
        self.dataset_builder.save_dataset(skill_name, eval_cases)

        # Get failure examples for targeted mutation
        failures = self.trace_collector.get_failure_traces(skill_name)
        failure_texts = [f.get("user_feedback") or f.get("agent_output", "")[:200] for f in failures]

        # Evaluate baseline
        baseline_score = self.evaluator.evaluate_rule_based(skill_content, eval_cases)

        # Evolution loop
        best = {"content": skill_content, "score": baseline_score["total"], "id": "baseline", "scores": baseline_score}
        all_candidates = [best.copy()]

        for gen in range(iterations):
            mutations = self.mutation_engine.generate_mutations(
                best["content"],
                failure_examples=failure_texts if failure_texts else None,
                num_variants=3,
            )

            for m in mutations:
                score = self.evaluator.evaluate_rule_based(m["content"], eval_cases)

                # Constraint gates
                if len(m["content"]) > 15000:  # size limit
                    continue
                if not m["content"].strip():
                    continue

                candidate = {
                    "id": m["id"],
                    "content": m["content"],
                    "score": score["total"],
                    "scores": score,
                    "mutation_type": m["mutation_type"],
                    "generation": gen,
                }
                all_candidates.append(candidate)

                if score["total"] > best["score"]:
                    best = candidate

            run.generations = gen + 1
            run.total_candidates = len(all_candidates)

        # Result
        improvement = best["score"] - baseline_score["total"]
        run.best_candidate = best
        run.improvement = round(improvement, 4)
        run.status = "completed"
        run.finished_at = datetime.now().isoformat()

        # Save
        result = asdict(run)
        self._history.append(result)
        self._save_history()

        # Save best candidate
        if improvement > 0:
            cand_path = os.path.join(CANDIDATES_DIR, f"{skill_name}_evolved.json")
            Path(cand_path).write_text(json.dumps({
                "skill_name": skill_name,
                "original": skill_content[:1000],
                "evolved": best["content"][:5000],
                "improvement": improvement,
                "scores": best.get("scores", {}),
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
            }, ensure_ascii=False, indent=2))

            # Auto-deploy: write evolved content back to SkillRegistry
            self._auto_deploy(skill_name, best["content"], run_id, improvement)

        return result

    def _auto_deploy(self, skill_name: str, evolved_content: str, run_id: str, improvement: float):
        """Deploy evolved skill content back to SkillRegistry if the skill exists."""
        try:
            from app.agents.evolution import skill_registry
            existing = skill_registry.get_skill(skill_name)
            if existing is None:
                logger.info("Evolution skip deploy: skill '%s' not in registry", skill_name)
                return
            # Only deploy if improvement is meaningful (>2%)
            if improvement < 0.02:
                logger.info("Evolution skip deploy: improvement %.4f too small for '%s'", improvement, skill_name)
                return
            ok, msg = skill_registry.edit_skill(skill_name, evolved_content)
            if ok:
                logger.info("Evolution auto-deployed '%s' (run=%s, improvement=+%.4f): %s", skill_name, run_id, improvement, msg)
            else:
                logger.warning("Evolution deploy failed for '%s': %s", skill_name, msg)
        except Exception as e:
            logger.warning("Evolution auto-deploy error for '%s': %s", skill_name, e)

    def auto_triage(self) -> list[dict]:
        """Identify skills that need optimization (by failure rate)."""
        stats = self.trace_collector.get_skill_stats()
        candidates = []
        for name, s in stats.items():
            if s["total_traces"] >= 5 and s["success_rate"] < 0.7:
                candidates.append({
                    "skill_name": name,
                    "success_rate": s["success_rate"],
                    "total_traces": s["total_traces"],
                    "priority": round((1 - s["success_rate"]) * s["total_traces"], 2),
                })
        candidates.sort(key=lambda x: x["priority"], reverse=True)
        return candidates

    def get_history(self, limit: int = 20) -> list[dict]:
        return self._history[-limit:]

    def get_stats(self) -> dict:
        return {
            "total_runs": len(self._history),
            "skill_stats": self.trace_collector.get_skill_stats(),
            "pending_triage": self.auto_triage(),
        }


# ---------------------------------------------------------------------------
# 6. GEPA-style Genetic-Pareto Evolution Engine
# ---------------------------------------------------------------------------
class SemanticPreservation:
    """Check that evolved content preserves the original's semantic intent."""

    @staticmethod
    def check(original: str, evolved: str, threshold: float = 0.3) -> tuple[bool, float]:
        """Return (passes, similarity_score). Uses keyword overlap as proxy."""
        orig_words = set(original.lower().split())
        evol_words = set(evolved.lower().split())
        if not orig_words:
            return True, 1.0
        intersection = orig_words & evol_words
        similarity = len(intersection) / len(orig_words)
        return similarity >= threshold, round(similarity, 4)

    @staticmethod
    async def check_llm(original: str, evolved: str) -> tuple[bool, str]:
        """LLM-based semantic preservation check."""
        model = None
        try:
            from app.models.provider import llm_provider
            model = llm_provider.get_chat_model(streaming=False)
            from langchain_core.messages import HumanMessage
            prompt = f"""Compare these two texts. Do they serve the same purpose?
Answer YES or NO with a brief reason.

Original:
{original[:1500]}

Evolved:
{evolved[:1500]}

Answer (YES/NO):"""
            result = await model.ainvoke([HumanMessage(content=prompt)])
            answer = result.content if hasattr(result, "content") else str(result)
            passes = answer.strip().upper().startswith("YES")
            return passes, answer.strip()
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
            # Fallback to keyword check
            passes, _ = SemanticPreservation.check(original, evolved)
            return passes, "LLM unavailable, used keyword check"
        finally:
            await _aclose_langchain_model(model)


class ParetoSelector:
    """Multi-objective Pareto-aware selection (GEPA pattern).
    Objectives: fitness score, length efficiency, structure quality."""

    @staticmethod
    def is_dominated(a: dict, b: dict, objectives: list[str]) -> bool:
        """Return True if b dominates a (b is better in all objectives)."""
        better_in_all = all(b.get(o, 0) >= a.get(o, 0) for o in objectives)
        strictly_better = any(b.get(o, 0) > a.get(o, 0) for o in objectives)
        return better_in_all and strictly_better

    @staticmethod
    def pareto_front(candidates: list[dict], objectives: list[str]) -> list[dict]:
        """Extract the Pareto front from candidates."""
        front = []
        for c in candidates:
            dominated = any(
                ParetoSelector.is_dominated(c, other, objectives)
                for other in candidates if other["id"] != c["id"]
            )
            if not dominated:
                front.append(c)
        return front

    @staticmethod
    def tournament_select(candidates: list[dict], k: int = 3) -> dict:
        """Tournament selection: pick best from k random candidates."""
        import random
        tournament = random.sample(candidates, min(k, len(candidates)))
        return max(tournament, key=lambda c: c.get("score", 0))


class GEPAEngine:
    """Genetic-Pareto Prompt Evolution — advanced evolution engine.
    Uses crossover, mutation, tournament selection, and Pareto-optimal ranking."""

    def __init__(self):
        self.mutation_engine = MutationEngine()
        self.evaluator = FitnessEvaluator()
        self.semantic = SemanticPreservation()
        self.pareto = ParetoSelector()

    def evolve(self, original: str, eval_cases: list[dict],
               population_size: int = 8, generations: int = 5,
               failure_examples: list[str] = None) -> dict:
        """Run GEPA evolution loop."""
        import random

        # Initialize population
        population = [{"id": "baseline", "content": original,
                        "score": 0, "scores": {}, "generation": 0}]

        # Generate initial population via mutation
        mutations = self.mutation_engine.generate_mutations(
            original, failure_examples=failure_examples,
            num_variants=population_size - 1,
        )
        for m in mutations:
            scores = self.evaluator.evaluate_rule_based(m["content"], eval_cases)
            population.append({
                "id": m["id"], "content": m["content"],
                "score": scores["total"], "scores": scores,
                "generation": 0, "mutation_type": m.get("mutation_type", ""),
            })

        # Evaluate baseline
        baseline_scores = self.evaluator.evaluate_rule_based(original, eval_cases)
        population[0]["score"] = baseline_scores["total"]
        population[0]["scores"] = baseline_scores

        # Evolution loop
        for gen in range(1, generations + 1):
            new_population = []

            # Elitism: keep top 2
            population.sort(key=lambda c: c["score"], reverse=True)
            new_population.extend(population[:2])

            while len(new_population) < population_size:
                # Tournament selection
                parent_a = self.pareto.tournament_select(population)
                parent_b = self.pareto.tournament_select(population)

                # Crossover
                child_content = self.mutation_engine.crossover(
                    parent_a["content"], parent_b["content"]
                )

                # Mutation
                mutations = self.mutation_engine.generate_mutations(
                    child_content, num_variants=1,
                    failure_examples=failure_examples,
                )
                if mutations:
                    child_content = mutations[0]["content"]

                # Constraint gates
                if len(child_content) > 15000:  # size limit
                    continue
                if not child_content.strip():
                    continue

                # Semantic preservation
                preserved, sim = self.semantic.check(original, child_content)
                if not preserved:
                    continue

                # Evaluate
                scores = self.evaluator.evaluate_rule_based(child_content, eval_cases)
                child_id = hashlib.md5(child_content[:500].encode()).hexdigest()[:8]
                new_population.append({
                    "id": f"gen{gen}_{child_id}",
                    "content": child_content,
                    "score": scores["total"],
                    "scores": scores,
                    "generation": gen,
                    "semantic_similarity": sim,
                })

            population = new_population

        # Final selection: Pareto front on multiple objectives
        objectives = ["length_score", "structure_score", "specificity_score", "coverage_score"]
        for c in population:
            for obj in objectives:
                c[obj] = c.get("scores", {}).get(obj, 0)

        front = self.pareto.pareto_front(population, objectives)
        best = max(front, key=lambda c: c["score"]) if front else population[0]

        return {
            "best": best,
            "pareto_front_size": len(front),
            "population_size": len(population),
            "generations": generations,
            "baseline_score": baseline_scores["total"],
            "best_score": best["score"],
            "improvement": round(best["score"] - baseline_scores["total"], 4),
        }


# ---------------------------------------------------------------------------
# 7. Plugin System
# ---------------------------------------------------------------------------
PLUGINS_DIR = os.path.join(DATA_DIR, "..", "plugins")
USER_PLUGINS_DIR = os.path.expanduser("~/.hermes/plugins")
os.makedirs(PLUGINS_DIR, exist_ok=True)


class PluginRegistry:
    """Plugin system supporting user, project, and pip entry points.
    Plugins can register tools, hooks, and CLI commands."""

    DISCOVERY_SOURCES = ["user", "project", "pip"]

    def __init__(self):
        self._plugins: dict[str, dict] = {}

    def discover(self, project_dir: str = "") -> list[dict]:
        """Discover plugins from all sources."""
        found = []

        # User plugins (~/.hermes/plugins/)
        if os.path.isdir(USER_PLUGINS_DIR):
            for p in Path(USER_PLUGINS_DIR).iterdir():
                if p.is_dir() and (p / "plugin.json").exists():
                    found.append(self._load_plugin(p, "user"))

        # Project plugins (.hermes/plugins/)
        if project_dir:
            proj_plugins = os.path.join(project_dir, ".hermes", "plugins")
            if os.path.isdir(proj_plugins):
                for p in Path(proj_plugins).iterdir():
                    if p.is_dir() and (p / "plugin.json").exists():
                        found.append(self._load_plugin(p, "project"))

        # Local plugins
        if os.path.isdir(PLUGINS_DIR):
            for p in Path(PLUGINS_DIR).iterdir():
                if p.is_dir() and (p / "plugin.json").exists():
                    found.append(self._load_plugin(p, "local"))

        return [f for f in found if f]

    def _load_plugin(self, path: Path, source: str) -> Optional[dict]:
        try:
            meta = json.loads((path / "plugin.json").read_text())
            name = meta.get("name", path.name)
            entry = {
                "name": name,
                "version": meta.get("version", "0.1.0"),
                "description": meta.get("description", ""),
                "source": source,
                "path": str(path),
                "tools": meta.get("tools", []),
                "hooks": meta.get("hooks", []),
                "agents": meta.get("agents", []),
                "mcp_servers": meta.get("mcp_servers", []),
                "enabled": True,
                "loaded": False,
                "module": None,
            }
            self._plugins[name] = entry
            return entry
        except Exception as e:
            logger.warning(f"Failed to load plugin from {path}: {e}")
            return None

    def _activate_plugin_bindings(self, plugin: dict, mod: Any = None):
        if mod is not None and hasattr(mod, "HOOKS") and plugin.get("hooks") == []:
            plugin["hooks"] = mod.HOOKS

        if mod is not None and hasattr(mod, "TOOLS"):
            raw_tools = list(getattr(mod, "TOOLS", []) or [])
            if raw_tools:
                plugin["tools"] = [t.name if hasattr(t, "name") else str(t) for t in raw_tools]
                try:
                    from app.agents.evolution import tool_registry
                    for tool_obj in raw_tools:
                        tool_name = getattr(tool_obj, "name", "")
                        if not tool_name:
                            continue
                        tool_registry._custom_tools[tool_name] = tool_obj
                        tool_registry._tool_metadata[tool_name] = {
                            "name": tool_name,
                            "description": getattr(tool_obj, "description", "") or f"Plugin tool from {plugin['name']}",
                            "code": "",
                            "created_by": f"plugin:{plugin['name']}",
                        }
                except Exception as e:
                    logger.warning(f"Failed to register plugin tools for '{plugin['name']}': {e}")

        if plugin.get("hooks"):
            try:
                from app.agents.hooks import hooks_registry
                hooks_registry.register_from_skill(plugin["name"], plugin["hooks"])
            except Exception as e:
                logger.warning(f"Failed to register plugin hooks for '{plugin['name']}': {e}")

        if mod is not None and hasattr(mod, "AGENTS") and plugin.get("agents") == []:
            plugin["agents"] = list(getattr(mod, "AGENTS", []) or [])

        if plugin.get("agents"):
            try:
                from app.agents.subagents import SubagentConfig, subagent_manager
                for agent in plugin["agents"]:
                    if not isinstance(agent, dict):
                        continue
                    agent_payload = dict(agent)
                    agent_name = str(agent_payload.pop("name", "")).strip()
                    if not agent_name:
                        continue
                    agent_payload.setdefault("source", "plugin")
                    subagent_manager._agents[agent_name] = SubagentConfig(name=agent_name, **agent_payload)
            except Exception as e:
                logger.warning(f"Failed to register plugin agents for '{plugin['name']}': {e}")

        if mod is not None and hasattr(mod, "MCP_SERVERS") and plugin.get("mcp_servers") == []:
            plugin["mcp_servers"] = list(getattr(mod, "MCP_SERVERS", []) or [])

        if plugin.get("mcp_servers"):
            try:
                from app.skills.mcp import MCPServerConfig, mcp_registry
                for server in plugin["mcp_servers"]:
                    if not isinstance(server, dict):
                        continue
                    try:
                        mcp_registry.register(MCPServerConfig(**server))
                    except Exception as e:
                        logger.warning(f"Failed to register MCP server from plugin '{plugin['name']}': {e}")
            except Exception as e:
                logger.warning(f"Failed to activate plugin MCP servers for '{plugin['name']}': {e}")

    def load_plugin(self, name: str) -> tuple[bool, str]:
        """Actually load and execute plugin Python code via importlib."""
        plugin = self._plugins.get(name)
        if not plugin:
            return False, f"Plugin '{name}' not found"
        if plugin.get("loaded"):
            return True, f"Plugin '{name}' already loaded"
        if not plugin.get("enabled"):
            return False, f"Plugin '{name}' is disabled"

        plugin_path = Path(plugin["path"])
        init_file = plugin_path / "__init__.py"
        main_file = plugin_path / "main.py"
        entry = init_file if init_file.exists() else main_file if main_file.exists() else None

        if not entry:
            self._activate_plugin_bindings(plugin)
            plugin["loaded"] = True
            return True, f"Plugin '{name}' registered (no Python entry point)"

        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(f"hermes_plugin_{name}", str(entry))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            plugin["module"] = mod
            self._activate_plugin_bindings(plugin, mod)
            plugin["loaded"] = True

            return True, f"Plugin '{name}' loaded from {entry.name}"
        except Exception as e:
            logger.warning(f"Failed to load plugin code for '{name}': {e}")
            return False, f"Plugin '{name}' load error: {e}"

    def load_all(self) -> list[str]:
        """Load all enabled plugins."""
        loaded = []
        for name, plugin in self._plugins.items():
            if plugin.get("enabled") and not plugin.get("loaded"):
                ok, msg = self.load_plugin(name)
                if ok:
                    loaded.append(name)
        return loaded

    def discover_pip_plugins(self) -> list[dict]:
        """Discover plugins installed via pip entry points (hermes_plugins group)."""
        found = []
        try:
            from importlib.metadata import entry_points
            # Modern API (Python 3.10+): entry_points(group=...)
            try:
                hermes_eps = list(entry_points(group="hermes_plugins"))
            except TypeError:
                # Fallback for older Python
                eps = entry_points()
                hermes_eps = eps.get("hermes_plugins", []) if isinstance(eps, dict) else [
                    ep for ep in eps if getattr(ep, "group", None) == "hermes_plugins"
                ]
            for ep in hermes_eps:
                try:
                    mod = ep.load()
                    name = ep.name
                    self._plugins[name] = {
                        "name": name,
                        "version": getattr(mod, "__version__", "0.1.0"),
                        "description": getattr(mod, "__doc__", "") or "",
                        "source": "pip",
                        "path": "",
                        "tools": getattr(mod, "TOOLS", []),
                        "hooks": getattr(mod, "HOOKS", []),
                        "agents": getattr(mod, "AGENTS", []),
                        "mcp_servers": getattr(mod, "MCP_SERVERS", []),
                        "enabled": True,
                        "loaded": True,
                        "module": mod,
                    }
                    self._activate_plugin_bindings(self._plugins[name], mod)
                    found.append(self._plugins[name])
                except Exception as e:
                    logger.warning(f"Failed to load pip plugin '{ep.name}': {e}")
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
        return found

    def enable(self, name: str) -> tuple[bool, str]:
        if name not in self._plugins:
            return False, f"Plugin '{name}' not found"
        self._plugins[name]["enabled"] = True
        return True, f"Plugin '{name}' enabled"

    def disable(self, name: str) -> tuple[bool, str]:
        if name not in self._plugins:
            return False, f"Plugin '{name}' not found"
        self._plugins[name]["enabled"] = False
        return True, f"Plugin '{name}' disabled"

    def list_plugins(self) -> list[dict]:
        return [{k: v for k, v in p.items() if k != "module"} for p in self._plugins.values()]

    def get_plugin(self, name: str) -> Optional[dict]:
        return self._plugins.get(name)


# ---------------------------------------------------------------------------
# 8. SKILL.md YAML Frontmatter Parser
# ---------------------------------------------------------------------------
import re as _re

FRONTMATTER_PATTERN = _re.compile(r'^---\s*\n(.*?)\n---\s*\n(.*)$', _re.DOTALL)


def parse_skill_md(content: str) -> dict:
    """Parse a SKILL.md file with YAML frontmatter (Claude Code / Hermes format)."""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {"system_prompt": content, "frontmatter": {}}

    frontmatter_text = match.group(1)
    body = match.group(2)

    # Simple YAML parser (avoid dependency)
    frontmatter = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Handle lists
            if value == "":
                continue
            # Handle booleans
            if value.lower() in ("true", "yes"):
                value = True
            elif value.lower() in ("false", "no"):
                value = False
            frontmatter[key] = value

    # Parse list items (lines starting with -)
    current_key = None
    for line in frontmatter_text.split("\n"):
        stripped = line.strip()
        if ":" in stripped and not stripped.startswith("-"):
            current_key = stripped.split(":")[0].strip()
        elif stripped.startswith("- ") and current_key:
            if current_key not in frontmatter:
                frontmatter[current_key] = []
            elif not isinstance(frontmatter[current_key], list):
                frontmatter[current_key] = []
            frontmatter[current_key].append(stripped[2:].strip())

    return {
        "system_prompt": body.strip(),
        "frontmatter": frontmatter,
        "name": frontmatter.get("name", ""),
        "description": frontmatter.get("description", ""),
        "allowed_tools": frontmatter.get("allowed-tools", frontmatter.get("tools")),
        "disable_model_invocation": frontmatter.get("disable-model-invocation", False),
        "context": frontmatter.get("context"),
        "model": frontmatter.get("model"),
        "effort": frontmatter.get("effort"),
        "paths": frontmatter.get("paths"),
        "shell": frontmatter.get("shell"),
        "hooks": frontmatter.get("hooks"),
        "user_invocable": frontmatter.get("user-invocable", True),
        "argument_hint": frontmatter.get("argument-hint"),
        "arguments": frontmatter.get("arguments"),
    }


def render_skill_md(skill_data: dict) -> str:
    """Render a skill dict back to SKILL.md format."""
    fm_lines = ["---"]
    for key in ["name", "description"]:
        if skill_data.get(key):
            fm_lines.append(f"{key}: {skill_data[key]}")
    if skill_data.get("disable_model_invocation"):
        fm_lines.append("disable-model-invocation: true")
    if skill_data.get("context"):
        fm_lines.append(f"context: {skill_data['context']}")
    if skill_data.get("model"):
        fm_lines.append(f"model: {skill_data['model']}")
    if skill_data.get("effort"):
        fm_lines.append(f"effort: {skill_data['effort']}")
    if skill_data.get("allowed_tools"):
        tools = skill_data["allowed_tools"]
        if isinstance(tools, list):
            fm_lines.append("allowed-tools: " + " ".join(tools))
        else:
            fm_lines.append(f"allowed-tools: {tools}")
    fm_lines.append("---")
    return "\n".join(fm_lines) + "\n" + skill_data.get("system_prompt", "")


# ---------------------------------------------------------------------------
# 9. Secure Setup on Load
# ---------------------------------------------------------------------------
@dataclass
class RequiredEnvVar:
    name: str
    prompt: str = ""
    help: str = ""
    required_for: str = "full functionality"


def check_skill_env_requirements(skill: dict) -> list[dict]:
    """Check if a skill's required environment variables are set."""
    reqs = skill.get("required_environment_variables", [])
    results = []
    for req in reqs:
        if isinstance(req, dict):
            name = req.get("name", "")
            is_set = bool(os.environ.get(name))
            results.append({
                "name": name,
                "is_set": is_set,
                "prompt": req.get("prompt", f"Set {name}"),
                "help": req.get("help", ""),
                "required_for": req.get("required_for", "full functionality"),
            })
    return results


# ---------------------------------------------------------------------------
# 10. External Skill Directories
# ---------------------------------------------------------------------------
def scan_external_skill_dirs(config_dirs: list[str]) -> list[dict]:
    """Scan external skill directories for SKILL.md files."""
    found = []
    for dir_path in config_dirs:
        expanded = os.path.expanduser(os.path.expandvars(dir_path))
        if not os.path.isdir(expanded):
            continue
        for skill_dir in Path(expanded).iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    parsed = parse_skill_md(skill_md.read_text())
                    parsed["source_dir"] = str(skill_dir)
                    parsed["external"] = True
                    if not parsed.get("name"):
                        parsed["name"] = skill_dir.name
                    found.append(parsed)
    return found


# ---------------------------------------------------------------------------
# 11. SOUL.md Personality System
# ---------------------------------------------------------------------------
SOUL_DIR = os.path.join(DATA_DIR, "..", "config")


def load_soul(soul_path: str = None) -> str:
    """Load SOUL.md personality file (Hermes pattern)."""
    paths_to_check = [
        soul_path,
        os.path.join(SOUL_DIR, "SOUL.md"),
        os.path.expanduser("~/.hermes/SOUL.md"),
    ]
    for p in paths_to_check:
        if p and os.path.exists(p):
            return Path(p).read_text()
    return ""


def save_soul(content: str, soul_path: str = None) -> tuple[bool, str]:
    """Save SOUL.md personality file."""
    path = soul_path or os.path.join(SOUL_DIR, "SOUL.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(content)
    return True, f"SOUL.md saved to {path}"


# ---------------------------------------------------------------------------
# 12. Prompt Cache Injection (Anthropic pattern)
# ---------------------------------------------------------------------------
CACHE_BREAKPOINT = {"type": "ephemeral"}


def inject_cache_breakpoints(system_prompt: str, sections: list[str] = None) -> list[dict]:
    """Inject Anthropic-style cache breakpoints into system prompt sections.
    Returns list of content blocks with cache_control markers."""
    if not sections:
        # Auto-detect sections by ## headers
        parts = system_prompt.split("\n## ")
        sections = [parts[0]] + [f"## {p}" for p in parts[1:]] if len(parts) > 1 else [system_prompt]

    blocks = []
    for i, section in enumerate(sections):
        block = {"type": "text", "text": section}
        # Add cache breakpoint after stable sections (personality, skills, memory)
        if i < len(sections) - 1:
            block["cache_control"] = CACHE_BREAKPOINT
        blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# 13. Cron / Scheduled Automations
# ---------------------------------------------------------------------------
CRON_DIR = os.path.join(DATA_DIR, "cron")
os.makedirs(CRON_DIR, exist_ok=True)


@dataclass
class CronJob:
    name: str
    schedule: str           # cron expression or human-readable
    action: str             # skill name or command
    action_type: str = "skill"  # "skill", "command", "prompt"
    arguments: str = ""
    enabled: bool = True
    last_run: str = ""
    next_run: str = ""
    delivery: str = "log"   # "log", "webhook", "email"
    delivery_target: str = ""
    delivery_headers: dict[str, str] = field(default_factory=dict)
    delivery_subject: str = ""
    created_at: str = ""


class CronManager:
    """Manage scheduled agent tasks (Hermes cron pattern)."""

    def __init__(self):
        self._jobs: dict[str, CronJob] = {}
        self._scheduler_running = False
        self._scheduler_thread = None
        self._load()

    def _load(self):
        path = os.path.join(CRON_DIR, "jobs.json")
        if os.path.exists(path):
            try:
                data = json.loads(Path(path).read_text())
                for j in data:
                    job = CronJob(**j)
                    self._jobs[job.name] = job
            except Exception as e:
                logger.debug("Suppressed error in self_evolution: %s", e)

    def _save(self):
        path = os.path.join(CRON_DIR, "jobs.json")
        Path(path).write_text(json.dumps(
            [asdict(j) for j in self._jobs.values()],
            ensure_ascii=False, indent=2
        ))

    def _normalize_schedule(self, schedule: str) -> str:
        raw = (schedule or "").strip()
        if not raw:
            return ""
        if len(raw.split()) == 5:
            return raw
        lowered = raw.lower()
        day_map = {
            "sun": 0, "sunday": 0,
            "mon": 1, "monday": 1,
            "tue": 2, "tuesday": 2,
            "wed": 3, "wednesday": 3,
            "thu": 4, "thursday": 4,
            "fri": 5, "friday": 5,
            "sat": 6, "saturday": 6,
        }
        if lowered == "hourly":
            return "0 * * * *"
        if lowered == "daily":
            return "0 0 * * *"
        if lowered == "weekly":
            return "0 0 * * 0"
        match = _re.match(r"every\s+(\d+)\s+minutes?", lowered)
        if match:
            return f"*/{int(match.group(1))} * * * *"
        match = _re.match(r"every\s+(\d+)\s+hours?", lowered)
        if match:
            return f"0 */{int(match.group(1))} * * *"
        match = _re.match(r"(?:daily|every day)\s+at\s+(\d{1,2}):(\d{2})", lowered)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            return f"{minute} {hour} * * *"
        match = _re.match(r"weekdays\s+at\s+(\d{1,2}):(\d{2})", lowered)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            return f"{minute} {hour} * * 1-5"
        match = _re.match(r"weekly\s+on\s+([a-z]+)\s+at\s+(\d{1,2}):(\d{2})", lowered)
        if match:
            dow = day_map.get(match.group(1))
            if dow is None:
                return ""
            hour = int(match.group(2))
            minute = int(match.group(3))
            return f"{minute} {hour} * * {dow}"
        return ""

    def _cron_matches_time(self, schedule: str, current: datetime) -> bool:
        try:
            normalized = self._normalize_schedule(schedule)
            parts = normalized.strip().split()
            if len(parts) != 5:
                return False
            fields = [current.minute, current.hour, current.day, current.month, current.weekday()]
            fields[4] = (fields[4] + 1) % 7
            for field_val, pattern in zip(fields, parts):
                if pattern == "*":
                    continue
                if "/" in pattern:
                    _base, step = pattern.split("/", 1)
                    if field_val % int(step) != 0:
                        return False
                elif "," in pattern:
                    if field_val not in [int(x) for x in pattern.split(",")]:
                        return False
                elif "-" in pattern:
                    lo, hi = pattern.split("-", 1)
                    if not (int(lo) <= field_val <= int(hi)):
                        return False
                else:
                    if field_val != int(pattern):
                        return False
            return True
        except Exception as e:
            logger.debug("Suppressed error in self_evolution: %s", e)
            return False

    def _compute_next_run(self, schedule: str) -> str:
        normalized = self._normalize_schedule(schedule)
        if not normalized:
            return ""
        cursor = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(60 * 24 * 14):
            if self._cron_matches_time(normalized, cursor):
                return cursor.isoformat()
            cursor += timedelta(minutes=1)
        return ""

    def _deliver_result(self, job: CronJob, result: dict) -> dict:
        mode = (job.delivery or "log").lower()
        if mode == "log":
            logger.info("Cron delivery for '%s': %s", job.name, result.get("status"))
            return {"status": "logged"}
        if mode == "webhook":
            if not job.delivery_target:
                return {"status": "skipped", "reason": "Missing delivery_target"}
            try:
                import httpx
                response = httpx.post(
                    job.delivery_target,
                    json={"job": asdict(job), "result": result},
                    headers=job.delivery_headers or {},
                    timeout=15,
                )
                if response.status_code >= 400:
                    return {"status": "error", "code": response.status_code, "reason": response.text[:200]}
                return {"status": "sent", "code": response.status_code}
            except Exception as e:
                return {"status": "error", "reason": str(e)}
        if mode == "email":
            host = os.environ.get("HERMES_SMTP_HOST", "")
            sender = os.environ.get("HERMES_SMTP_FROM", "")
            if not host or not sender or not job.delivery_target:
                return {"status": "skipped", "reason": "SMTP not configured"}
            try:
                import smtplib
                from email.message import EmailMessage

                port = int(os.environ.get("HERMES_SMTP_PORT", "587"))
                username = os.environ.get("HERMES_SMTP_USERNAME", "")
                password = os.environ.get("HERMES_SMTP_PASSWORD", "")
                message = EmailMessage()
                message["From"] = sender
                message["To"] = job.delivery_target
                message["Subject"] = job.delivery_subject or f"Cron job {job.name}: {result.get('status', 'completed')}"
                message.set_content(json.dumps(result, ensure_ascii=False, indent=2))

                if port == 465:
                    with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                        if username:
                            smtp.login(username, password)
                        smtp.send_message(message)
                else:
                    with smtplib.SMTP(host, port, timeout=15) as smtp:
                        smtp.ehlo()
                        smtp.starttls()
                        smtp.ehlo()
                        if username:
                            smtp.login(username, password)
                        smtp.send_message(message)
                return {"status": "sent", "target": job.delivery_target}
            except Exception as e:
                return {"status": "error", "reason": str(e)}
        return {"status": "skipped", "reason": f"Unsupported delivery mode: {job.delivery}"}

    def add_job(self, name: str, schedule: str, action: str, **kwargs) -> tuple[bool, str]:
        if name in self._jobs:
            return False, f"Job '{name}' already exists"
        if not self._normalize_schedule(schedule):
            return False, f"Invalid schedule: {schedule}"
        # Drop unknown kwargs so callers can pass extra payload fields safely
        valid_fields = {f.name for f in fields(CronJob)}
        clean = {k: v for k, v in kwargs.items() if k in valid_fields}
        self._jobs[name] = CronJob(
            name=name, schedule=schedule, action=action,
            created_at=datetime.now().isoformat(), next_run=self._compute_next_run(schedule), **clean,
        )
        self._save()
        return True, f"Cron job '{name}' created"

    def remove_job(self, name: str) -> tuple[bool, str]:
        if name not in self._jobs:
            return False, f"Job '{name}' not found"
        del self._jobs[name]
        self._save()
        return True, f"Job '{name}' removed"

    def enable_job(self, name: str) -> tuple[bool, str]:
        if name not in self._jobs:
            return False, f"Job '{name}' not found"
        self._jobs[name].enabled = True
        self._jobs[name].next_run = self._compute_next_run(self._jobs[name].schedule)
        self._save()
        return True, f"Job '{name}' enabled"

    def disable_job(self, name: str) -> tuple[bool, str]:
        if name not in self._jobs:
            return False, f"Job '{name}' not found"
        self._jobs[name].enabled = False
        self._jobs[name].next_run = ""
        self._save()
        return True, f"Job '{name}' disabled"

    def list_jobs(self) -> list[dict]:
        return [asdict(j) for j in self._jobs.values()]

    def get_job(self, name: str) -> Optional[dict]:
        j = self._jobs.get(name)
        return asdict(j) if j else None

    async def run_job(self, name: str) -> dict:
        """Manually trigger a cron job."""
        job = self._jobs.get(name)
        if not job:
            return {"error": f"Job '{name}' not found"}

        result = {"job": name, "timestamp": datetime.now().isoformat()}
        try:
            if job.action_type == "skill":
                from app.agents.evolution import skill_registry
                skill = skill_registry.get_skill(job.action)
                if skill:
                    result["output"] = f"Skill '{job.action}' would be executed"
                    result["status"] = "success"
                else:
                    result["status"] = "error"
                    result["error"] = f"Skill '{job.action}' not found"
            elif job.action_type == "command":
                import subprocess, shlex
                proc = subprocess.run(
                    shlex.split(job.action), shell=False, capture_output=True,
                    text=True, timeout=60,
                )
                result["output"] = proc.stdout[:2000]
                result["status"] = "success" if proc.returncode == 0 else "error"
            elif job.action_type == "evolution":
                result.update(self._run_auto_evolution(job.action))
            else:
                result["output"] = f"Action type '{job.action_type}' not yet supported"
                result["status"] = "skipped"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

        job.last_run = datetime.now().isoformat()
        job.next_run = self._compute_next_run(job.schedule) if job.enabled else ""
        result["delivery"] = self._deliver_result(job, result)
        self._save()
        return result

    @staticmethod
    def _run_auto_evolution(action: str) -> dict:
        """Run auto-triage and evolve underperforming skills.

        action can be 'auto_triage' (find + evolve worst skills)
        or a specific skill name to evolve.
        """
        try:
            # Use module-level singleton
            ctrl = evolution_controller
            if action == "auto_triage":
                triage = ctrl.auto_triage()
                if not triage:
                    return {"status": "success", "output": "No skills need evolution (all above 70% success rate).", "evolved": []}
                evolved = []
                for candidate in triage[:3]:  # evolve top 3 worst skills
                    skill_name = candidate["skill_name"]
                    from app.agents.evolution import skill_registry
                    skill = skill_registry.get_skill(skill_name)
                    if skill is None:
                        continue
                    content = skill.get("system_prompt", "")
                    if not content:
                        continue
                    result = ctrl.evolve_skill(skill_name, content, iterations=3)
                    evolved.append({
                        "skill": skill_name,
                        "improvement": result.get("improvement", 0),
                        "status": result.get("status", "unknown"),
                    })
                return {
                    "status": "success",
                    "output": f"Triaged {len(triage)} skills, evolved {len(evolved)}.",
                    "triage": triage,
                    "evolved": evolved,
                }
            else:
                # Evolve specific skill
                from app.agents.evolution import skill_registry
                skill = skill_registry.get_skill(action)
                if skill is None:
                    return {"status": "error", "error": f"Skill '{action}' not found"}
                content = skill.get("system_prompt", "")
                result = ctrl.evolve_skill(action, content, iterations=5)
                return {
                    "status": "success",
                    "output": f"Evolved '{action}': improvement={result.get('improvement', 0):+.4f}",
                    "evolution_result": result,
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # --- Background Scheduler ---
    def _cron_matches_now(self, schedule: str) -> bool:
        """Check if a cron expression matches the current minute.
        Supports: '* * * * *' (min hour dom month dow), simple ints, and '*'"""
        return self._cron_matches_time(schedule, datetime.now())

    def start_scheduler(self, poll_interval: int = 60, dedupe_window: int = 90):
        """Start background thread that checks jobs every `poll_interval` seconds.
        `dedupe_window` (seconds) prevents same job from running twice within the window."""
        import threading

        def _scheduler_loop():
            import time as _time
            import asyncio
            loop = asyncio.new_event_loop()
            logger.info(f"Cron scheduler started (poll={poll_interval}s)")
            try:
                while self._scheduler_running:
                    for name, job in list(self._jobs.items()):
                        if not job.enabled:
                            continue
                        if self._cron_matches_now(job.schedule):
                            if job.last_run:
                                try:
                                    last = datetime.fromisoformat(job.last_run)
                                    if (datetime.now() - last).total_seconds() < dedupe_window:
                                        continue
                                except ValueError:
                                    pass
                            try:
                                result = loop.run_until_complete(self.run_job(name))
                                logger.info(f"Cron job '{name}' executed: {result.get('status')}")
                            except Exception as e:
                                logger.warning(f"Cron job '{name}' failed: {e}")
                    _time.sleep(poll_interval)
            finally:
                loop.close()

        self._scheduler_running = True
        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True, name="cron-scheduler"
        )
        self._scheduler_thread.start()
        return True

    def stop_scheduler(self):
        """Stop the background scheduler thread."""
        self._scheduler_running = False
        return True


# ---------------------------------------------------------------------------
# 14. Elicitation (structured user input)
# ---------------------------------------------------------------------------
@dataclass
class ElicitationRequest:
    """Request for structured user input (Claude Code pattern)."""
    elicitation_id: str
    title: str
    description: str = ""
    fields: list[dict] = field(default_factory=list)
    # Each field: {"name": str, "type": "text"|"select"|"boolean", "label": str, "options": [...]}
    required: bool = True
    timeout_seconds: int = 300


class ElicitationManager:
    """Manage structured input requests from agent to user."""

    def __init__(self):
        self._pending: dict[str, ElicitationRequest] = {}
        self._results: dict[str, dict] = {}

    def create_request(self, title: str, fields: list[dict],
                       description: str = "") -> ElicitationRequest:
        elicit_id = hashlib.md5(f"{title}:{datetime.now().isoformat()}".encode()).hexdigest()[:10]
        req = ElicitationRequest(
            elicitation_id=elicit_id,
            title=title,
            description=description,
            fields=fields,
        )
        self._pending[elicit_id] = req
        return req

    def submit_result(self, elicitation_id: str, values: dict) -> tuple[bool, str]:
        if elicitation_id not in self._pending:
            return False, f"Elicitation '{elicitation_id}' not found"
        self._results[elicitation_id] = {
            "elicitation_id": elicitation_id,
            "values": values,
            "submitted_at": datetime.now().isoformat(),
        }
        del self._pending[elicitation_id]
        return True, "Result submitted"

    def get_result(self, elicitation_id: str) -> Optional[dict]:
        return self._results.get(elicitation_id)

    def get_pending(self) -> list[dict]:
        return [asdict(r) for r in self._pending.values()]


# ---------------------------------------------------------------------------
# 15. execute_code — Programmatic Tool Calling
# ---------------------------------------------------------------------------
async def execute_code(code: str, language: str = "python",
                       timeout: int = 30) -> dict:
    """Execute code in a sandboxed environment (Hermes execute_code pattern).
    Collapses multi-step tool pipelines into single inference calls."""
    import subprocess
    result = {"language": language, "status": "success", "output": "", "error": ""}
    try:
        if language == "python":
            proc = subprocess.run(
                ["python", "-c", code],
                capture_output=True, text=True, timeout=timeout,
            )
        elif language == "bash":
            proc = subprocess.run(
                ["bash", "-c", code], shell=False, capture_output=True, text=True, timeout=timeout,
            )
        else:
            return {"status": "error", "error": f"Unsupported language: {language}"}

        result["output"] = proc.stdout[:5000]
        result["error"] = proc.stderr[:2000]
        result["return_code"] = proc.returncode
        if proc.returncode != 0:
            result["status"] = "error"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["error"] = f"Execution timed out after {timeout}s"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


# Singletons
evolution_controller = EvolutionController()
gepa_engine = GEPAEngine()
plugin_registry = PluginRegistry()
cron_manager = CronManager()
elicitation_manager = ElicitationManager()

# Seed built-in auto-evolution cron job (daily at 03:00)
if "_auto_evolve" not in cron_manager._jobs:
    cron_manager.add_job(
        "_auto_evolve",
        "daily at 03:00",
        "auto_triage",
        action_type="evolution",
    )
