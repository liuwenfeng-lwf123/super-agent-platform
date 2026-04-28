"""Skills, skill library, and skill management routes."""
from fastapi import APIRouter, HTTPException
import os

router = APIRouter()


@router.get("/skills")
async def list_skills():
    from app.skills.base import skill_registry
    from app.agents.evolution import skill_registry as custom_skill_registry
    built_in = [s.model_dump() for s in skill_registry.list_skills()]
    custom = custom_skill_registry.list_skills()
    # Custom skills override built-ins with the same name
    custom_names = {s.get("name") for s in custom}
    deduped_built_in = [s for s in built_in if s.get("name") not in custom_names]
    return deduped_built_in + custom


@router.get("/skill-library")
async def list_skill_library():
    from app.skills.ecosystem import list_ecosystem_skills
    from app.agents.evolution import skill_registry as custom_skill_registry
    installed = {skill["name"] for skill in custom_skill_registry.list_skills()}
    skills = []
    for skill in list_ecosystem_skills():
        item = dict(skill)
        item["installed"] = item["name"] in installed
        item["source"] = "official"
        item.pop("system_prompt", None)
        skills.append(item)
    return {"skills": skills, "count": len(skills)}


@router.post("/skill-library/{name}/install")
async def install_skill_library_item(name: str, payload: dict | None = None):
    from app.skills.ecosystem import get_ecosystem_skill
    from app.agents.evolution import skills_hub
    skill = get_ecosystem_skill(name)
    if not skill:
        return {"ok": False, "message": f"生态技能 '{name}' 不存在"}
    ok, message = skills_hub.install_from_json(skill, source="official", force=(payload or {}).get("force", False))
    return {"ok": ok, "message": message, "name": name}


@router.post("/skill-library/openclaw/preview")
async def preview_openclaw_skill(payload: dict):
    from app.skills.openclaw_import import fetch_remote_skill_md, preview_openclaw_skill as preview_skill
    content = payload.get("content", "")
    source_url = payload.get("url", "")
    try:
        if source_url and not content:
            content = fetch_remote_skill_md(source_url)
        if not content:
            return {"ok": False, "message": "请提供 SKILL.md 内容或 raw URL"}
        return preview_skill(content, source_url=source_url)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/skill-library/openclaw/import")
async def install_openclaw_skill(payload: dict):
    from app.skills.openclaw_import import build_openclaw_skill_data, fetch_remote_skill_md, preview_openclaw_skill as preview_skill
    from app.agents.evolution import skills_hub
    content = payload.get("content", "")
    source_url = payload.get("url", "")
    force = bool(payload.get("force", False))
    try:
        if source_url and not content:
            content = fetch_remote_skill_md(source_url)
        if not content:
            return {"ok": False, "message": "请提供 SKILL.md 内容或 raw URL"}
        preview = preview_skill(content, source_url=source_url)
        security = preview.get("security", {})
        if not security.get("safe", False) and not force:
            return {
                "ok": False,
                "message": "安全扫描未通过，请检查后再强制安装",
                "security": security,
            }
        skill = build_openclaw_skill_data(content, source_url=source_url)
        ok, message = skills_hub.install_from_json(skill, source="community", force=force)
        return {"ok": ok, "message": message, "name": skill.get("name"), "security": security}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.get("/skill-library/clawhub/search")
async def search_clawhub_skill_library(q: str = "", limit: int = 20):
    from app.skills.openclaw_import import search_clawhub_skills
    from app.agents.evolution import skill_registry as custom_skill_registry
    installed = {
        skill.get("clawhub_slug") or skill.get("name")
        for skill in custom_skill_registry.list_skills()
        if skill.get("source") in {"clawhub", "community"}
    }
    try:
        data = search_clawhub_skills(q, limit=limit)
        for item in data.get("results", []):
            item["installed"] = item.get("slug") in installed
        return data
    except Exception as exc:
        return {"results": [], "count": 0, "error": str(exc)}


