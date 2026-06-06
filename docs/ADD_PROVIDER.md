# 新增数据 Provider

当前 provider 配置不是动态插件加载。新增 provider 时需要同时修改代码、配置、流水线和测试。

## 1. 实现 Provider

在 `src/qqq_tracker/providers/` 新建模块，继承 `BaseProvider`，返回 `ProviderResult`：

```python
from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


class PolygonProvider(BaseProvider):
    provider_name = "polygon"

    def daily_prices(self, symbol: str) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("daily_prices")
        return ProviderResult(self.provider_name, True, pd.DataFrame(), "ok")
```

通过 `request_json()` 发起请求，以复用重试、429 识别和密钥脱敏。不要在异常、URL 或日志中暴露 API key。

## 2. 导出 Provider

在 `src/qqq_tracker/providers/__init__.py` 中导入，并加入 `__all__`。

## 3. 注册配置

在 `config/sources.yml` 中增加：

```yaml
providers:
  polygon:
    enabled: true
    class_path: qqq_tracker.providers.polygon.PolygonProvider
    api_key_env: POLYGON_API_KEY
    base_url: https://api.polygon.io
```

注意：当前 `class_path` 是注册信息，不会自动实例化 provider。

如有调用额度，在 `config/api_limits.yml` 中增加预算、保留额度、节流和遇到 429 后的停止策略。

## 4. 接入流水线

根据用途修改相应入口：

- 生产日报：`make_providers()` 和 `run_daily()`。
- 历史缓存：`pipeline/cache_backfill.py`。
- 能力探测：`pipeline/capability_probe.py`。

新增调用时应同步写入：

- 原始或标准化数据。
- `data_quality.csv`。
- `api_usage.csv`。
- manifest 的文件索引或摘要。

## 5. 输出契约

价格日线统一字段：

```text
date,symbol,open,high,low,close,adjusted_close,volume,source
```

所有输出必须保留实际 provider，并对缺失、限速和回退进行客观记录。

## 6. 测试与文档

至少增加：

- 响应规范化测试。
- 缺失 key、API 错误和 429 测试。
- 回退、缓存合并或调用预算测试。
- 输出字段和 manifest 测试。

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

最后同步 README、数据模型、Secrets 和工作流说明。
