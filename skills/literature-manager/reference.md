# 参考手册

---

## 1. 库全景与 API 口径

对账库全景以 Zotero 客户端显示为准（本技能不提供全库盘点命令）。API 口径：

| 口径 | 结论 |
|:-----|:-----|
| `/items`、`/items/top` | 默认**排除回收站**；回收站走 `/items/trash`（或 `includeTrashed=1`） |
| 未分类条目 | = 全库顶层 − 属于任一集合的顶层（**必须分页合并去重**） |
| **Total-Results 求和陷阱** | 跨集合条目被多个集合重复计数，`Σ各集合 − 全库` 会**低估未分类** |
| 条目→所属集合反查 | **不要用 `GET /items/<key>/collections`**（返回内容并非该条目所属集合）。可靠做法：逐集合 `GET /collections/<key>/items/top` 枚举后反演映射；或直接看完整条目 GET 的 `data.collections`（权威） |
| `include=collections` 参数 | **不支持**（400），别用 |
| 条目加入集合 | 官方**没有** `POST /collections/<key>/items` 端点；正规途径是更新条目自身的 `collections` 字段。用内置 `collections add --key <集合> --item <KEY...>`（PATCH 追加完整列表，缺省预览） |
| 空集合显示 | Zotero 桌面端可隐藏空集合（"显示空集合"设置），**API 永远返回全部集合**——桌面端与 API 集合数不一致是正常现象 |
| 条目移出集合 | PATCH `/items/<key>` 提交去掉目标后的**完整** `collections` 数组（数组属性按完整列表解释），带 `If-Unmodified-Since-Version`；条目保留在库和其他集合。用内置 `collections remove --key <集合> --item <KEY...>`（缺省预览）。**勿用 `delete`**——那是整库删条目（所有集合归属一并消失） |
| 集合统计 | `collections list` 显示各集合顶层条目数（Total-Results，单集合口径可信） |

**以桌面端为准**：桌面端是用户第一视角；API 用于精确计算与自动化。两者对不上时先按上表口径核对，不要怀疑数据丢失。

---

## 2. 写请求总则（Zotero Web API v3）

- **版本控制强制**：所有 PUT/PATCH/DELETE 必须带条目当前版本——`If-Unmodified-Since-Version` 请求头或 JSON 内 `version` 属性。服务器状态已变时返回 **412**（需重新 GET 后重试）；完全不带版本头的删除类请求返回 **428**。
- **对象版本 vs 库版本**：单对象写校验该对象自身 `version`；多对象批量 POST 可逐条带各自版本，或统一传库版本头。
- **PATCH vs PUT**：PATCH 只改提交的字段（部分更新）；PUT 是全量替换，未提交的已有字段会被清掉——改字段前先 GET 全量。数组属性（`collections`/`tags`/`creators`）按**完整列表**解释：想保留其余归属就必须提交完整新列表。
- **常用错误码**：400 请求体错 / 404 key 不存在 / 409 库被锁定（同步中）/ 412 版本冲突 / 428 缺版本头 / 429 触发限流（等待后重试）/ 500 服务端错误。
- 写操作需 API key 具备库写权限（技能 `.env` 配置）。

---

## 3. 内置命令 ↔ API 效果对照（影响范围与可逆性）

| 命令 | 实际 API 调用 | 影响范围 | 可逆性 |
|:-----|:-------------|:---------|:-------|
| `zotero pull` | GET 集合 bibtex 导出 → 覆盖本地 .bib | 仅本地文件；线上只读 | git 历史 + 重拉 |
| `zotero update` | PUT 条目全量 data + 版本头 | 单条目字段 | 无历史，慎用 |
| `zotero delete` | PATCH `/items/<key>` `{deleted:1}` + 版本头（缺省移入回收站）；`--purge` 才 DELETE | **整库删条目，所有集合归属同时消失** | 缺省可在回收站恢复；`--purge` 不可恢复 |
| `collections create/rename` | POST/PUT `/collections/<key>` + 版本头 | 集合壳本身（**删除集合为高危操作，不开放 CLI/AI，须在客户端手动删除**） | rename/create 直接 |
| `collections remove` | PATCH 条目 `collections` 数组（去掉目标）+ 版本头 | 仅目标集合归属；条目与其他集合不动 | 用 `add` 加回即恢复 |
| `collections add` | PATCH 条目 `collections` 数组（追加目标）+ 版本头 | 仅新增目标集合归属；现有归属不动 | 用 `remove` 移出即恢复 |

> **多集合共用文献准则**（同一篇文献挂多个项目集合是常态）：调整归属一律走 `add`/`remove`（只动目标集合）；`delete` 是整库级操作，执行前确认该条目不再被任何项目需要；批量清理某个集合前后，用逐集合枚举反演核对各项目集合条目数不变。

---

## 4. 故障排查

| 现象 | 原因与处理 |
|:-----|:-----|
| `pull` 报"请指定集合 Key" | 当前目录不在项目内（无 `.papergate_config [zotero]`）；在项目目录运行或显式 `--collection` |
| 写请求 412 | 版本过期（他人/其他端改过该条目）；重新 GET 取新版本再重试 |
| bib 中文 citekey 为 `__<year>` 坍缩形态 | pull 自动拼音化修复（只改线上 citationKey，重拉一轮）；全部失败时按提示手动在 Zotero 设 Manage Citation Key |
| bib keywords 杂乱（check 提示"标签较多"） | keywords = 原文作者词 + 数据库附加词（如 INSPEC 受控词）机制性混搭，属正常；按需用 `zotero update --key X --tags "词1,词2"` 整体覆盖（空串清空）后 pull |

---

## 5. 维护原则与官方文档

- **与官方文档冲突时以官方文档为准**。
- 新增写操作前先查下方官方 write_requests 页确认端点存在性与语义，严禁凭印象调用未经官方文档证实的端点。

| 文档 | 用途 |
|:-----|:-----|
| Zotero Web API v3 基础 | <https://www.zotero.org/support/dev/web_api/v3/basics>（读端点、分页、Total-Results、includeTrashed） |
| Zotero Web API v3 写请求 | <https://www.zotero.org/support/dev/web_api/v3/write_requests>（PATCH/PUT 语义、版本头、对象JSON、错误码） |
| Zotero Web API v3 规范 | <https://www.zotero.org/support/dev/web_api/v3/specification>（端点细节、deleted 端点） |
| Zotero 桌面端同步/集合 | <https://www.zotero.org/support/collections>（集合概念、"显示空集合"设置） |
