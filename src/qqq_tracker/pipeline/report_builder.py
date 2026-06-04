from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd


def build_ai_input(price_summary: pd.DataFrame, macro_summary: pd.DataFrame, fmp_summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if not price_summary.empty:
        for _, r in price_summary.iterrows():
            rows.extend([
                ["价格", f"{r['symbol']} 20日收益率", r.get("return_20d"), ">=0绿色，-5%到0黄色，<-5%红色", r.get("signal_return_20d"), r.get("date"), "价格趋势", r.get("source")],
                ["价格", f"{r['symbol']} 60日收益率", r.get("return_60d"), ">=0绿色，-10%到0黄色，<-10%红色", r.get("signal_return_60d"), r.get("date"), "中期趋势", r.get("source")],
                ["风险", f"{r['symbol']} 20日年化波动率", r.get("vol_20d"), "<25%绿色，25%-35%黄色，>35%红色", r.get("signal_vol_20d"), r.get("date"), "波动风险", r.get("source")],
                ["风险", f"{r['symbol']} 当前回撤", r.get("current_drawdown"), ">-10%绿色，-10%到-15%黄色，<-15%红色", r.get("signal_drawdown"), r.get("date"), "回撤风险", r.get("source")],
            ])
    if not macro_summary.empty:
        for _, r in macro_summary.iterrows():
            rows.append(["宏观", r.get("name") or r.get("series_id"), r.get("latest_value"), "用于方向判断", r.get("status", "信息"), r.get("latest_date"), "宏观环境", "FRED"])
    if not fmp_summary.empty:
        available_ratio = fmp_summary["ok"].mean() if "ok" in fmp_summary.columns and len(fmp_summary) else None
        status = "绿色" if available_ratio and available_ratio >= 0.8 else "黄色" if available_ratio and available_ratio >= 0.5 else "灰色"
        rows.append(["基本面", "FMP报价可用率", available_ratio, ">=80%绿色，50%-80%黄色，<50%灰色", status, pd.Timestamp.today().date().isoformat(), "基本面数据质量", "FMP"])
    return pd.DataFrame(rows, columns=["指标类别", "指标名称", "当前值", "阈值/比较基准", "状态", "数据日期", "方向/解读", "来源"])


def build_analysis_summary(ai_input: pd.DataFrame) -> pd.DataFrame:
    if ai_input.empty:
        return pd.DataFrame([["综合风险状态", "灰色", "AI输入层为空，无法判断"]], columns=["项目", "结果", "说明"])
    counts = ai_input["状态"].value_counts().to_dict()
    red = counts.get("红色", 0)
    yellow = counts.get("黄色", 0)
    gray = counts.get("灰色", 0)
    if red >= 2:
        overall = "红色"
        action = "暂停新增，触发复核。"
    elif red == 1 or yellow >= 3:
        overall = "黄色"
        action = "小额定投或持有观察，不追涨。"
    elif gray > 0:
        overall = "黄色"
        action = "核心数据基本可用，但需保留数据质量限制。"
    else:
        overall = "绿色"
        action = "可按计划执行，但仍需遵守仓位上限。"
    return pd.DataFrame([
        ["综合风险状态", overall, action],
        ["数据质量", "较完整" if gray == 0 else "基本可用", f"绿色{counts.get('绿色',0)}项，黄色{yellow}项，红色{red}项，灰色{gray}项。"],
        ["是否允许强化买入", "否" if overall != "绿色" else "仍需仓位确认", "本项目不输出无条件买入；所有动作受仓位和风险约束。"],
    ], columns=["项目", "结果", "说明"])


def build_markdown_summary(as_of: str, ai_input: pd.DataFrame, analysis: pd.DataFrame, manifest_path: str) -> str:
    lines = [
        f"# Nasdaq-100 / QQQ Daily Tracker Summary - {as_of}",
        "",
        "本文件供 ChatGPT 或人工复核读取。不要在本文件中保存 API key。",
        "",
        "## 分析判断层",
        "",
    ]
    for _, r in analysis.iterrows():
        lines.append(f"- **{r['项目']}**：{r['结果']}。{r['说明']}")
    lines += ["", "## AI输入层", ""]
    for _, r in ai_input.iterrows():
        lines.append(f"- {r['指标类别']} / {r['指标名称']}：{r['当前值']}，状态={r['状态']}，来源={r['来源']}。")
    lines += ["", "## 文件清单", "", f"见 `{manifest_path}`。"]
    return "\n".join(lines) + "\n"


def write_excel(path: Path, sheets: Dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        wb = writer.book
        header_fmt = wb.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1, "align": "center"})
        pct_fmt = wb.add_format({"num_format": "0.00%"})
        for sheet_name, df in sheets.items():
            name = sheet_name[:31]
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(1, len(df)), max(0, len(df.columns) - 1))
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, header_fmt)
                width = min(max(len(str(col)) + 4, 12), 42)
                ws.set_column(i, i, width)
                if any(k in str(col) for k in ["return", "vol", "drawdown", "收益", "波动", "回撤", "率"]):
                    ws.set_column(i, i, width, pct_fmt)
