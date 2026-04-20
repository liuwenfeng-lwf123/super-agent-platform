import os
import json
import asyncio
from langchain_core.messages import HumanMessage, SystemMessage
from app.models.provider import llm_provider
from app.skills.search import web_search_tool
from app.sandbox.manager import sandbox_executor
from typing import AsyncGenerator


PLANNER_SYSTEM = """You are a task planner. Break down the user's task into independent sub-tasks that can be executed in parallel.
Each sub-task should be specific and actionable.

Respond ONLY in this JSON format, no other text:
{
  "needs_planning": true/false,
  "reasoning": "why",
  "steps": [
    {"id": 1, "task": "description", "skill": "research|code|report|webpage|search"},
    ...
  ]
}

If the task is simple enough for one response, set needs_planning to false."""


class SubAgent:
    def __init__(self, agent_id: str, task: str, skill: str, model: str | None = None):
        self.agent_id = agent_id
        self.task = task
        self.skill = skill
        self.model = model
        self.status = "pending"
        self.result = ""

    async def execute(self) -> dict:
        self.status = "running"
        try:
            if self.skill == "search":
                self.result = await web_search_tool.search_and_summarize(self.task)
            else:
                self.result = await self._run_with_llm()
            self.status = "completed"
        except Exception as e:
            self.result = f"Error: {str(e)}"
            self.status = "failed"
        return self.to_dict()

    async def _run_with_llm(self) -> str:
        skill_prompts = {
            "research": "You are a research specialist. Conduct thorough research on the given topic. Provide detailed findings with key insights.",
            "code": "You are a coding expert. Write clean, efficient, well-documented code for the given task.",
            "report": "You are a report writer. Create a well-structured, professional report in Markdown format.",
            "webpage": "You are a web developer. Create a modern, responsive web page with HTML/CSS/JS.",
        }
        system = skill_prompts.get(self.skill, "You are a helpful assistant. Complete the given task thoroughly.")

        chat_model = llm_provider.get_chat_model(self.model, streaming=False)
        response = await chat_model.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=self.task),
        ])
        return response.content if hasattr(response, "content") else str(response)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "task": self.task,
            "skill": self.skill,
            "status": self.status,
            "result": self.result[:2000],
        }


class MultiAgentOrchestrator:
    def __init__(self, max_parallel: int = 5):
        self.max_parallel = max_parallel

    async def plan(self, task: str, model: str | None = None) -> dict:
        chat_model = llm_provider.get_chat_model(model, streaming=False)
        response = await chat_model.ainvoke([
            SystemMessage(content=PLANNER_SYSTEM),
            HumanMessage(content=task),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        try:
            json_match = content
            if "{" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                json_match = content[start:end]
            return json.loads(json_match)
        except (json.JSONDecodeError, ValueError):
            return {"needs_planning": False, "reasoning": "Failed to parse plan", "steps": []}

    async def execute_parallel(
        self, steps: list[dict], model: str | None = None
    ) -> AsyncGenerator[dict, None]:
        agents = [
            SubAgent(
                agent_id=f"sub-{i+1}",
                task=step["task"],
                skill=step.get("skill", "research"),
                model=model,
            )
            for i, step in enumerate(steps)
        ]

        yield {
            "type": "plan",
            "data": {
                "total": len(agents),
                "steps": [{"id": a.agent_id, "task": a.task, "skill": a.skill} for a in agents],
            },
        }

        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_agent(agent: SubAgent) -> SubAgent:
            async with semaphore:
                yield_event = {"type": "agent_status", "data": {"agent_id": agent.agent_id, "status": "running"}}
                return (agent, yield_event)

        tasks = [self._run_with_status(a) for a in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                yield {"type": "agent_status", "data": {"agent_id": "unknown", "status": "failed", "result": str(r)}}
            else:
                agent, result = r
                yield result

        yield {
            "type": "agents_completed",
            "data": {
                "results": [a.to_dict() for a in agents],
                "summary": self._summarize(agents),
            },
        }

    async def _run_with_status(self, agent: SubAgent) -> tuple:
        return agent, await agent.execute()

    def _summarize(self, agents: list[SubAgent]) -> str:
        completed = [a for a in agents if a.status == "completed"]
        failed = [a for a in agents if a.status == "failed"]
        lines = []
        for a in completed:
            lines.append(f"**{a.agent_id}** ({a.skill}): {a.result[:500]}")
        for a in failed:
            lines.append(f"**{a.agent_id}** FAILED: {a.result[:200]}")
        return "\n\n".join(lines)


orchestrator = MultiAgentOrchestrator()
