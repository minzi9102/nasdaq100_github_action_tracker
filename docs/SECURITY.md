# 安全说明

## 不要提交 API key

严禁把真实 API key 写入：

- 代码文件
- README
- GitHub issue
- GitHub Actions 日志
- Excel 报表
- CSV 输出
- 提交历史

本项目通过环境变量读取密钥。本地使用 `.env`，GitHub Actions 使用 Repository Secrets。

## 已泄露密钥怎么办

如果密钥曾经被上传、截图、发送到聊天或提交到仓库，应立即去数据商后台撤销并重新生成。

## GitHub Secrets

GitHub Secrets 会在 Actions workflow 中通过 `${{ secrets.NAME }}` 引用。只有明确在 workflow 中引用的 secret 才会暴露给该 workflow。

## 日志注意事项

不要 `print(os.environ)`，不要打印完整请求 URL，因为有些接口把 API key 放在 query string 里。