@router.get("/skill-library/clawhub/auth")
async def get_clawhub_auth_status_api():
    from app.skills.openclaw_import import get_clawhub_auth_status
    return get_clawhub_auth_status()


@router.get("/skill-library/clawhub/{slug}")
async def get_clawhub_skill_library_item(slug: str):
    from app.skills.openclaw_import import get_clawhub_skill
    from app.agents.evolution import skill_registry as custom_skill_registry
    try:
        data = get_clawhub_skill(slug)
        data["installed"] = any(
            skill.get("clawhub_slug") == slug or skill.get("name") == slug
            for skill in custom_skill_registry.list_skills()
        )
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/skill-library/clawhub/{slug}/preview")
async def preview_clawhub_skill_library_item(slug: str, payload: dict | None = None):
    from app.skills.openclaw_import import preview_clawhub_skill
    try:
        return preview_clawhub_skill(slug, version=(payload or {}).get("version"))
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/skill-library/clawhub/{slug}/install")
async def install_clawhub_skill_library_item(slug: str, payload: dict | None = None):
    from app.skills.openclaw_import import build_clawhub_skill_data, preview_clawhub_skill
    from app.agents.evolution import skills_hub
    payload = payload or {}
    force = bool(payload.get("force", False))
    version = payload.get("version")
    try:
        preview = preview_clawhub_skill(slug, version=version)
        security = preview.get("security", {})
        if not security.get("safe", False) and not force:
            return {
                "ok": False,
                "message": "安全扫描未通过，请检查后再强制安装",
                "security": security,
            }
        skill, detail = build_clawhub_skill_data(slug, version=version)
        ok, message = skills_hub.install_from_json(skill, source="community", force=force)
        return {
            "ok": ok,
            "message": message,
            "name": skill.get("name"),
            "slug": slug,
            "detail": detail,
            "security": security,
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc), "slug": slug}


@router.post("/skills/recommend")
async def recommend_skill(message: str):
    from app.skills.base import skill_registry
    msg = message.lower()
    recommendations = []

    if any(w in msg for w in ["research", "investigate", "analyze", "study", "survey", "explore", "调研", "研究", "分析", "调查"]):
        recommendations.append(skill_registry.get("deep-research"))
    if any(w in msg for w in ["web", "page", "website", "app", "html", "frontend", "dashboard", "网页", "页面", "网站", "前端"]):
        recommendations.append(skill_registry.get("web-page"))
    if any(w in msg for w in ["report", "document", "paper", "write up", "summary", "报告", "文档", "论文", "总结", "写报告"]):
        recommendations.append(skill_registry.get("report-generation"))
    if any(w in msg for w in ["slide", "presentation", "ppt", "keynote", "幻灯片", "演示", "PPT"]):
        recommendations.append(skill_registry.get("slide-creation"))
    if any(w in msg for w in ["data", "csv", "excel", "chart", "visualization", "statistics", "数据", "图表", "可视化", "统计", "分析数据"]):
        recommendations.append(skill_registry.get("data-analysis"))

    recommendations = [r for r in recommendations if r is not None]
    if not recommendations:
        recommendations = list(skill_registry._skills.values())[:3]

    return [{"name": r.name, "display_name": r.display_name, "description": r.description} for r in recommendations]


