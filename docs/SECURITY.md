# 安全说明

## 密钥边界

真实 API key 只能放在：

- 本地 `.env`
- GitHub Repository Secrets

禁止写入代码、README、Issue、PR、Actions 日志、CSV、Excel、截图和提交历史。

当前变量：

```text
ALPHA_VANTAGE_API_KEY
FRED_API_KEY
TIINGO_API_TOKEN
TWELVE_DATA_API_KEY
FMP_API_KEY
MAIL_USERNAME
MAIL_PASSWORD
GITHUB_TOKEN
```

`FMP_API_KEY` 仅供独立能力探测使用。`MAIL_USERNAME` 和 `MAIL_PASSWORD` 仅供生产日报邮件通知使用，其中 `MAIL_PASSWORD` 是 QQ 邮箱 SMTP 授权码，不是邮箱登录密码。`GITHUB_TOKEN` 仅供华为云 FunctionGraph 调用 GitHub `workflow_dispatch` API，建议使用只授权本仓库 Actions read/write 的 fine-grained token。Invesco 持仓接口无需密钥。

## 日志与异常

provider 应通过 `BaseProvider.request_json()` 请求。该实现会：

- 对重试进行统一处理。
- 将 HTTP 429 转换为 `RateLimitError`。
- 对 URL query 中的 `apikey`、`api_key`、`token` 和 `access_token` 脱敏。
- 对已知密钥值进行替换。

仍然不要打印 `os.environ`、`.env` 内容、完整请求参数或原始认证 header。

## GitHub Actions

Secrets 仅在需要的 step 中通过 `${{ secrets.NAME }}` 注入。不要将 secret 提升到整个 workflow 的全局 `env`，也不要把包含认证信息的原始响应上传为 artifact。

生产和辅助工作流拥有 `contents: write`，会向仓库提交生成数据。启用第三方 Action 前应检查来源和版本，并优先固定到可信版本。

## 华为云 FunctionGraph

华为云函数中的 `GITHUB_TOKEN` 只能配置为环境变量或密钥管理项，不得写入函数代码、测试事件、日志、截图或仓库文档。函数日志可以记录 workflow 文件名、ref 和 HTTP 状态码，但不要记录 Authorization header 或 token 值。

## 泄露处置

如果密钥曾被提交、上传、截图或发送到聊天：

1. 立即在数据商后台撤销。
2. 创建新密钥并更新 GitHub Secret 和本地 `.env`。
3. 检查 Actions 日志、artifact、Issue、PR 和 Git 历史。
4. 必要时清理历史，但不要把“删除文件”误认为旧提交中的密钥已经消失。
