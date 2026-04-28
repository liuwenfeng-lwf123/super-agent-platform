"""
Tool Pipeline Registry — predefined tool chains that reduce LLM round-trips.

Pipelines are reusable sequences of tool calls with data flow between steps.
Instead of the LLM reasoning through each step individually (N inference calls),
a pipeline executes the whole chain in a single tool call.
"""
import asyncio
import json
import logging
import time
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PipelineStep:
    """A single step in a pipeline."""
    tool_name: str
    description: str = ""
    input_template: dict[str, str] = field(default_factory=dict)
    # References to previous step outputs: {"param": "$step_N.field"}
    output_key: str = ""  # key to extract from result
    condition: str = ""  # skip if condition evaluates to False
    timeout: int = 30


@dataclass
class Pipeline:
    """A predefined sequence of tool calls."""
    name: str
    description: str
    steps: list[PipelineStep] = field(default_factory=list)
    created_by: str = "system"  # system or user
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [
                {
                    "tool_name": s.tool_name,
                    "description": s.description,
                    "input_template": s.input_template,
                    "output_key": s.output_key,
                    "condition": s.condition,
                    "timeout": s.timeout,
                }
                for s in self.steps
            ],
            "created_by": self.created_by,
            "tags": self.tags,
        }


@dataclass
class PipelineResult:
    """Result of executing a pipeline."""
    pipeline_name: str
    status: str  # completed, partial, failed
    steps_completed: int = 0
    steps_total: int = 0
    outputs: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    final_result: str = ""


