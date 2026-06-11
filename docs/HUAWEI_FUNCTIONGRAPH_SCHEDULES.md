# 华为云 FunctionGraph 定时触发说明

## 1. 目标

GitHub Actions 的 `schedule` 事件不保证准点触发。当前生产日报和 Tiingo 缓存回填改由华为云 FunctionGraph 定时触发，并通过 GitHub `workflow_dispatch` API 启动对应工作流。

执行仍在 GitHub Actions 中完成；华为云只负责准点发送触发请求。

## 2. 函数清单

| 华为云函数 | GitHub workflow | 触发时间（北京时间） | 用途 |
| --- | --- | --- | --- |
| `github-daily-target-refresh-dispatch` | `daily-target-refresh.yml` | 周二至周五 `10:45` | 刷新 QQQ 目标日期，供 Tiingo 回填读取 |
| `github-daily-tracker-dispatch` | `daily-tracker.yml` | 周二至周六 `18:30` | 整理已回填数据，生成 Nasdaq-100 / QQQ 日报并发送邮件 |
| `github-tiingo-cache-backfill-dispatch` | `tiingo_cache_backfill.yml` | 周一至周五 `11:00`、`13:10`、`15:20` | 维护 Tiingo 历史价格缓存 |

## 3. 函数代码

三个函数使用相同 Python 代码，通过环境变量决定触发哪个 workflow。

```python
import json
import os
import urllib.error
import urllib.request


def handler(event, context):
    owner = os.environ.get("GITHUB_OWNER", "minzi9102")
    repo = os.environ.get("GITHUB_REPO", "nasdaq100_github_action_tracker")
    workflow = os.environ.get("GITHUB_WORKFLOW", "daily-tracker.yml")
    ref = os.environ.get("GITHUB_REF", "main")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: GITHUB_TOKEN")

    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches"
    payload = json.dumps({"ref": ref}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {
                "ok": resp.status == 204,
                "status": resp.status,
                "message": "GitHub workflow_dispatch sent",
                "workflow": workflow,
                "ref": ref,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API failed: status={exc.code}, body={body}")
```

## 4. 环境变量

不要把真实 token 写入仓库或日志。`GITHUB_TOKEN` 应配置在华为云函数环境变量或密钥管理能力中。

### `github-daily-target-refresh-dispatch`

```text
GITHUB_OWNER=minzi9102
GITHUB_REPO=nasdaq100_github_action_tracker
GITHUB_WORKFLOW=daily-target-refresh.yml
GITHUB_REF=main
GITHUB_TOKEN=<GitHub token>
```

### `github-daily-tracker-dispatch`

```text
GITHUB_OWNER=minzi9102
GITHUB_REPO=nasdaq100_github_action_tracker
GITHUB_WORKFLOW=daily-tracker.yml
GITHUB_REF=main
GITHUB_TOKEN=<GitHub token>
```

### `github-tiingo-cache-backfill-dispatch`

```text
GITHUB_OWNER=minzi9102
GITHUB_REPO=nasdaq100_github_action_tracker
GITHUB_WORKFLOW=tiingo_cache_backfill.yml
GITHUB_REF=main
GITHUB_TOKEN=<GitHub token>
```

GitHub token 建议使用 Fine-grained personal access token，仅授权本仓库，并授予 Actions read/write 权限。

## 5. 定时触发器

华为云 FunctionGraph 定时触发器使用 `CRON_TZ=Asia/Shanghai` 固定北京时间。

### 目标日刷新

```text
daily-target-refresh-1045
CRON_TZ=Asia/Shanghai 0 45 10 ? * TUE-FRI
```

### 日报

```text
daily-tracker-1830
CRON_TZ=Asia/Shanghai 0 30 18 ? * TUE-SAT
```

### Tiingo 缓存回填

```text
tiingo-backfill-1100
CRON_TZ=Asia/Shanghai 0 0 11 ? * MON-FRI

tiingo-backfill-1310
CRON_TZ=Asia/Shanghai 0 10 13 ? * MON-FRI

tiingo-backfill-1520
CRON_TZ=Asia/Shanghai 0 20 15 ? * MON-FRI
```

## 6. 验证与故障排查

手动测试函数时，测试事件可以使用：

```json
{
  "manual": true
}
```

成功时函数返回 HTTP `204` 对应的结果，并且 GitHub Actions 页面会出现 `workflow_dispatch` 运行记录。

常见错误：

- `Missing required environment variable: GITHUB_TOKEN`：华为云函数未配置 `GITHUB_TOKEN`。
- GitHub API `401` 或 `403`：token 无效、过期或缺少 Actions write 权限。
- GitHub API `404`：仓库名、workflow 文件名或 token 仓库授权范围不正确。
- workflow 在 `git push` 阶段失败：检查工作流中的 rebase retry 日志；真实文件冲突仍需人工处理。

## 7. 维护规则

- `daily-target-refresh.yml`、`daily-tracker.yml` 和 `tiingo_cache_backfill.yml` 保留 `workflow_dispatch`，不再保留 GitHub `schedule`。
- 若更换函数名、触发时间、workflow 文件名或 token 权限，应同步更新本文档。
- 不要在截图、Issue、提交信息、Actions 日志或文档中暴露真实 token。
