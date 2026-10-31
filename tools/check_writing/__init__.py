# PaperGate — writing 阶段检查包
# 按领域拆分，__init__ 聚合导出，保持 `from check_writing import check_*` 契约不变。
# 子模块：paper_text（正文格式）/ tables（表格数据）/ scripts_structure（脚本结构）/
#        figures_dataflow（图与数据流）；_common（跨领域共享 helper）。
from .figures_dataflow import (
    check_data_flow,
    check_figure_dpi,
    check_artifact_tracking,
    check_figure_exists,
    check_figure_references,
    check_figures_naming,
)
from .paper_text import (
    check_abstract_format,
    check_citation_format,
    check_paper_forbidden_syntax,
    check_paper_image_files,
    check_paper_md_frontmatter,
    check_paper_polish,
    check_paper_references_heading,
    check_paper_stat_format,
    check_paper_title_hierarchy,
    check_template_placeholder,
    check_word_count,
)
from .scripts_structure import (
    check_locale_contract,
    check_raw_data_archive,
    check_research_plan_sections,
    check_results_manifest,
    check_scripts_structure,
    check_write_dir_clean,
)
from .tables import (
    check_table_data_closure,
    check_table_references,
)

__all__ = [
    "check_abstract_format",
    "check_citation_format",
    "check_data_flow",
    "check_figure_dpi",
    "check_artifact_tracking",
    "check_figure_exists",
    "check_figure_references",
    "check_figures_naming",
    "check_paper_forbidden_syntax",
    "check_paper_image_files",
    "check_paper_md_frontmatter",
    "check_paper_polish",
    "check_paper_references_heading",
    "check_paper_stat_format",
    "check_paper_title_hierarchy",
    "check_locale_contract",
    "check_raw_data_archive",
    "check_research_plan_sections",
    "check_results_manifest",
    "check_scripts_structure",
    "check_table_data_closure",
    "check_table_references",
    "check_template_placeholder",
    "check_word_count",
    "check_write_dir_clean",
]
