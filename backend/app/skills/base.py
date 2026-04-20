from app.models.schemas import SkillConfig
from app.models.provider import llm_provider
from app.agents.tools import set_thread_context
from app.sandbox.manager import sandbox_executor
from app.skills.search import web_search_tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from app.agents.tools import ALL_TOOLS
import os
import json
from typing import AsyncGenerator


class SkillWorkflow:
    def __init__(
        self,
        name: str,
        display_name: str,
        description: str,
        system_prompt: str,
        steps: list[dict],
        tools: list[str] | None = None,
        output_filename: str | None = None,
    ):
        self._name = name
        self._display_name = display_name
        self._description = description
        self._system_prompt = system_prompt
        self._steps = steps
        self._tools = tools
        self._output_filename = output_filename

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def to_config(self) -> SkillConfig:
        return SkillConfig(
            name=self._name,
            display_name=self._display_name,
            description=self._description,
            system_prompt=self._system_prompt,
        )


DEEP_RESEARCH = SkillWorkflow(
    name="deep-research",
    display_name="Deep Research",
    description="Conduct deep research on a topic with multi-source analysis and generate a comprehensive report",
    system_prompt="""You are a deep research specialist. Your job is to conduct thorough, multi-source research on the given topic.

Research Process:
1. **Explore**: Search for the topic from multiple angles - overview, recent developments, key players, challenges
2. **Investigate**: Read and analyze the most relevant sources in detail
3. **Synthesize**: Combine findings into a structured, comprehensive report

Report Structure:
- Executive Summary
- Key Findings (with citations)
- Detailed Analysis
- Conclusions & Outlook
- References

Always write the final report to a file called 'research_report.md' using the write_file tool.
Respond in the same language as the user's question.""",
    steps=[
        {"name": "explore", "prompt": "Search for broad overview information about the topic"},
        {"name": "investigate", "prompt": "Deep dive into specific aspects and read key sources"},
        {"name": "synthesize", "prompt": "Create a comprehensive research report"},
    ],
    output_filename="research_report.md",
)


WEB_PAGE = SkillWorkflow(
    name="web-page",
    display_name="Web Page Creation",
    description="Create beautiful, responsive web pages and web applications",
    system_prompt="""You are a web development expert. Create modern, responsive web pages.

Development Process:
1. **Plan**: Understand requirements and plan the page structure
2. **Build**: Write complete HTML with inline CSS and JavaScript
3. **Polish**: Ensure responsive design, good UX, and visual appeal

Guidelines:
- Use modern CSS (flexbox, grid, custom properties)
- Make it responsive and mobile-friendly
- Add smooth animations and transitions
- Use a cohesive color scheme and typography
- Include interactive elements where appropriate

Always save the final HTML to a file (e.g., 'index.html') using the write_file tool.
The user can preview the file from the workspace panel.
Respond in the same language as the user's question.""",
    steps=[
        {"name": "plan", "prompt": "Plan the page structure and design"},
        {"name": "build", "prompt": "Write the complete HTML/CSS/JS code"},
        {"name": "polish", "prompt": "Review and improve the output"},
    ],
    output_filename="index.html",
)


REPORT_GENERATION = SkillWorkflow(
    name="report-generation",
    display_name="Report Generation",
    description="Generate professional structured reports and documents",
    system_prompt="""You are a report generation specialist. Create well-structured, professional reports.

Report Process:
1. **Gather**: Collect relevant information through search and analysis
2. **Structure**: Organize findings into a clear report structure
3. **Write**: Produce a polished, professional document

Report Guidelines:
- Use Markdown formatting
- Include: Title, Executive Summary, Sections with Headers, Conclusions
- Add data tables where relevant
- Cite sources
- Use bullet points for key findings

Always save the final report to a file (e.g., 'report.md') using the write_file tool.
Respond in the same language as the user's question.""",
    steps=[
        {"name": "gather", "prompt": "Collect and analyze information"},
        {"name": "structure", "prompt": "Organize into report structure"},
        {"name": "write", "prompt": "Write the polished report"},
    ],
    output_filename="report.md",
)


