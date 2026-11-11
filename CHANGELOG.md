# 更新记录

## 未发布

- **修订期工程支撑**（此前只有文档 SOP）：`papergate.py revision new R1` 生成
  `review/revisions/R<n>/` 的 `revision.json`（轮次/期刊/投稿版 commit）与回复信骨架，
  只补缺失文件、不覆盖已有内容
- **修子阶段误报**：回复信是否「已写」按**槽位填没填**判（占位清空 + 至少一条 `### `
  逐条小节，`lib_config.revision_letter_state`），不再按「文件存在」判——空骨架落盘当天
  不再被报成「回复信已写，待修订 paper」；当前轮次按轮次号最大者取，R2 建骨架时不会被
  R1 的完整回复信盖住
- **新增 `revision_dirs` 检查**：修订目录缺回复信 / 缺期刊来信原件 / 占位未替换 /
  `revision.json` 轮次与目录名不一致 / 投稿版 commit 解不开。只核确定性事实，不判回复质量
- `submission.md §三` 补增量复审口径（以投稿版 commit 为 since、意见类型→受影响模块映射），
  审查技能 `review.md` 补「修订期增量复审」节（按模块算覆盖、`round.json` 只写本轮真重审的模块）

- **文献笔记改「证据卡」**：笔记不再是把 PDF 全文重打一遍，只留六块骨架
  （研究问题／方法／关键发现／边界／摘录／不该用来支撑）与**带位置的摘录**；
  方法细节回 PDF 读。frontmatter 新增 `source_kind`（pdf｜abstract｜web），
  它同时决定摘录位置怎么写（`p.12`｜`abstract`｜URL），也是证据等级的**声明值**
- **退役五项转录保真度检查**：`note_structure` / `note_gist` / `note_heading_gaps` /
  `note_table_gaps` / `note_word_preservation`。它们查的是「抄得像不像原文」，
  而核心从不查「这条笔记撑不撑得住正文那句话」；拆开后该由摘录位置承担可回查性
- **新增 `evidence_card` 检查**：六块齐不齐、每条摘录有没有位置与原文、
  **被正文引用的文献至少一条实引原文**；报覆盖率不逐篇刷屏，旧格式笔记计入「未转换」
- `determine_evidence_level` 按 `source_kind` 定级：pdf/web 一手，abstract 二手；
  `concept_note` 由 full 改判 **partial**（AI 概括不等于原文）
- `refresh-words` 命令与 `source_words` 要求退役：保留率检查没了，词数基准无消费方
- 测试与发布产物同一世界：`PAPERGATE_SKILLS_DIR` 可覆盖框架技能目录，
  测试默认指向空目录——本机装了私有技能也不再干扰「未装技能」类断言
- `dist.py` 容忍「已跟踪但工作树已删」的文件（删了还没提交时不再崩）

- **技能可贡献检查项**：`manifest.yaml` 新增 `checks:`（注册名 / 入口 / 函数 / 阶段），
  合并后与核心检查项同等对待——同阶段筛选、同 JSON、同 action_items，`check --list` 一并列出；
  与核心检查项重名一律拒绝
- **技能可补充阶段子状态**：`manifest.yaml` 新增 `status:`，核心只判粗阶段，
  「第几轮、完成到哪」由能力自报；`stage` 不允许技能改写
- 技能命令转发带上 `PYTHONPATH`，技能脚本才 import 得到 `skill_api`
- 技能加载失败（入口缺失 / 模块导不进 / 函数名写错 / 检查项缺 `stage`）逐条报出，
  不再静默降级为「未启用」——装坏比没装更危险
- **技能分档**：`literature-manager` / `md-to-word-academic` 由核心声明为**必备**
  （`REQUIRED_SKILLS`）——核心检查无条件读它们的产物，缺了报 error 而不是「未启用」；
  审查、检索仍是可选。新检查项 `required_skills`
- **`writes` 有了约束**：声明的路径必须落在产物区内
  （`write/` `references/` `results/` `review/` `tmp/`），越界即加载失败。
  技能不能自行开辟产物区——产物位置由目录契约定义，技能只是生产者
- 新增 `framework/philosophy.md`：设计哲学与「有意不做」的红线，作为改框架时的单一理由来源
- README 精简：去掉会过时的计数（检查项数、测试数、示例输出），改为给出可自查的命令

## 1.0.0 — 首次公开发布

- 核心框架：项目骨架、稿件契约、阶段状态机、导出适配层
- 检查项覆盖基础、写作、引用、投稿各阶段（权威清单以 `check --list` 为准）
- **准备期**：正文一字未写的项目只跑基础检查，内容类检查显示不运行，避免新建项目一上来就满屏红叉；写了第一个字即回到写作期
- **配置校验**：`.papergate_config` 里的未知配置段与未知字段会被报出来，拼错的键给出正确写法，避免「写了却不生效」
- 修正 `[citation] overuse_max`：文档与提示文案一直让用户配它，代码此前并未读取，现已生效
- 技能可插拔：技能放在文件夹里，不装进 Agent 的技能目录；没装的能力显示「未启用」，不产出假的通过
- 示例项目 `examples/demo-paper` 写作期检查 0 error
- 测试随仓库演进，CI 覆盖 Python 3.11 / 3.13
