# AGENTS.md

## 1. 核心规则
1. 每次任务开始先评估复杂度：
   - 低复杂度：不做证据检索
   - 中等复杂度：先调用 `$github-kb`
   - 高复杂度：先调用 `$programming-evidence-search`
2. 每次代码修改前，先阅读当前 Git 仓库至少 `20` 条提交记录；若不足以支撑判断，继续阅读。
3. 每次任务只允许完成一个功能的开发或改动；若需求跨多个功能，拆成多个独立任务。
4. 每次代码修改 / 重构 / 调试后，必须调用 `$git-md-micro-commit` 存档；不询问用户。
5. 每次最终答复后，默认调用 `$codex-notifier`；“关闭响铃”只关声音，“关闭通知”全部关闭。

## 2. 复杂度判定
### 低复杂度
满足多数以下特征时视为低复杂度：
- 小范围、局部、确定性修改
- 不涉及新库 / 新框架 / 新 API / 新协议
- 不涉及架构设计、技术选型、方案对比
- 不涉及复杂调试、兼容性、版本差异
- 本地代码、仓库历史和已有上下文足以支撑判断

处理：直接执行，不调用证据检索 skill。

### 中等复杂度
满足任一以下特征时视为中等复杂度：
- 多文件 / 多模块协作，但范围可控
- 需要确认开源仓库实现、issue、PR、commit、release
- 需要确认外部库行为或接口语义
- 一般调试，问题范围基本可收敛
- 关键证据主要来自 GitHub

处理：先调用 `$github-kb`。若证据不足，再升级到 `$programming-evidence-search`。

### 高复杂度
满足任一以下特征时视为高复杂度：
- 架构设计 / 技术选型 / 方案对比
- 中大型修改、系统性重构、跨功能联动
- 复杂调试、根因不明确
- 版本迁移、升级、兼容性处理
- 用户明确要求“先做证据研究再实现”
- 需要综合官方文档、规范、GitHub、社区资料等多源证据
- 判断错误会明显影响实现方向、质量或安全性

处理：必须先调用 `$programming-evidence-search`。

## 3. 标准流程
- 低复杂度计划：本地分析 -> 计划/实现 -> 最终答复 -> `$codex-notifier`
- 中等复杂度计划：本地分析 -> `$github-kb` -> 计划/实现 -> 最终答复 -> `$codex-notifier`
- 高复杂度计划：本地分析 -> `$programming-evidence-search` -> 计划/实现 -> 最终答复 -> `$codex-notifier`

- 低复杂度改码：读至少 `20` 条提交记录 -> 改码 -> `$git-md-micro-commit` -> 最终答复 -> `$codex-notifier`
- 中等复杂度改码：读至少 `20` 条提交记录 -> `$github-kb` -> 改码 -> `$git-md-micro-commit` -> 最终答复 -> `$codex-notifier`
- 高复杂度改码：读至少 `20` 条提交记录 -> `$programming-evidence-search` -> 改码 -> `$git-md-micro-commit` -> 最终答复 -> `$codex-notifier`

## 4. 证据检索约束
### `$github-kb`
- 用于中等复杂度任务的最小必要 GitHub 证据检索
- 默认 `1` 轮，证据不足最多补 `1` 轮
- 同一结论禁止重复同类检索
- 至少产出：简要结论、实现提示、风险提醒
- 若证据不足：输出 `Low confidence + 缺失证据清单`
- 若 GitHub 证据不足以支撑关键判断，升级到 `$programming-evidence-search`

### `$programming-evidence-search`
- 用于高复杂度任务或 `$github-kb` 升级场景
- 默认 `1` 轮，证据不足最多补 `1` 轮
- 至少产出：`decision_summary`、`recommended_approach`、`implementation_hints`、`do_not_do`
- 若证据不足：输出 `Low confidence + 缺失证据清单`
- 若 skill 不可用，必须明确说明“证据检索未完成”

## 5. Python 规则
- 所有 Python / pip 命令必须使用项目内由 `uv` 管理的虚拟环境（默认 `.venv`）
- 禁止使用系统 Python、全局 pip 或非 `uv` 管理环境

## 6. 兜底
- `$git-md-micro-commit` 不可用：手动执行结构化 `git commit`；若非 Git 仓库，在最终答复说明“未完成存档（非 Git 仓库）”
- `$codex-notifier` 不可用：在最终答复说明“通知未发送（skill 不可用）”
- `$github-kb` 不可用：在最终答复说明“GitHub 证据检索未完成（github-kb 不可用）”
- `$programming-evidence-search` 不可用：在最终答复说明“证据检索未完成（programming-evidence-search 不可用）”