from __future__ import annotations

from dataclasses import dataclass

from backend.app.api.schemas.marketplace import TalentRecommendationRequest
from backend.app.core.typing import string_list, string_or_default
from backend.app.marketplace.models import TalentListing
from backend.app.tasks.models import Task


@dataclass(frozen=True)
class RoleSpec:
    role: str
    team_role: str
    reason: str
    skill_tags: tuple[str, ...]
    capability_tags: tuple[str, ...]


@dataclass(frozen=True)
class ScoredListing:
    listing: TalentListing
    score: float
    reasons: list[str]
    missing_tags: list[str]


TEAM_ROLE_PRESETS: dict[str, tuple[RoleSpec, ...]] = {
    "research": (
        RoleSpec(
            "project_manager",
            "manager",
            "拆解目标、协调专家、控制交付节奏。",
            ("planning", "coordination"),
            (),
        ),
        RoleSpec(
            "researcher",
            "research_specialist",
            "收集资料、验证来源、沉淀研究证据。",
            ("research", "market"),
            ("web.search",),
        ),
        RoleSpec(
            "analyst",
            "data_analyst",
            "整理数据、发现趋势、输出可执行结论。",
            ("analysis", "data"),
            ("data.analysis",),
        ),
    ),
    "novel": (
        RoleSpec(
            "editor",
            "chief_editor",
            "维护世界观、节奏和章节质量。",
            ("writing", "editing"),
            (),
        ),
        RoleSpec(
            "writer",
            "chapter_writer",
            "按大纲生成章节内容并保持角色一致。",
            ("writing", "story"),
            ("longform.write",),
        ),
        RoleSpec(
            "reviewer",
            "continuity_reviewer",
            "检查设定冲突、伏笔和人物动机。",
            ("review", "continuity"),
            (),
        ),
    ),
    "software": (
        RoleSpec(
            "project_manager",
            "tech_lead",
            "拆分需求、安排实现顺序、验收交付。",
            ("planning", "architecture"),
            (),
        ),
        RoleSpec(
            "software_engineer",
            "backend_engineer",
            "实现后端服务、工具调用和任务编排。",
            ("backend", "python"),
            ("code.execute",),
        ),
        RoleSpec(
            "qa_engineer",
            "qa_specialist",
            "设计测试、复现问题、验证回归。",
            ("testing", "quality"),
            (),
        ),
    ),
    "design": (
        RoleSpec(
            "product_manager",
            "product_manager",
            "明确用户目标、定义范围和验收标准。",
            ("product", "planning"),
            (),
        ),
        RoleSpec(
            "designer",
            "visual_designer",
            "产出界面视觉、素材和交互稿。",
            ("design", "ui"),
            ("image.generate",),
        ),
        RoleSpec(
            "reviewer",
            "design_reviewer",
            "检查一致性、可用性和交付质量。",
            ("review", "ux"),
            (),
        ),
    ),
    "general": (
        RoleSpec(
            "project_manager",
            "manager",
            "拆解目标、安排人员、跟踪进度。",
            ("planning", "coordination"),
            (),
        ),
        RoleSpec(
            "researcher",
            "research_specialist",
            "补齐信息、收集资料、形成判断依据。",
            ("research",),
            ("web.search",),
        ),
        RoleSpec(
            "operator",
            "operator",
            "执行工具调用、整理产物、推动任务完成。",
            ("operations",),
            (),
        ),
    ),
}