class PipelineRegistry:
    """Manages and executes tool pipelines."""

    def __init__(self):
        self._pipelines: dict[str, Pipeline] = {}
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in pipelines."""
        self.register(Pipeline(
            name="research_and_summarize",
            description="Search the web, fetch top results, and produce a summary",
            steps=[
                PipelineStep(
                    tool_name="web_search",
                    description="Search for information",
                    input_template={"query": "$input.query"},
                    output_key="results",
                ),
                PipelineStep(
                    tool_name="web_fetch",
                    description="Fetch the top result page",
                    input_template={"url": "$step_0.results[0].url"},
                    output_key="content",
                    condition="len($step_0.results) > 0",
                ),
            ],
            tags=["research", "web"],
        ))

        self.register(Pipeline(
            name="code_and_test",
            description="Write code to a file and immediately test it",
            steps=[
                PipelineStep(
                    tool_name="write_file",
                    description="Write the code",
                    input_template={"path": "$input.path", "content": "$input.code"},
                ),
                PipelineStep(
                    tool_name="execute_bash",
                    description="Run tests",
                    input_template={"command": "$input.test_command"},
                    output_key="test_output",
                ),
            ],
            tags=["code", "test"],
        ))

        self.register(Pipeline(
            name="fetch_and_extract",
            description="Fetch a web page and extract specific data with Python",
            steps=[
                PipelineStep(
                    tool_name="web_fetch",
                    description="Fetch the page content",
                    input_template={"url": "$input.url"},
                    output_key="page_content",
                ),
                PipelineStep(
                    tool_name="execute_python",
                    description="Extract data from page content",
                    input_template={"code": "$input.extract_code"},
                    output_key="extracted",
                ),
            ],
            tags=["web", "data"],
        ))

        self.register(Pipeline(
            name="multi_search_compare",
            description="Search multiple queries and compare results",
            steps=[
                PipelineStep(
                    tool_name="web_search",
                    description="First search",
                    input_template={"query": "$input.query_1"},
                    output_key="results_1",
                ),
                PipelineStep(
                    tool_name="web_search",
                    description="Second search",
                    input_template={"query": "$input.query_2"},
                    output_key="results_2",
                ),
            ],
            tags=["research", "compare"],
        ))

        self.register(Pipeline(
            name="read_analyze_write",
            description="Read a file, analyze with Python, write results",
            steps=[
                PipelineStep(
                    tool_name="read_file",
                    description="Read source file",
                    input_template={"path": "$input.source_path"},
                    output_key="file_content",
                ),
                PipelineStep(
                    tool_name="execute_python",
                    description="Analyze content",
                    input_template={"code": "$input.analysis_code"},
                    output_key="analysis",
                ),
                PipelineStep(
                    tool_name="write_file",
                    description="Write results",
                    input_template={"path": "$input.output_path", "content": "$step_1.analysis"},
                    condition="$step_1.analysis",
                ),
            ],
            tags=["code", "analysis"],
        ))

    def register(self, pipeline: Pipeline):
        """Register a new pipeline."""
        self._pipelines[pipeline.name] = pipeline

    def get(self, name: str) -> Optional[Pipeline]:
        return self._pipelines.get(name)

    def list_pipelines(self, tag: str | None = None) -> list[dict]:
        """List all available pipelines, optionally filtered by tag."""
        pipelines = self._pipelines.values()
        if tag:
            pipelines = [p for p in pipelines if tag in p.tags]
        return [p.to_dict() for p in pipelines]

    def remove(self, name: str) -> bool:
        """Remove a user-created pipeline."""
        p = self._pipelines.get(name)
        if not p:
            return False
        if p.created_by == "system":
            return False
        del self._pipelines[name]
        return True

    async def execute(
        self,
        pipeline_name: str,
        inputs: dict[str, Any],
    ) -> PipelineResult:
        """Execute a pipeline with the given inputs."""
        pipeline = self._pipelines.get(pipeline_name)
        if not pipeline:
            return PipelineResult(
                pipeline_name=pipeline_name,
                status="failed",
                errors=[f"Pipeline '{pipeline_name}' not found"],
            )

        from app.agents.tools import get_tool_by_name

        result = PipelineResult(
            pipeline_name=pipeline_name,
            steps_total=len(pipeline.steps),
        )
        step_outputs: dict[str, Any] = {}
        start_time = time.time()

        for i, step in enumerate(pipeline.steps):
            step_key = f"step_{i}"

            # Check condition
            if step.condition:
                try:
                    cond_result = self._eval_condition(step.condition, inputs, step_outputs)
                    if not cond_result:
                        logger.debug("Pipeline %s step %d skipped (condition false)", pipeline_name, i)
                        continue
                except Exception as e:
                    logger.debug("Pipeline %s step %d condition error: %s", pipeline_name, i, e)
                    continue

            # Resolve input template
            try:
                resolved_input = self._resolve_template(step.input_template, inputs, step_outputs)
            except Exception as e:
                result.errors.append(f"Step {i} ({step.tool_name}): input resolution failed: {e}")
                result.status = "partial"
                break

            # Execute tool
            tool_obj = get_tool_by_name(step.tool_name, include_deferred=True, wrap=True)
            if not tool_obj:
                result.errors.append(f"Step {i}: tool '{step.tool_name}' not found")
                result.status = "partial"
                break

            try:
                step_result = await asyncio.wait_for(
                    tool_obj.ainvoke(resolved_input),
                    timeout=step.timeout,
                )
                # Store output
                if step.output_key:
                    step_outputs[step_key] = {step.output_key: step_result}
                else:
                    step_outputs[step_key] = {"result": step_result}
                result.steps_completed += 1
                result.outputs[step_key] = str(step_result)[:2000]
            except asyncio.TimeoutError:
                result.errors.append(f"Step {i} ({step.tool_name}): timeout after {step.timeout}s")
                result.status = "partial"
                break
            except Exception as e:
                result.errors.append(f"Step {i} ({step.tool_name}): {e}")
                result.status = "partial"
                break

        result.elapsed_seconds = time.time() - start_time

        if not result.errors:
            result.status = "completed"

        # Build final result from last step
        if step_outputs:
            last_key = f"step_{result.steps_completed - 1}"
            last_output = step_outputs.get(last_key, {})
            result.final_result = str(next(iter(last_output.values()), ""))[:3000]

        return result

    def _resolve_template(
        self, template: dict[str, str], inputs: dict, step_outputs: dict
    ) -> dict[str, Any]:
        """Resolve $input.X and $step_N.X references in templates."""
        resolved = {}
        for key, value_template in template.items():
            if isinstance(value_template, str) and value_template.startswith("$"):
                resolved[key] = self._resolve_ref(value_template, inputs, step_outputs)
            else:
                resolved[key] = value_template
        return resolved

    def _resolve_ref(self, ref: str, inputs: dict, step_outputs: dict) -> Any:
        """Resolve a single $reference."""
        if ref.startswith("$input."):
            field_name = ref[7:]
            return inputs.get(field_name, "")
        elif ref.startswith("$step_"):
            # Parse: $step_N.field or $step_N.field[index].subfield
            parts = ref[1:].split(".", 1)
            step_key = parts[0]  # step_N
            remainder = parts[1] if len(parts) > 1 else "result"
            step_data = step_outputs.get(step_key, {})

            # Handle array indexing: results[0].url
            if "[" in remainder:
                field = remainder.split("[")[0]
                arr = step_data.get(field, [])
                try:
                    idx_str = remainder.split("[")[1].split("]")[0]
                    idx = int(idx_str)
                    item = arr[idx] if isinstance(arr, list) and len(arr) > idx else arr
                    # Check for .subfield after ]
                    after_bracket = remainder.split("]", 1)[1]
                    if after_bracket.startswith("."):
                        subfield = after_bracket[1:]
                        return item.get(subfield, "") if isinstance(item, dict) else item
                    return item
                except (ValueError, IndexError, AttributeError):
                    return str(arr)
            else:
                return step_data.get(remainder, "")
        return ref

    def _eval_condition(self, condition: str, inputs: dict, step_outputs: dict) -> bool:
        """Evaluate a simple condition string."""
        # Replace references with values
        resolved = condition
        for ref_match in _find_refs(condition):
            value = self._resolve_ref(ref_match, inputs, step_outputs)
            resolved = resolved.replace(ref_match, repr(value))
        try:
            return bool(eval(resolved, {"__builtins__": {"len": len, "bool": bool}}, {}))
        except Exception:
            return True  # Default to True if condition can't be evaluated


def _find_refs(text: str) -> list[str]:
    """Extract $references from a condition string."""
    import re
    return re.findall(r'\$[\w.[\]]+', text)


# Singleton
pipeline_registry = PipelineRegistry()
