# 硬门禁声称-实现对照表（CLAIMS）

> 目的：standards/*.md 等文档声称的「硬规 / 阻塞 / 门禁」必须已有 CHECK_REGISTRY
> 实现与测试覆盖。本表是「声称（文档）↔ 实现（注册表 + 测试）」的对照锁，
> 对应审计发现的 P1-1 结构性模式（文档声称先行、实现滞后，历史出现 3+ 次）。
>
> 纪律（tests/test_claims_sync.py 锁死，漏登记/残留行即测试失败）：
> - 文档新增/修改硬门禁语义 → 同步新增/修改本表行
> - 删除/降级某门禁实现 → 移除对应行
> - 本表只收「阻塞 / 门禁」级声称；warning 级提示不在此列（那是常规检查项的事）
> - 章节名用标题文字（标题重排不失效）；不写裸编号
> - **语义锁列**：声称含「投稿期 error/阻塞/硬门禁」的行必须标 `submission_error`，
>   测试锁死其源码含阶段机制（stage_severity / is_submission_stage）或仅投稿期注册
>   ——这是 2026-08-16 修复 table_refs「docstring 声称投稿期 error 但实现固定 minor」
>   后追加的语义层锁，防声称-实现漂移的同一模式再现。

| 声称来源 | 章节 | 声称（硬规/阻塞/门禁） | check_key | 语义锁 |
|:---------|:-----|:----------------------|:----------|:-------|
| standards/writing/data-flow.md | §1.0 数据采集子阶段 / §1.3 管线产出登记 | 采集快照必须登记+入库；分析产出必须登记依赖；未登记/未入库/依赖过期即提示（投稿期阻塞） | results_manifest | submission_error |
| standards/writing/data-flow.md | §二 图的产出约定 | 位图 DPI 统一 300；不合格图引用给提示、不阻断 | figure_dpi | — |
| standards/writing/data-flow.md | §三 表的产出约定 | 每张表的数据源必须经 table_map.json 显式声明；映射文件缺失报 warning 提示补充 | table_data_closure | — |
| standards/writing/citation-standards.md | §2.1 键数基准 | 各章引用键数达标，投稿期硬门禁（不达标阻塞投稿） | citation_density | submission_error |
| standards/writing/citation-standards.md | §八 文献库防篡改与单向同步 | references.bib 必须纳入 Git 跟踪（未跟踪即报错） | bib_git_tracked | — |
| standards/submission/submission.md | §五 投稿包生成步骤 | 投稿稿件 submission_manuscript.md 必须存在（存在性 + CSL 路径检查） | submission_manuscript | submission_error |
| standards/submission/submission.md | §一 标签策略 | 投稿动作必须有 `<项目名>-vX.Y-<目标>-submitted` 标签（硬闸门） | submission_tag | submission_error |
| standards/submission/submission.md | §一 标签策略 | 论文标签命名规范（带项目名前缀防冲突） | git_tag_naming | — |
| papergate/ARCHITECTURE.md | §3.4 投稿分文件架构 | `:::` 等 L2 导出语法只允许在投稿副本（源稿出现即拦截） | paper_forbidden_syntax | — |
| standards/submission/submission.md | §二 投稿包文件组织与易错点 | 投稿包导出文件过期（源稿更新后未重导）即提示 | export_freshness | — |
| standards/submission/submission.md | §五 投稿包生成步骤 | 投稿稿件挂载 CSL 的路径必须真实存在 | csl_path | — |
| standards/writing/citation-standards.md | §一 文献准入分级 | 笔记与 bib 双向闭合（有笔记无 bib 条目等缺口提示） | notes_bib_closure | — |
| standards/writing/data-flow.md | §三 表的产出约定 | 正文表格引用必须闭环（引用↔定义，投稿期 error） | table_refs | submission_error |