@router.post("/skills/{name}/copy")
async def copy_skill(name: str, payload: dict):
    import re
    from app.skills.base import skill_registry as built_in_skill_registry
    from app.agents.evolution import skill_registry as custom_skill_registry

    source = custom_skill_registry.get_skill(name)
    if source:
        display_name = source.get("display_name", name)
        description = source.get("description", "")
        system_prompt = source.get("system_prompt", "")
        tools = list(source.get("tools") or [])
        category = source.get("category", "general")
    else:
        built_in = built_in_skill_registry.get(name)
        if not built_in:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        config = built_in.to_config()
        display_name = config.display_name
        description = config.description
        system_prompt = config.system_prompt
        tools = list(config.tools) if config.tools else []
        category = "built_in_copy"

    raw_name = payload.get("new_name") or f"custom_{name}"
    base_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_name).strip("_").lower() or f"custom_{name}"
    new_name = base_name
    suffix = 2
    while custom_skill_registry.get_skill(new_name) is not None:
        new_name = f"{base_name}_{suffix}"
        suffix += 1

    new_display_name = payload.get("display_name") or f"{display_name}（可进化副本）"
    ok, message = custom_skill_registry.create_skill(
        name=new_name,
        display_name=new_display_name,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        category=category,
    )
    return {"ok": ok, "message": message, "name": new_name}


# --- Skill Management (Hermes-style, progressive disclosure) ---
@router.get("/skills/progressive")
async def list_skills_progressive():
    """Hermes Level-0 progressive-disclosure listing (name + description only)."""
    from app.agents.evolution import skill_registry
    return {"skills": skill_registry.list_skills_level0()}


@router.get("/skills/agentskills")
async def list_agentskills_pre():
    """Listed before /skills/{name} so FastAPI matches it first."""
    from app.skills.agentskills_compat import discover_agentskills
    return {"skills": [s.to_dict() for s in discover_agentskills()]}


@router.get("/skills/{name}")
async def get_skill(name: str, section: str = ""):
    from app.agents.evolution import skill_registry
    from app.skills.base import skill_registry as built_in_skill_registry
    if section:
        content = skill_registry.view_skill_level2(name, section)
        if content is None:
            built_in = built_in_skill_registry.get(name)
            content = getattr(built_in, section, None) if built_in else None
        return {"name": name, "section": section, "content": content}
    skill = skill_registry.view_skill_level1(name)
    if not skill:
        built_in = built_in_skill_registry.get(name)
        if not built_in:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        config = built_in.to_config().model_dump(mode="json")
        config["version"] = 1
        config["built_in"] = True
        config["editable"] = False
        return config
    skill = dict(skill)
    skill["editable"] = True
    return skill


@router.post("/skills/{name}/patch")
async def patch_skill_api(name: str, payload: dict):
    from app.agents.evolution import skill_registry
    old_string = payload.get("old_string", "")
    new_string = payload.get("new_string", "")
    if not old_string:
        raise HTTPException(status_code=400, detail="old_string required")
    ok, msg = skill_registry.patch_skill(name, old_string, new_string)
    return {"ok": ok, "message": msg}


@router.post("/skills/{name}/edit")
async def edit_skill_api(name: str, payload: dict):
    from app.agents.evolution import skill_registry
    new_content = payload.get("content", "")
    if not new_content:
        raise HTTPException(status_code=400, detail="content required")
    ok, msg = skill_registry.edit_skill(name, new_content)
    return {"ok": ok, "message": msg}


@router.post("/skills/{name}/rollback")
async def rollback_skill_api(name: str):
    from app.agents.evolution import skill_registry
    ok, msg = skill_registry.rollback_skill(name)
    return {"ok": ok, "message": msg}


@router.get("/skills/{name}/versions")
async def get_skill_versions(name: str):
    from app.agents.evolution import skill_registry
    return {"name": name, "versions": skill_registry.get_versions(name)}


@router.post("/skills/{name}/score")
async def score_skill_api(name: str, payload: dict):
    from app.agents.evolution import skill_registry
    score = payload.get("score", 0)
    return skill_registry.record_score(name, int(score))


@router.post("/skills/{name}/crystallize")
async def crystallize_skill_api(name: str):
    from app.agents.evolution import skill_registry
    ok, msg = skill_registry.crystallize_skill(name)
    return {"ok": ok, "message": msg}


