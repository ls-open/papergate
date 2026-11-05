"""技能稳定接口（Skill API）。

技能与框架之间只有这一层契约：技能需要读配置、日志、bib、frontmatter 时，
从这里导入，不要反向 import `tools/lib_*` 的内部实现。

向后兼容承诺：本模块已导出的名字不删、不改签名；新增能力只追加。
技能被移除时，核心不依赖它；核心改动时，尽量不改这里。

技能贡献检查项时，函数的签名与核心检查一致：`f(ctx: ProjectContext) -> CheckResult`。
`CheckResult` / `ProjectContext` / `build_context` 就是为此导出的——不要在技能里
反向 import `tools/check_shared.py`，那不在兼容承诺内。
"""

from check_shared import (  # noqa: F401
    CHECK_STAGES,
    CheckResult,
    ProjectContext,
    build_context,
    is_submission_period,
)
from lib_common import (  # noqa: F401
    MIN_PLAUSIBLE_SOURCE_WORDS,
    build_evidence_inventory,
    build_notes_index,
    cite_sentences,
    count_words,
    extract_chapter_citations,
    fingerprint_evidence_inputs,
    load_env_file,
    load_frontmatter,
    mojibake_count,
    mojibake_hits,
    needs_gist,
    normalize_doi,
    parse_bib_full,
    parse_bib_keys,
    read_file,
    split_frontmatter,
    strip_non_prose,
)
from lib_config import (  # noqa: F401
    find_project_root,
    load_config,
    run_git,
)
from lib_log import configure_logging, get_logger  # noqa: F401
