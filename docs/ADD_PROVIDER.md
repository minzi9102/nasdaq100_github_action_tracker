# 如何新增数据接口

本项目使用 Provider 结构。新增接口分三步。

## 1. 新建 Provider 文件

例如新增 Polygon：

```text
src/qqq_tracker/providers/polygon.py
```

参考结构：

```python
from __future__ import annotations

import pandas as pd

from .base import BaseProvider, ProviderResult


class PolygonProvider(BaseProvider):
    provider_name = "polygon"

    def daily_prices(self, symbol: str) -> ProviderResult:
        if not self.available:
            return self.unavailable_result("daily_prices")
        # 调用接口，整理成DataFrame
        return ProviderResult(self.provider_name, True, pd.DataFrame(), "ok")
```

## 2. 注册到 config/sources.yml

```yaml
providers:
  polygon:
    enabled: true
    class_path: qqq_tracker.providers.polygon.PolygonProvider
    api_key_env: POLYGON_API_KEY
    base_url: https://api.polygon.io
```

## 3. 在 daily_run.py 中调用

在 `make_providers()` 中初始化，在 `run_daily()` 中写入输出。

## 输出字段要求

价格类接口建议统一输出：

| 字段 | 说明 |
|---|---|
| date | 交易日期 |
| symbol | 代码 |
| open | 开盘价 |
| high | 最高价 |
| low | 最低价 |
| close | 收盘价 |
| adjusted_close | 复权收盘价 |
| volume | 成交量 |
| source | 数据源 |

宏观类接口建议统一输出：

| 字段 | 说明 |
|---|---|
| date | 日期 |
| series_id | 序列代码 |
| value | 数值 |
| source | 数据源 |