@router.get("/skills/{name}/maturity")
async def get_skill_maturity(name: str):
    from app.agents.evolution import skill_registry
    skill = skill_registry.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {
        "name": name, "maturity": skill.get("maturity", "draft"),
        "run_count": skill.get("run_count", 0),
        "avg_score": skill_registry._avg_score(skill),
        "trust_level": skill.get("trust_level", "local"),
    }


@router.get("/skills/maturity/report")
async def maturity_report():
    from app.agents.evolution import skill_registry
    return {"report": skill_registry.get_maturity_report()}


@router.post("/skills/{name}/files")
async def write_skill_file_api(name: str, payload: dict):
    from app.agents.evolution import skill_registry
    ok, msg = skill_registry.write_skill_file(name, payload.get("file_path", ""), payload.get("content", ""))
    return {"ok": ok, "message": msg}


@router.delete("/skills/{name}/files/{file_path:path}")
async def remove_skill_file_api(name: str, file_path: str):
    from app.agents.evolution import skill_registry
    ok, msg = skill_registry.remove_skill_file(name, file_path)
    return {"ok": ok, "message": msg}


@router.get("/skills/{name}/files")
async def list_skill_files_api(name: str):
    from app.agents.evolution import skill_registry
    return {"name": name, "files": skill_registry.list_skill_files(name)}


@router.post("/skills/{name}/feedback")
async def record_skill_feedback_api(name: str, payload: dict):
    from app.agents.learning_loop import learnings_loop
    return learnings_loop.record_feedback(name, payload.get("feedback", ""), payload.get("context", ""))


@router.get("/skills/{name}/learnings")
async def get_skill_learnings(name: str):
    from app.agents.learning_loop import learnings_loop
    return {"name": name, "learnings": learnings_loop.get_learnings(name)}


@router.get("/skills/learnings/all")
async def list_all_learnings():
    from app.agents.learning_loop import learnings_loop
    return {"learnings": learnings_loop.list_all_learnings()}


@router.get("/skills/agentskills")
async def list_agentskills():
    from app.skills.agentskills_compat import discover_agentskills
    return {"skills": [s.to_dict() for s in discover_agentskills()]}


@router.post("/skills/agentskills/export")
async def export_agentskill(payload: dict):
    from app.skills.agentskills_compat import export_skill_md
    md = export_skill_md(
        name=payload["name"],
        description=payload.get("description", ""),
        system_prompt=payload.get("system_prompt", ""),
        author=payload.get("author", ""),
        version=payload.get("version", "1.0.0"),
    )
    return {"skill_md": md}


@router.post("/skills/hub/install")
async def hub_install_skill(payload: dict):
    from app.agents.evolution import skills_hub
    return dict(zip(["ok", "message"],
        skills_hub.install_from_json(payload.get("skill", {}), payload.get("source", "community"), payload.get("force", False))))


@router.get("/skills/hub/installed")
async def hub_list_installed():
    from app.agents.evolution import skills_hub
    return {"installed": skills_hub.list_installed()}


@router.get("/skills/hub/quarantined")
async def hub_quarantined():
    from app.agents.evolution import skills_hub
    return {"quarantined": skills_hub.get_quarantined()}


@router.post("/skills/parse-md")
async def parse_skill_md_api(payload: dict):
    from app.agents.self_evolution import parse_skill_md
    return parse_skill_md(payload.get("content", ""))


@router.post("/skills/render-md")
async def render_skill_md_api(payload: dict):
    from app.agents.self_evolution import render_skill_md
    return {"rendered": render_skill_md(payload)}


@router.post("/skills/check-env")
async def check_skill_env_api(payload: dict):
    from app.agents.self_evolution import check_skill_env_requirements
    return {"requirements": check_skill_env_requirements(payload)}


@router.post("/skills/scan-external")
async def scan_external_dirs_api(payload: dict):
    from app.agents.self_evolution import scan_external_skill_dirs
    return {"skills": scan_external_skill_dirs(payload.get("dirs", []))}