SLIDE_CREATION = SkillWorkflow(
    name="slide-creation",
    display_name="Slide Creation",
    description="Create beautiful presentation slides as HTML",
    system_prompt="""You are a presentation design expert. Create beautiful, professional slides as a single HTML file.

Slide Process:
1. **Outline**: Plan the slide structure and content
2. **Design**: Create visually appealing slides with HTML/CSS/JS
3. **Polish**: Ensure smooth transitions and consistent styling

Slide Guidelines:
- Use reveal.js or custom CSS for slide transitions
- Each slide should have a clear title and concise content
- Use color, icons, and layout to make slides visually appealing
- Include speaker notes where helpful
- Make it work in any modern browser

Create the presentation as a self-contained HTML file and save it using write_file (e.g., 'slides.html').
Respond in the same language as the user's question.""",
    steps=[
        {"name": "outline", "prompt": "Plan slide structure and content"},
        {"name": "design", "prompt": "Create the slide HTML/CSS/JS"},
        {"name": "polish", "prompt": "Add transitions and polish"},
    ],
    output_filename="slides.html",
)


DATA_ANALYSIS = SkillWorkflow(
    name="data-analysis",
    display_name="Data Analysis",
    description="Analyze data, create visualizations, and generate insights",
    system_prompt="""You are a data analysis expert. Analyze data, create visualizations, and generate insights.

Analysis Process:
1. **Load**: Read and understand the data
2. **Analyze**: Compute statistics, find patterns, identify trends
3. **Visualize**: Create charts and plots
4. **Report**: Summarize findings

Tools:
- Use execute_python with pandas, matplotlib, seaborn for analysis
- Install needed packages via execute_bash (pip install pandas matplotlib seaborn)
- Save visualizations as image files
- Write analysis report to a markdown file

Always save outputs to the workspace using write_file.
Respond in the same language as the user's question.""",
    steps=[
        {"name": "load", "prompt": "Load and explore the data"},
        {"name": "analyze", "prompt": "Run analysis and create visualizations"},
        {"name": "report", "prompt": "Write up findings"},
    ],
    output_filename="analysis_report.md",
)


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, SkillWorkflow] = {}
        self._register_builtin()

    def _register_builtin(self):
        for skill in [DEEP_RESEARCH, WEB_PAGE, REPORT_GENERATION, SLIDE_CREATION, DATA_ANALYSIS]:
            self._skills[skill.name] = skill

    def get(self, name: str) -> SkillWorkflow | None:
        return self._skills.get(name)

    def list_skills(self) -> list[SkillConfig]:
        return [skill.to_config() for skill in self._skills.values()]

    def register(self, skill: SkillWorkflow):
        self._skills[skill.name] = skill

    def unregister(self, name: str) -> bool:
        if name in self._skills:
            del self._skills[name]
            return True
        return False


skill_registry = SkillRegistry()


async def execute_skill(
    skill_name: str,
    task: str,
    model: str | None = None,
    thread_id: str = "_default",
) -> AsyncGenerator[str, None]:
    skill = skill_registry.get(skill_name)
    if not skill:
        yield json.dumps({"type": "error", "content": f"Skill '{skill_name}' not found"})
        yield json.dumps({"type": "done"})
        return

    if thread_id:
        set_thread_context(thread_id)

    chat_model = llm_provider.get_chat_model(model, streaming=True)
    agent = create_react_agent(chat_model, ALL_TOOLS)

    lc_messages = [
        SystemMessage(content=skill.system_prompt),
        HumanMessage(content=task),
    ]

    full_content = ""
    try:
        async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
            kind = event.get("event", "")
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    if isinstance(chunk.content, str):
                        full_content += chunk.content
                        yield json.dumps({"type": "token", "content": chunk.content})
            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_input = event.get("data", {}).get("input", {})
                yield json.dumps({
                    "type": "tool_call",
                    "data": {"tool": tool_name, "input": str(tool_input)[:200], "status": "running"},
                })
            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                output = event.get("data", {}).get("output", "")
                yield json.dumps({
                    "type": "tool_result",
                    "data": {"tool": tool_name, "output": str(output)[:500], "status": "completed"},
                })
    except Exception as e:
        yield json.dumps({"type": "error", "content": str(e)})

    yield json.dumps({"type": "done"})