def role_specs_for_request(data: TalentRecommendationRequest) -> list[RoleSpec]:
    preset = list(TEAM_ROLE_PRESETS.get(data.team_type, TEAM_ROLE_PRESETS["general"]))
    role_tags = tuple(normalize_tag(tag) for tag in data.skill_tags if tag)
    capability_tags = tuple(normalize_tag(tag) for tag in data.capability_tags if tag)
    if not data.required_roles:
        return [
            RoleSpec(
                role=spec.role,
                team_role=spec.team_role,
                reason=spec.reason,
                skill_tags=tuple(dict.fromkeys((*spec.skill_tags, *role_tags))),
                capability_tags=tuple(dict.fromkeys((*spec.capability_tags, *capability_tags))),
            )
            for spec in preset
        ]
    preset_by_role = {spec.role: spec for spec in preset}
    specs: list[RoleSpec] = []
    for role in data.required_roles:
        normalized_role = normalize_role(role)
        default = preset_by_role.get(normalized_role)
        default_skills = default.skill_tags if default is not None else ()
        default_capabilities = default.capability_tags if default is not None else ()
        specs.append(
            RoleSpec(
                role=normalized_role,
                team_role=default.team_role if default is not None else normalized_role,
                reason=default.reason if default is not None else "老板需求中明确要求该岗位。",
                skill_tags=tuple(dict.fromkeys((*default_skills, *role_tags))),
                capability_tags=tuple(dict.fromkeys((*default_capabilities, *capability_tags))),
            )
        )
    return specs


def missing_work_packages_from_task(task: Task) -> list[dict[str, object]]:
    project_plan = task.project_plan if isinstance(task.project_plan, dict) else None
    if project_plan is None:
        return []
    packages = project_plan.get("work_packages", [])
    if not isinstance(packages, list):
        return []

    missing: list[dict[str, object]] = []
    for raw_package in packages:
        if not isinstance(raw_package, dict):
            continue
        if raw_package.get("assigned_agent_profile_id") is not None:
            continue
        role = string_or_default(raw_package.get("required_role"), "specialist")
        if role == "project_manager":
            continue
        missing.append(
            {
                "package_id": string_or_default(raw_package.get("package_id"), "unknown"),
                "title": string_or_default(raw_package.get("title"), role),
                "required_role": role,
                "required_skills": string_list(raw_package.get("required_skills")),
                "expected_artifacts": string_list(raw_package.get("expected_artifacts")),
            }
        )
    return missing


def missing_work_package_by_id(task: Task, work_package_id: str) -> dict[str, object] | None:
    return next(
        (
            package
            for package in missing_work_packages_from_task(task)
            if package.get("package_id") == work_package_id
        ),
        None,
    )


def role_spec_for_missing_package(package: dict[str, object]) -> RoleSpec:
    role = string_or_default(package.get("required_role"), "specialist")
    return RoleSpec(
        role=normalize_role(role),
        team_role=role,
        reason=f"Work package {package.get('package_id', 'unknown')} has no assigned agent.",
        skill_tags=tuple(
            normalize_tag(skill) for skill in string_list(package.get("required_skills"))
        ),
        capability_tags=(),
    )


def task_team_type(task: Task) -> str:
    snapshot = task.team_snapshot if isinstance(task.team_snapshot, dict) else None
    if snapshot is None:
        return "general"
    team = snapshot.get("team")
    if not isinstance(team, dict):
        return "general"
    return string_or_default(team.get("team_type"), "general")


def score_listing(
    listing: TalentListing,
    *,
    role: str,
    skill_tags: tuple[str, ...],
    capability_tags: tuple[str, ...],
) -> ScoredListing:
    score = 0.0
    reasons: list[str] = []
    missing_tags: list[str] = []
    listing_skills = {normalize_tag(tag) for tag in listing.skill_tags}
    listing_capabilities = {normalize_tag(tag) for tag in listing.capability_tags}
    normalized_role = normalize_role(listing.role)

    if normalized_role == role:
        score += 5
        reasons.append("岗位匹配")
    elif role in normalized_role or normalized_role in role:
        score += 2
        reasons.append("岗位相近")

    for tag in skill_tags:
        if tag in listing_skills:
            score += 1.5
            reasons.append(f"技能匹配: {tag}")
        else:
            missing_tags.append(tag)

    for tag in capability_tags:
        if tag in listing_capabilities:
            score += 1
            reasons.append(f"能力匹配: {tag}")
        else:
            missing_tags.append(tag)

    if listing.risk_level == "low":
        score += 0.25
        reasons.append("低风险")

    return ScoredListing(
        listing=listing,
        score=score,
        reasons=reasons,
        missing_tags=list(dict.fromkeys(missing_tags)),
    )


def normalize_role(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def normalize_tag(value: str) -> str:
    return value.strip().lower()
