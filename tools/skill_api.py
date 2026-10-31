"""技能稳定接口（Skill API）。

技能与框架之间只有这一层契约：技能需要读配置、日志、bib、frontmatter 时，
从这里导入，不要反向 import `tools/lib_*` 的内部实现。

向后兼容承诺：本模块已导出的名字不删、不改签名；新增能力只追加。
技能被移除时，核心不依赖它；核心改动时，尽量不改这里。
"""

from lib_common import (  # noqa: F401
    MIN_PLAUSIBLE_SOURCE_WORDS,
    count_words,
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
