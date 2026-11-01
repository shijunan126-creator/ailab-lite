"""Official skill marketplace: a small, curated catalog shipped with the platform.

Phase 1 is intentionally read-only: the catalog is code, not user content. Installing copies the package into the
user's personal skill library, where the normal Worker scan, review policy, versioning and deletion rules apply.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

MARKET_ROOT = Path(__file__).with_name("market_skills")

CATALOG: tuple[dict, ...] = (
    {
        "slug": "work-report",
        "name": "work-report",
        "title": "周报/月报",
        "description": "将工作记录整理为有结果、有风险和下一步行动的周报或月报。",
        "category": "写作",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "meeting-minutes",
        "name": "meeting-minutes",
        "title": "会议纪要",
        "description": "从会议记录中提炼议题、决策、分歧与可追踪的行动项。",
        "category": "写作",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "contract-review",
        "name": "contract-review",
        "title": "合同审阅要点",
        "description": "梳理合同义务、条款风险与谈判建议，生成供法务复核的问题清单。",
        "category": "法务",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "data-analysis-report",
        "name": "data-analysis-report",
        "title": "数据分析报告",
        "description": "从业务问题出发核对数据口径，形成有依据、有局限的分析与建议。",
        "category": "分析",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "competitor-analysis",
        "name": "competitor-analysis",
        "title": "竞品分析",
        "description": "围绕用户场景比较竞品定位、能力与商业模式，形成可验证的产品建议。",
        "category": "分析",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "hiring-kit",
        "name": "hiring-kit",
        "title": "招聘JD与面试题",
        "description": "将岗位目标转为清晰JD、结构化面试题和基于证据的评分标准。",
        "category": "人事",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "official-writing",
        "name": "official-writing",
        "title": "公文/通知写作",
        "description": "按受众和行文目的起草通知、请示、函等正式文本，核对执行要素。",
        "category": "写作",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
    {
        "slug": "speech-writing",
        "name": "speech-writing",
        "title": "演讲稿/致辞",
        "description": "根据听众、场合和时长写出适合口头表达的演讲稿或致辞。",
        "category": "写作",
        "version": "1.0.0",
        "license": "Apache-2.0",
        "author": "AILab",
        "requires": [],
        "builtin": False,
    },
)


def listing() -> list[dict]:
    return [dict(item) for item in CATALOG]


def find(slug: str) -> dict | None:
    return next((dict(item) for item in CATALOG if item["slug"] == slug), None)


def package_zip(slug: str) -> bytes:
    """Build the installable ZIP from the checked-in package directory."""
    entry = find(slug)
    if entry is None:
        raise KeyError(slug)
    root = MARKET_ROOT / slug
    if not root.is_dir():
        raise FileNotFoundError(f"market package missing: {slug}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError(f"market package has no SKILL.md: {slug}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()
