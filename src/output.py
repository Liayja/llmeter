"""
终端打印 + 结果保存
"""
import json
import csv
import statistics
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    RICH = True
except ImportError:
    RICH = False 

def print_report(m: dict, model: str, engine_type: str):
    """打印压测报告到终端"""
    title = f"📊 压测结果 — {engine_type}/{model}"

    if RICH:
        _print_rich(m, title)
    else:
        _print_plain(m, title)

def _print_rich(m: dict, title: str):
    console = Console()
    table = Table(title=title)
    table.add_column("指标", style="bold cyan")
    table.add_column("值", justify="right")
    table.add_row("总请求", str(m["total"]))
    table.add_row("✅ 成功", f"[green]{m['success']}[/green]")
    table.add_row("❌ 失败", f"[red]{m['fail']}[/red]")
    if m.get("incomplete") or m.get("error_count"):
        table.add_row("　├ 未完成(超时/中断)", str(m.get("incomplete", 0)))
        table.add_row("　└ HTTP错误(4xx/5xx)", str(m.get("error_count", 0)))
    if m.get("retried"):
        table.add_row("↻ 重试", f"[yellow]{m['retried']}[/yellow] 个请求至少重试过 1 次")
    table.add_row("成功率", f"{m['success_rate']:.1f}%")
    table.add_row("QPS", f"{m['qps']:.2f} req/s")
    if m.get("total_output_tokens", 0) > 0:
        table.add_row("Token 吞吐", f"{m['tps']:.1f} tok/s")
    if "cache_hit_rate" in m:
        table.add_row("缓存命中率", f"{m['cache_hit_rate']:.1f}%")
    table.add_section()
    table.add_row("[bold]延迟 (Latency)[/bold]", "")
    for label, key in [("Avg", "latency_avg"), ("P50", "latency_p50"),
                        ("P75", "latency_p75"), ("P90", "latency_p90"),
                        ("P95", "latency_p95"), ("P98", "latency_p98"),
                        ("P99", "latency_p99"), ("P99.9", "latency_p999"),
                        ("Min", "latency_min"), ("Max", "latency_max")]:
        table.add_row(f"  {label}", f"{m[key]:.3f}s")
    if m.get("ttft_avg") is not None:
        table.add_section()
        table.add_row("[bold]首 Token 延迟 (TTFT)[/bold]", "")
        for label, key in [("Avg", "ttft_avg"), ("P50", "ttft_p50"), ("P90", "ttft_p90"),
                            ("P95", "ttft_p95"), ("P99", "ttft_p99"), ("P99.9", "ttft_p999")]:
            table.add_row(f"  {label}", f"{m[key]:.3f}s")
    if m.get("tpot_avg") is not None:
        table.add_section()
        _tpot_note = {"stream": "流式口径（剔除 prefill）",
                       "nonstream": "非流式口径（含 prefill）"}.get(m.get("tpot_mode", ""), "")
        table.add_row("[bold]每 Token 耗时 (TPOT)[/bold]", _tpot_note)
        for label, key in [("Avg", "tpot_avg"), ("P50", "tpot_p50"), ("P95", "tpot_p95")]:
            table.add_row(f"  {label}", f"{m[key]*1000:.1f} ms")
    if m.get("itl_avg") is not None:
        table.add_section()
        table.add_row("[bold]Token 间隔 (ITL)[/bold]", "")
        for label, key in [("Avg", "itl_avg"), ("P50", "itl_p50"), ("P95", "itl_p95"), ("Max", "itl_max")]:
            table.add_row(f"  {label}", f"{m[key]*1000:.1f} ms")
    if m.get("concurrency_peak") is not None:
        table.add_section()
        if m.get("mode") == "open":
            table.add_row("[bold]发压模式[/bold]", f"开环 · 目标 {m.get('qps_target')} QPS")
            table.add_row("  在途峰值(积压)", f"{m['concurrency_peak']}")
            if m.get("dispatch_phase") is not None:
                table.add_row("  两阶段耗时", f"发车 {m['dispatch_phase']:.1f}s + 排空 {m['drain_phase']:.1f}s")
                table.add_row("  实际发车速率", f"{m.get('qps_dispatch', 0):.2f} req/s")
        else:
            table.add_row("[bold]并发[/bold]", f"{m['concurrency_peak']} (目标 {m.get('concurrency_target', '?')})")
    if m["errors"]:
        table.add_section()
        table.add_row("[bold red]错误摘要[/bold red]", "")
        for i, e in enumerate(m["errors"][:5]):
            table.add_row(f"  #{i+1}", f"[red]{e['msg'][:100]}[/red]")
    console.print(table)

def _print_plain(m: dict, title: str):
    """纯文本输出"""
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")
    print(f" 总请求: {m['total']}  |  ✅ 成功: {m['success']}  |  ❌ 失败: {m['fail']}")
    if m.get("incomplete") or m.get("error_count"):
        print(f"   ├ 未完成(超时/中断): {m.get('incomplete', 0)}  └ HTTP错误(4xx/5xx): {m.get('error_count', 0)}")
    if m.get("retried"):
        print(f"  ↻ 重试: {m['retried']} 个请求至少重试过 1 次（成功率/延迟含重试结果）")
    print(f" 成功率: {m['success_rate']:.1f}%  |  QPS: {m['qps']:.2f} req/s")
    if m.get("total_output_tokens", 0) > 0:
        print(f" Token 吞吐: {m['tps']:.1f} tok/s")
    if "cache_hit_rate" in m:
        print(f" 缓存命中率: {m['cache_hit_rate']:.1f}%")
    print(f" 延迟 — Avg: {m['latency_avg']:.3f}s  P50: {m['latency_p50']:.3f}s  P75: {m['latency_p75']:.3f}s  "
          f"P90: {m['latency_p90']:.3f}s  P95: {m['latency_p95']:.3f}s  P98: {m['latency_p98']:.3f}s  "
          f"P99: {m['latency_p99']:.3f}s  P99.9: {m['latency_p999']:.3f}s")
    if m.get("ttft_avg") is not None:
        print(f" TTFT — Avg: {m['ttft_avg']:.3f}s  P50: {m['ttft_p50']:.3f}s  P90: {m['ttft_p90']:.3f}s  "
              f"P95: {m['ttft_p95']:.3f}s  P99: {m['ttft_p99']:.3f}s")
    if m.get("tpot_avg") is not None:
        _tpot_note = {"stream": "（流式，剔除 prefill）",
                      "nonstream": "（非流式，含 prefill）"}.get(m.get("tpot_mode", ""), "")
        print(f" TPOT{_tpot_note} — Avg: {m['tpot_avg']*1000:.1f}ms  P50: {m['tpot_p50']*1000:.1f}ms  "
              f"P95: {m['tpot_p95']*1000:.1f}ms")
    if m.get("concurrency_peak") is not None:
        if m.get("mode") == "open":
            print(f" 发压模式: 开环 · 目标 {m.get('qps_target')} QPS  |  在途峰值(积压): {m['concurrency_peak']}")
            if m.get("dispatch_phase") is not None:
                print(f"  两阶段耗时: 发车 {m['dispatch_phase']:.1f}s + 排空 {m['drain_phase']:.1f}s  |  "
                      f"实际发车速率 {m.get('qps_dispatch', 0):.2f} req/s")
        else:
            print(f" 并发: 峰值 {m['concurrency_peak']} / 目标 {m.get('concurrency_target', '?')}")
    if m["errors"]:
        print(f"\n 错误摘要:")
        for i, e in enumerate(m["errors"][:5]):
            print(f"   #{i+1}: {e['msg'][:120]}")


def save_results(metrics: dict, raw_results: list, cfg: dict, engine_type: str):
    """保存结果到 JSON 文件"""
    now = datetime.now()
    date_dir = Path(cfg["output_dir"]) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_model = cfg["model"].replace("/", "_").replace(":", "_")
    # 文件名反映发压模式：开环用 qps(+时长)，闭环用并发数，避免开环时 c10 默认值误导。
    if cfg.get("qps"):
        qps_tag = f"qps{cfg['qps']:g}"
        load_tag = f"{qps_tag}_d{cfg['duration']}" if cfg.get("duration") else qps_tag
    else:
        load_tag = f"c{cfg['concurrency']}"
    filename = f"{safe_model}_{load_tag}_n{cfg['total_requests']}_{now.strftime('%H%M%S')}.json"
    filepath = date_dir / filename
    output = {
        "meta": {
            "engine": engine_type,
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            # 实际请求地址：排查厂商自定义路径（如智谱 /api/paas/v4/chat/completions）时最有用
            "endpoint": cfg.get("endpoint"),
            "chat_url": cfg.get("chat_url"),
            # 发压模式自描述：开环记 qps/duration(并发由服务器决定，无设定值)，闭环记 concurrency。
            "mode": "open" if cfg.get("qps") else "closed",
            "qps": cfg.get("qps") if cfg.get("qps") else None,
            "duration": cfg.get("duration") if cfg.get("qps") else None,
            "concurrency": None if cfg.get("qps") else cfg["concurrency"],
            "total_requests": cfg["total_requests"],
            "stream": cfg.get("stream"),
            "warmup": cfg.get("warmup"),
            "retries": cfg.get("retries"),
            # 关键请求参数落盘，便于事后核对"配置是否真的生效"
            "max_tokens": cfg.get("max_tokens"),
            "temperature": cfg.get("temperature"),
            "extra_params": cfg.get("extra_params"),
            "prompt": cfg["prompt"][:200],
            "timestamp": now.isoformat(),
        },
        "summary": metrics,
        "details": raw_results,
    }
    filepath.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 结果已保存: {filepath}")


# ═══════════════════════════════════════════════════
# 多场景对比报告
# ═══════════════════════════════════════════════════

# 对比表列定义：(表头, 取值函数, 格式化函数)
def _concurrency_cell(r: dict) -> str:
    """并发列展示：
    - 闭环: 目标并发数(限流上限)，即 concurrency。
    - 开环: 无并发上限，展示实测「在途峰值」并加 ~ 前缀标明是积压水位而非设定值，
            避免被误读成“限了 N 并发”。
    """
    if r.get("mode") == "open":
        peak = r.get("concurrency_peak")
        return f"~{peak}" if peak is not None else "—"
    return str(r.get("concurrency"))


_COMPARE_COLS = [
    ("场景",      lambda r: r["name"],                       str),
    ("模式",      lambda r: "流式" if r.get("tpot_mode") == "stream"
                   else ("非流式" if r.get("tpot_mode") == "nonstream" else "—"), str),
    ("目标QPS",   lambda r: r.get("qps_target"),             lambda v: f"{v:g}" if v is not None else "—"),
    ("并发",      _concurrency_cell,                         str),
    ("请求数",    lambda r: r["total"],                      str),
    ("入Token",   lambda r: r.get("avg_input_tokens"),       lambda v: str(v) if v is not None else "—"),
    ("出Token",   lambda r: r.get("avg_output_tokens"),      lambda v: str(v) if v is not None else "—"),
    ("总Token",   lambda r: r.get("total_tokens"),             lambda v: f"{v:,}" if v is not None else "—"),
    ("缓存命中",  lambda r: r.get("cache_hit_rate"),          lambda v: f"{v:.1f}%" if v is not None else "—"),
    ("成功率",    lambda r: r["success_rate"],               lambda v: f"{v:.1f}%"),
    ("重试",      lambda r: r.get("retried", 0),             str),
    ("未完成",    lambda r: r.get("incomplete", 0),          str),
    ("HTTP错误",  lambda r: r.get("error_count", 0),         str),
    ("QPS",       lambda r: r["qps"],                        lambda v: f"{v:.2f}"),
    ("TPS",       lambda r: r["tps"],                        lambda v: f"{v:.1f}"),
    ("延迟Avg",   lambda r: r["latency_avg"],                lambda v: f"{v:.3f}s"),
    ("延迟Std",   lambda r: r.get("latency_std"),             lambda v: f"{v:.3f}s" if v else "—"),
    ("延迟P50",   lambda r: r["latency_p50"],                lambda v: f"{v:.3f}s"),
    ("延迟P95",   lambda r: r["latency_p95"],                lambda v: f"{v:.3f}s"),
    ("延迟P99",   lambda r: r["latency_p99"],                lambda v: f"{v:.3f}s"),
    ("TTFT Avg",  lambda r: r.get("ttft_avg"),               lambda v: f"{v:.3f}s" if v is not None else "—"),
    ("TTFT P95",  lambda r: r.get("ttft_p95"),               lambda v: f"{v:.3f}s" if v is not None else "—"),
    ("TPOT Avg",  lambda r: r.get("tpot_avg"),               lambda v: f"{v*1000:.1f}ms" if v is not None else "—"),
    ("TPOT P95",  lambda r: r.get("tpot_p95"),               lambda v: f"{v*1000:.1f}ms" if v is not None else "—"),
    ("长尾比",    lambda r: (r["latency_p95"] / r["latency_p50"]) if r["latency_p50"] else 0,
                  lambda v: f"{v:.2f}" if v != 0 else "—"),
]


def _row_from_result(name: str, metrics: dict, concurrency: int) -> dict:
    """把单场景的 metrics 摊平成对比表的一行"""
    return {"name": name, "concurrency": concurrency, **metrics}


# 跨模型对比时最左侧的「模型」列
_MODEL_COL = ("模型", lambda r: r.get("model_label") or r.get("_model") or "-", str)


def _cols(show_model: bool):
    """返回对比表列定义；show_model=True 时最左加一列模型。"""
    return ([_MODEL_COL] + _COMPARE_COLS) if show_model else _COMPARE_COLS


def print_comparison(rows: list, model: str, *, show_model: bool = False):
    """打印多场景对比表到终端"""
    title = f"📊 多场景压测对比 — {model}"
    cols = _cols(show_model)

    if RICH:
        console = Console()
        table = Table(title=title, show_lines=True)
        for header, _, _ in cols:
            table.add_column(header, justify="right" if header not in ("场景", "模型") else "left",
                             style="cyan" if header in ("场景", "模型") else None)
        for r in rows:
            cells = []
            for _, getter, fmt in cols:
                cells.append(fmt(getter(r)))
            # 成功率 < 99% 整行标红告警
            if r["success_rate"] < 99:
                cells = [f"[red]{c}[/red]" for c in cells]
            table.add_row(*cells)
        console.print(table)
    else:
        print(f"\n{'='*120}")
        print(f" {title}")
        print(f"{'='*120}")
        headers = [h for h, _, _ in cols]
        # 计算每列最大宽度：对齐表头和所有行
        all_cells = [headers[:]]  # 第一行是表头
        for r in rows:
            all_cells.append([fmt(getter(r)) for _, getter, fmt in cols])
        col_widths = []
        for ci in range(len(headers)):
            max_w = max(len(str(row[ci])) for row in all_cells)
            col_widths.append(max(max_w, len(headers[ci])))
        # 打印表头
        print(" | ".join(f"{headers[i]:>{col_widths[i]}}" for i in range(len(headers))))
        print("-+-".join("-" * w for w in col_widths))
        for row_cells in all_cells[1:]:
            print(" | ".join(f"{str(row_cells[i]):>{col_widths[i]}}" for i in range(len(row_cells))))


def save_comparison(rows: list, model: str, output_dir: str, *, show_model: bool = False):
    """保存对比报告为 Markdown + CSV"""
    now = datetime.now()
    date_dir = Path(output_dir) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_").replace(" — ", "_")
    stem = f"comparison_{safe_model}_{now.strftime('%H%M%S')}"

    cols = _cols(show_model)
    headers = [h for h, _, _ in cols]

    # ── Markdown ──────────────────────────────────────────
    md_lines = [
        f"# 多场景压测对比 — {model}",
        "",
        f"> 生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for r in rows:
        cells = [fmt(getter(r)) for _, getter, fmt in cols]
        md_lines.append("| " + " | ".join(cells) + " |")
    md_path = date_dir / f"{stem}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # ── CSV ───────────────────────────────────────────────
    csv_path = date_dir / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in rows:
            # CSV 写原始数值，方便 Excel 计算/画图
            writer.writerow([getter(r) for _, getter, _ in cols])

    print(f"\n💾 对比报告已保存:")
    print(f"   📄 Markdown: {md_path}")
    print(f"   📊 CSV:      {csv_path}")


# ═══════════════════════════════════════════════════
# Excel 导出（面向非技术人员的格式化报表）
# ═══════════════════════════════════════════════════

# 指标的中文说明，用于 Excel 里的"指标说明" sheet
_METRIC_HELP = {
    "模型": "被压测的模型（跨模型对比时用于区分不同模型的同场景结果）",
    "场景": "测试场景的名称",
    "目标QPS": "开环模式设定的恒定到达率(req/s)；闭环场景此列为空",
    "并发": "闭环=设定的并发上限；开环以 ~N 表示实测在途峰值(积压水位，非设定值)",
    "请求数": "总共发送了多少次请求",
    "入Token": "每次请求平均消耗的输入 Token 数（输入越长数值越大）",
    "出Token": "每次成功请求平均生成的输出 Token 数",
    "总Token": "该场景总共消耗的 Token 数（≈ 花了多少额度）",
    "缓存命中": "缓存命中率：命中的缓存 token 占输入 token 的比例（0~100%）。0% 表示未命中或未启用"
                "前缀缓存。细分的 token 加权/每请求口径见 JSON summary 的 cache_* 字段",
    "成功率": "请求成功的比例。注意：存在重试时，重试后成功的请求会计入成功，成功率会偏乐观；参考「重试」列",
    "重试": "至少重试过一次的请求数（含最终成功与失败）。>0 时说明发生过限流/瞬时错误，限流可能被重试掩盖",
    "未完成": "超时或连接中断的请求数（未到达服务端或未拿到完整响应，status_code=0）",
    "HTTP错误": "服务端明确返回 4xx/5xx 的请求数（如 429 限流、500 服务端错误）",
    "模式": "流式 / 非流式。TPOT 口径随模式不同（流式剔除 prefill、非流式含 prefill），跨模式比较 TTFT/TPOT 无效",
    "QPS (req/s)": "每秒处理的请求数（Queries Per Second），越高越好",
    "TPS (tok/s)": "每秒生成的 Token 数（Tokens Per Second），反映吞吐能力",
    "延迟Avg (s)": "所有成功请求的平均响应时间，单位秒",
    "延迟Std (s)": "响应时间的标准差，越小表示表现越稳定",
    "延迟P50 (s)": "50% 的请求在这个时间内完成（中位数），反映典型用户体验",
    "延迟P95 (s)": "95% 的请求在这个时间内完成，反映大多数用户的最差体验",
    "延迟P99 (s)": "99% 的请求在这个时间内完成，反映极端情况",
    "TTFT Avg (s)": "首 Token 平均时间（从发送请求到收到第一个字），反映模型思考速度；仅流式模式有值",
    "TTFT P95 (s)": "95% 请求的首 Token 时间",
    "TPOT Avg (ms)": "每个输出 Token 的平均生成时间；流式口径=(latency-TTFT)/(out-1) 剔除 prefill，非流式口径=latency/out 含 prefill",
    "TPOT P95 (ms)": "95% 请求的每 Token 生成时间（口径同上）",
    "长尾比 (P95/P50)": "P95延迟÷P50延迟，越接近1.0表示延迟越均匀，>2.0表示存在明显抖动",
}

# ═══════════════════════════════════════════════════
# Excel 列块定义
# ═══════════════════════════════════════════════════

# 每个 Excel 列: (header, getter, raw_for_cell, excel_numfmt, higher_is_better, base_header)
#   header: 显示用的表头（含单位）
#   base_header: 原始列名（用于图表匹配等内部逻辑）
#   raw_for_cell: 写入单元格的原始值（数字或字符串）
#   excel_numfmt: Excel 数字格式字符串，None 表示字符串列
#   higher_is_better: True=越大越好(绿), False=越小越好(绿), None=不着色阶
def _build_excel_col(col_def):
    """从 _COMPARE_COLS 的 (header, getter, fmt) 构建 Excel 列定义。
    列头会追加单位，与终端输出用的 _COMPARE_COLS 头区分开。"""
    header, getter, _fmt = col_def

    # 列头 → (Excel 表头, 单位)
    _UNIT_MAP = {
        "场景":      ("场景", ""),
        "模型":      ("模型", ""),
        "模式":      ("模式", ""),
        "目标QPS":   ("目标QPS", "req/s"),
        "并发":      ("并发", ""),
        "请求数":    ("请求数", ""),
        "入Token":   ("入Token", ""),
        "出Token":   ("出Token", ""),
        "总Token":   ("总Token", ""),
        "缓存命中":  ("缓存命中", "%"),
        "成功率":    ("成功率", ""),
        "重试":      ("重试", "个"),
        "未完成":    ("未完成", "个"),
        "HTTP错误":  ("HTTP错误", "个"),
        "QPS":       ("QPS", "req/s"),
        "TPS":       ("TPS", "tok/s"),
        "延迟Avg":   ("延迟Avg", "s"),
        "延迟Std":   ("延迟Std", "s"),
        "延迟P50":   ("延迟P50", "s"),
        "延迟P95":   ("延迟P95", "s"),
        "延迟P99":   ("延迟P99", "s"),
        "TTFT Avg":  ("TTFT Avg", "s"),
        "TTFT P95":  ("TTFT P95", "s"),
        "TPOT Avg":  ("TPOT Avg", "ms"),
        "TPOT P95":  ("TPOT P95", "ms"),
        "长尾比":    ("长尾比", "P95/P50"),
    }
    excel_header, unit = _UNIT_MAP.get(header, (header, ""))
    # 表头格式: "指标名" 或 "指标名\n(单位)"
    display_header = f"{excel_header}\n({unit})" if unit else excel_header

    if header in ("场景", "模型", "目标QPS", "并发", "请求数"):
        # 目标QPS 用原始格式函数保留 "—"/数字展示；其余转字符串
        raw_fn = (lambda r: _fmt(getter(r))) if header == "目标QPS" else (lambda r: str(getter(r)))
        return (display_header, getter, raw_fn, None, None, header)
    if header == "成功率":
        return (display_header, getter, getter, '0.0"%"', True, header)
    if header == "缓存命中":
        # 命中率口径依赖具体请求模式，不做跨场景最优/色阶标注，仅展示数值；
        # API 未返回 cached_tokens 时显示 "—"，避免空白让人误以为漏了指标
        return (display_header, getter,
                lambda r: getter(r) if getter(r) is not None else "—",
                '0.0"%"', None, header)
    if header in ("QPS", "TPS"):
        return (display_header, getter, getter, "0.00" if header == "QPS" else "0.0", True, header)
    if header in ("延迟Avg", "延迟Std", "延迟P50", "延迟P95", "延迟P99"):
        return (display_header, getter, getter, "0.000", False, header)
    if header in ("TTFT Avg", "TTFT P95"):
        return (display_header, getter, getter, "0.000", False, header)
    if header in ("TPOT Avg", "TPOT P95"):
        return (display_header, getter, lambda r: (getter(r) * 1000) if getter(r) is not None else None, "0.0", False, header)
    if header == "长尾比":
        return (display_header, getter, getter, "0.00", False, header)
    if header in ("入Token", "出Token", "总Token", "重试", "未完成", "HTTP错误"):
        return (display_header, getter, lambda r: getter(r) if getter(r) is not None else None, "#,##0", None, header)
    return (display_header, getter, lambda r: getter(r), None, None, header)


def _excel_blocks(show_model: bool):
    """返回 Excel 的三个逻辑列块，每个块 = (块名, 块颜色, [列定义])。

    按列头名选列(而非位置下标)，这样 _COMPARE_COLS 增删列时不会错位。
    """
    compare = {c[0]: _build_excel_col(c) for c in _COMPARE_COLS}

    def pick(*headers):
        return [compare[h] for h in headers if h in compare]

    blocks = [
        ("基本信息",   "2F5496", pick("场景", "模式", "目标QPS", "并发", "请求数",
                                      "入Token", "出Token", "总Token", "缓存命中")),
        ("吞吐 & 延迟", "2E7D32", pick("成功率", "重试", "未完成", "HTTP错误",
                                      "QPS", "TPS",
                                      "延迟Avg", "延迟Std", "延迟P50",
                                      "延迟P95", "延迟P99", "长尾比")),
        ("流式指标",   "E65100", pick("TTFT Avg", "TTFT P95", "TPOT Avg", "TPOT P95")),
    ]
    if show_model:
        model_col = _build_excel_col(_MODEL_COL)
        blocks[0] = ("基本信息", "2F5496", [model_col] + blocks[0][2])
    return blocks


def save_excel(all_rows: list, groups: dict[str, list], model: str, output_dir: str,
               *, show_model: bool = False, charts: bool = False):
    """生成格式化的 Excel 报表，适合非技术人员阅读。

    包含：
    - 总览 sheet + 各维度独立 sheet（分组表头、色阶、最优值高亮），首个 sheet 即数据总览；
      表格本身带色阶/最优高亮，已足以直接读出性能，故默认不再生成柱状图
    - 数据表顶部的结论摘要（整体成功率/延迟/长尾/TPOT 快评 + 最快/最慢/最高吞吐行），
      冻结窗格设在数据首行，滚动表格时摘要与表头始终可见
    - 指标说明 sheet

    charts=True 时额外生成延迟 / QPS / TPS / TTFT 对比柱状图（QPS 与 TPS 分开展示）。
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.chart import BarChart, Reference
        from openpyxl.chart.label import DataLabelList
        from openpyxl.chart.text import RichText
        from openpyxl.drawing.text import Paragraph, ParagraphProperties, CharacterProperties, RichTextProperties
        from openpyxl.formatting.rule import ColorScaleRule
    except ImportError:
        print("\n⚠️  需要安装 openpyxl 才能生成 Excel: pip install openpyxl")
        return

    now = datetime.now()
    date_dir = Path(output_dir) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_").replace(" — ", "_")
    filepath = date_dir / f"report_{safe_model}_{now.strftime('%H%M%S')}.xlsx"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # ── 样式定义 ──────────────────────────────────
    hdr_font = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell_align = Alignment(horizontal="center", vertical="center")
    cell_align_left = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    best_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
    best_font = Font(name="微软雅黑", bold=True, size=11, color="1B5E20")
    block_fills = {
        "基本信息":   PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid"),
        "吞吐 & 延迟": PatternFill(start_color="D9F2D9", end_color="D9F2D9", fill_type="solid"),
        "流式指标":   PatternFill(start_color="FFE0B2", end_color="FFE0B2", fill_type="solid"),
    }
    block_header_fills = {
        "基本信息":   PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid"),
        "吞吐 & 延迟": PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid"),
        "流式指标":   PatternFill(start_color="E65100", end_color="E65100", fill_type="solid"),
    }

    # ── 列块 ──────────────────────────────────────
    blocks = _excel_blocks(show_model)

    def _flat_cols():
        """展平所有列块为单一列表"""
        flat = []
        for _bname, _bcolor, bcols in blocks:
            flat.extend(bcols)
        return flat

    def _col_letter_by_header(ws, base_header_name):
        """在 worksheet 中根据原始列名查找列字母"""
        flat = _flat_cols()
        for ci, (_, _, _, _, _, bh) in enumerate(flat, 1):
            if bh == base_header_name:
                return get_column_letter(ci)
        return "A"

    # ═══════════════════════════════════════════════════
    # 数据 Sheet
    # ═══════════════════════════════════════════════════
    def _write_data_sheet(ws, rows, title, start_row=3):
        """写入数据 sheet，带分组表头、色阶、最优值高亮

        start_row: 数据首行（默认 3）。若上方先写了结论摘要，表头与数据整体下移。
        """
        ws.title = title
        flat = _flat_cols()
        total_cols = len(flat)
        block_row = start_row - 2    # 块分组表头所在行
        header_row = start_row - 1   # 列名（含单位）表头所在行
        data_start_row = start_row   # 数据首行

        # ── 块分组表头（合并单元格） ──
        col_idx = 1
        for bname, bcolor, bcols in blocks:
            if len(bcols) == 0:
                continue
            if len(bcols) == 1:
                c = ws.cell(row=block_row, column=col_idx, value=bname)
                c.font = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
                c.fill = block_header_fills[bname]
                c.alignment = hdr_align
                c.border = thin_border
            else:
                ws.merge_cells(start_row=block_row, start_column=col_idx,
                               end_row=block_row, end_column=col_idx + len(bcols) - 1)
                c = ws.cell(row=block_row, column=col_idx, value=bname)
                c.font = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
                c.fill = block_header_fills[bname]
                c.alignment = hdr_align
                c.border = thin_border
                # 给合并区域的每个单元格加边框
                for ci in range(col_idx, col_idx + len(bcols)):
                    ws.cell(row=block_row, column=ci).border = thin_border
            col_idx += len(bcols)
        ws.row_dimensions[block_row].height = 26

        # ── 列名表头 ──
        for ci, (header, _, _, _, _, _) in enumerate(flat, 1):
            c = ws.cell(row=header_row, column=ci, value=header)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = hdr_align
            c.border = thin_border
        # 表头第二行含单位（如 延迟P50\n(s)），行高要能完整显示两行
        ws.row_dimensions[header_row].height = 32

        # ── 数据 ──
        # 先收集所有列的原始值，用于找最优值
        col_values = {ci: [] for ci in range(1, total_cols + 1)}
        for ri, r in enumerate(rows):
            row_num = data_start_row + ri
            for ci, (header, _getter, raw_fn, numfmt, _hbt, _bh) in enumerate(flat, 1):
                val = raw_fn(r)
                c = ws.cell(row=row_num, column=ci, value=val)
                c.alignment = cell_align if _bh not in ("场景", "模型") else cell_align_left
                c.border = thin_border
                if numfmt:
                    c.number_format = numfmt
                if val is not None and isinstance(val, (int, float)):
                    col_values[ci].append((row_num, val))
            # 行背景色按块区分
            for bi, (bname, _bcolor, bcols) in enumerate(blocks):
                start_c = sum(len(blocks[i][2]) for i in range(bi)) + 1
                end_c = start_c + len(bcols) - 1
                for ci in range(start_c, end_c + 1):
                    ws.cell(row=row_num, column=ci).fill = block_fills[bname]

        data_end_row = data_start_row + len(rows) - 1

        # ── 最优值高亮 ──
        for ci, (header, _, _, _, higher_better, _bh) in enumerate(flat, 1):
            if higher_better is None:
                continue
            if _bh == "成功率":
                continue  # 成功率用下方阈值色（红/黄/绿）表达，不做最优高亮
            vals = col_values[ci]
            if not vals:
                continue
            if higher_better:
                best_val = max(v[1] for v in vals)
            else:
                best_val = min(v[1] for v in vals)
            for row_num, val in vals:
                if val == best_val:
                    c = ws.cell(row=row_num, column=ci)
                    c.font = best_font
                    c.fill = best_fill

        # ── 条件着色：成功率 ──
        rate_col = None
        for ci, (header, _, _, _, _, _bh) in enumerate(flat, 1):
            if header == "成功率":
                rate_col = ci
                break
        if rate_col:
            for ri in range(data_start_row, data_end_row + 1):
                c = ws.cell(row=ri, column=rate_col)
                try:
                    val = float(c.value) if c.value is not None else 0
                    if val < 99:
                        c.fill = red_fill
                    elif val < 100:
                        c.fill = yellow_fill
                    else:
                        c.fill = green_fill
                except (ValueError, TypeError):
                    pass

        # ── 色阶：QPS, TPS, 延迟列 ──
        for ci, (header, _, _, _, higher_better, _bh) in enumerate(flat, 1):
            if higher_better is None:
                continue
            if _bh == "成功率":
                continue  # 避免渐变条件格式覆盖成功率的红/黄/绿阈值着色
            col_letter = get_column_letter(ci)
            cell_range = f"{col_letter}{data_start_row}:{col_letter}{data_end_row}"
            if higher_better:
                # 越大越好：低→红，中→黄，高→绿
                ws.conditional_formatting.add(cell_range,
                    ColorScaleRule(start_type="min", start_color="F4CCCC",
                                   mid_type="percentile", mid_value=50, mid_color="FFF2CC",
                                   end_type="max", end_color="B7E1CD"))
            else:
                # 越小越好：低→绿，中→黄，高→红
                ws.conditional_formatting.add(cell_range,
                    ColorScaleRule(start_type="min", start_color="B7E1CD",
                                   mid_type="percentile", mid_value=50, mid_color="FFF2CC",
                                   end_type="max", end_color="F4CCCC"))

        # ── 列宽自适应 ──
        for ci in range(1, total_cols + 1):
            max_width = len(str(flat[ci - 1][0])) * 2
            for ri in range(data_start_row, data_end_row + 1):
                val = str(ws.cell(row=ri, column=ci).value or "")
                max_width = max(max_width, len(val) * 1.2)
            ws.column_dimensions[get_column_letter(ci)].width = min(max_width + 4, 24)

        ws.freeze_panes = f"A{data_start_row}"
        return data_start_row, data_end_row

    # ═══════════════════════════════════════════════════
    # 图表
    # ═══════════════════════════════════════════════════
    def _add_charts(ws, rows, data_start_row, data_end_row):
        """添加延迟、QPS、TPS、TTFT 四个对比柱状图。

        - 多模型对比时用「模型 | 场景」作分类，避免同名场景重复导致标签挤在一起；
        - QPS 与 TPS 量纲差异大（个位数 vs 成百上千），共用一个 y 轴会把 QPS 压成
          轴底的一个小点，因此拆成两张图、各自独立坐标轴；
        - 图表高度固定、纵向间距按高度换算，杜绝多图互相重叠；
        - 柱间距/重叠调优、轴文字缩小，分类过多时标签旋转 -45°。
        """
        flat = _flat_cols()
        total_cols = len(flat)
        name_col_idx = None
        p50_col_idx = p95_col_idx = avg_col_idx = None
        qps_col_idx = tps_col_idx = None
        ttft_avg_idx = ttft_p95_idx = None

        for ci, (header, _, _, _, _, _bh) in enumerate(flat, 1):
            if _bh == "场景":
                name_col_idx = ci
            elif _bh == "延迟P50":
                p50_col_idx = ci
            elif _bh == "延迟P95":
                p95_col_idx = ci
            elif _bh == "延迟Avg":
                avg_col_idx = ci
            elif _bh == "QPS":
                qps_col_idx = ci
            elif _bh == "TPS":
                tps_col_idx = ci
            elif _bh == "TTFT Avg":
                ttft_avg_idx = ci
            elif _bh == "TTFT P95":
                ttft_p95_idx = ci

        # ── 多模型：分类改用「模型 | 场景」组合，避免同名场景重复 ──
        multi_model = len({r.get("model_label") or r.get("_model") for r in rows
                           if r.get("model_label") or r.get("_model")}) > 1
        x_axis_title = "模型 / 场景" if multi_model else "场景"
        if multi_model and name_col_idx is not None:
            # 图表分类辅助列：样式化标明用途，避免被误读为数据错误
            helper_col = total_cols + 1
            hc = ws.cell(row=data_start_row - 1, column=helper_col, value="图例: 模型 | 场景")
            hc.font = Font(name="微软雅黑", bold=True, size=10, color="808080")
            hc.fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
            hc.alignment = Alignment(horizontal="center", vertical="center")
            hc.border = thin_border
            for ri, r in enumerate(rows):
                lab = r.get("model_label") or r.get("_model") or "?"
                c = ws.cell(row=data_start_row + ri, column=helper_col,
                            value=f"{lab} | {r.get('name', '')}")
                c.font = Font(name="微软雅黑", size=9, color="808080")
                c.alignment = Alignment(horizontal="left", vertical="center")
                c.fill = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
                c.border = thin_border
            ws.column_dimensions[get_column_letter(helper_col)].width = 20
            name_col_idx = helper_col

        # ── 图表几何：固定高度、按行距换算纵向间距，防止图与图重叠 ──
        chart_height_cm = 9.5
        row_cm = 0.53  # Excel 默认行高 15pt ≈ 0.53cm
        chart_block_rows = int(chart_height_cm / row_cm) + 6

        def _axis_rich_text(size: int = 850, rotation: int | None = None) -> RichText:
            """轴文字格式：缩小字号；rotation 单位为 1/60000 度（-45° = -2700000）"""
            body = RichTextProperties(rot=rotation) if rotation is not None else None
            rpr = CharacterProperties(sz=size)
            return RichText(bodyPr=body, p=[Paragraph(pPr=ParagraphProperties(defRPr=rpr),
                                                      endParaRPr=rpr)])

        chart_row = data_end_row + 3

        def _make_chart(title, y_label, series_defs, width=None):
            """series_defs: [(label, col_idx, color), ...]"""
            chart = BarChart()
            chart.type = "col"
            chart.style = 10
            chart.title = title
            chart.y_axis.title = y_label
            chart.x_axis.title = x_axis_title
            chart.width = width or min(38, max(16, len(rows) * 2.0))
            chart.height = chart_height_cm
            # 柱更粗、同组系列间留少量空隙（簇状柱形，默认 gapWidth=150 柱太细）
            chart.gapWidth = 80
            chart.overlap = -10

            cats = Reference(ws, min_col=name_col_idx, min_row=data_start_row, max_row=data_end_row)
            for label, col_idx, color in series_defs:
                if col_idx is None:
                    continue
                data_ref = Reference(ws, min_col=col_idx, min_row=data_start_row - 1, max_row=data_end_row)
                chart.add_data(data_ref, titles_from_data=True)
            chart.set_categories(cats)

            # 系列颜色
            for i, (_label, _col_idx, color) in enumerate(series_defs):
                if i < len(chart.series):
                    chart.series[i].graphicalProperties.solidFill = color

            # 轴文字缩小，避免「字挤在一起」；分类多时标签旋转 -45°
            chart.x_axis.txPr = _axis_rich_text(
                size=850, rotation=-2700000 if len(rows) > 6 else None)
            chart.y_axis.txPr = _axis_rich_text(size=850)
            # 轴标题字号也缩小
            for axis in (chart.x_axis, chart.y_axis):
                if axis.title is not None and axis.title.tx is not None:
                    for p_ in axis.title.tx.rich.p:
                        for r_ in p_.r:
                            if r_.rPr is None:
                                r_.rPr = CharacterProperties(sz=900)
                            else:
                                r_.rPr.sz = 900

            # 图例放底部，避免挤占绘图区
            chart.legend.position = "b"
            chart.legend.overlay = False

            # 数据标签：分类少时显示数值，分类多时省略以免拥挤
            if len(rows) <= 6:
                for i, (_label, _col_idx, _color) in enumerate(series_defs):
                    if i < len(chart.series):
                        chart.series[i].dLbls = DataLabelList()
                        chart.series[i].dLbls.showVal = True
                        chart.series[i].dLbls.numFmt = "0.000" if "秒" in y_label else "0.00"
                        chart.series[i].dLbls.dLblPos = "outEnd"
            return chart

        # ── 布局：每张图占 chart_block_rows 行，纵向错开，杜绝重叠 ──
        last_chart_row = chart_row
        # 延迟对比图
        if p50_col_idx and p95_col_idx and avg_col_idx:
            chart1 = _make_chart("延迟对比 (P50/P95/Avg)", "秒", [
                ("P50", p50_col_idx, "5B9BD5"),
                ("P95", p95_col_idx, "ED7D31"),
                ("Avg", avg_col_idx, "A5A5A5"),
            ])
            ws.add_chart(chart1, f"A{chart_row}")
            last_chart_row = chart_row
            chart_row += chart_block_rows

        # QPS 图（独立 y 轴，避免被 TPS 的大数值压扁）
        if qps_col_idx:
            chart2 = _make_chart("吞吐对比 (QPS)", "req/s", [
                ("QPS", qps_col_idx, "5B9BD5"),
            ])
            ws.add_chart(chart2, f"A{chart_row}")
            last_chart_row = chart_row
            chart_row += chart_block_rows

        # TPS 图（独立 y 轴）
        if tps_col_idx:
            chart3 = _make_chart("吞吐对比 (TPS)", "tok/s", [
                ("TPS", tps_col_idx, "ED7D31"),
            ])
            ws.add_chart(chart3, f"A{chart_row}")
            last_chart_row = chart_row
            chart_row += chart_block_rows

        # TTFT 图
        if ttft_avg_idx and ttft_p95_idx:
            chart4 = _make_chart("首 Token 延迟对比 (TTFT)", "秒", [
                ("TTFT Avg", ttft_avg_idx, "5B9BD5"),
                ("TTFT P95", ttft_p95_idx, "ED7D31"),
            ])
            ws.add_chart(chart4, f"A{chart_row}")
            last_chart_row = chart_row

        # 返回最后一张图下方的安全行（预留；当前摘要已上移到表头上方）
        return last_chart_row + chart_block_rows

    # ═══════════════════════════════════════════════════
    # 结论摘要行
    # ═══════════════════════════════════════════════════
    def _add_summary(ws, rows, start_row, group_name=""):
        """在数据表上方输出面向阅读者的「结论摘要」，一行一个要点。

        面向非技术读者：开头说明「在对比什么场景/模型」，随后给出成功率、响应速度、
        首字时间(TTFT，流式)、吞吐、延迟稳定性、缓存命中率等可一眼读懂的关键结论；
        token 加权、平均每请求命中率等后台口径不在此展开（保留在 JSON summary/details）。
        """
        if len(rows) < 2:
            return 0
        multi_model = len({r.get("model_label") or r.get("_model") for r in rows
                           if r.get("model_label") or r.get("_model")}) > 1
        models = sorted({r.get("model_label") or r.get("_model") for r in rows
                         if r.get("model_label") or r.get("_model")})
        seen = []
        for r in rows:
            n = r.get("name", "?")
            if n not in seen:
                seen.append(n)
        scen_txt = "、".join(seen[:4]) + (f" 等 {len(seen)} 个" if len(seen) > 4 else "")

        def label(r) -> str:
            if multi_model:
                m = r.get("model_label") or r.get("_model") or "?"
                return f"{m} · {r.get('name', '?')}"
            return r.get("name", "?")

        def _tail_grade(ratio):
            if ratio < 1.5:
                return "稳定"
            if ratio < 3:
                return "有一定波动"
            if ratio < 5:
                return "波动较大"
            return "波动明显，建议关注"

        modes = {r.get("tpot_mode") for r in rows if r.get("tpot_mode")}
        if modes == {"stream"}:
            mode_txt = "流式"
        elif modes == {"nonstream"}:
            mode_txt = "非流式"
        elif modes:
            mode_txt = "流式+非流式"
        else:
            mode_txt = ""
        mode_suffix = f"（{mode_txt}）" if mode_txt else ""
        if multi_model:
            lines = [f"📋 对比：模型 {'、'.join(models)} ｜ 场景 {scen_txt}{mode_suffix}"]
        else:
            lines = [f"📋 对比：场景 {scen_txt}{mode_suffix}"]

        # 成功率（整表按请求数汇总）
        total_req = sum(r.get("total", 0) for r in rows)
        total_ok = sum(r.get("success", 0) for r in rows)
        total_fail = total_req - total_ok
        if total_fail:
            lines.append(f"✅ 成功率：{total_ok / total_req * 100:.1f}%（{total_fail} 次未成功）"
                         if total_req else "✅ 成功率：—")
        else:
            lines.append("✅ 成功率：100%（全部成功）")

        # 响应速度（P50）：平均 + 最快/最慢场景
        with_p50 = [r for r in rows if r.get("latency_p50", 0) > 0]
        if with_p50:
            p50s = [r["latency_p50"] for r in with_p50]
            fastest = min(with_p50, key=lambda r: r["latency_p50"])
            slowest = max(with_p50, key=lambda r: r["latency_p50"])
            fv = fastest["latency_p50"]
            sv = slowest["latency_p50"]
            txt = (f"⏱️ 响应速度：中位平均 {statistics.mean(p50s):.3f}s；"
                   f"最快 {label(fastest)} {fv:.3f}s / 最慢 {label(slowest)} {sv:.3f}s")
            if fv > 0 and sv / fv >= 1.5:
                txt += f"（约 {sv / fv:.1f} 倍差距）"
            lines.append(txt)

        # 首字时间 TTFT（流式指标；结论中补齐）
        ttft_rows = [r for r in rows if r.get("ttft_avg") is not None]
        if ttft_rows:
            vals = [r["ttft_avg"] for r in ttft_rows]
            best = min(ttft_rows, key=lambda r: r["ttft_avg"])
            worst = max(ttft_rows, key=lambda r: r["ttft_avg"])
            lines.append(f"⚡ 首字时间(TTFT)：平均 {statistics.mean(vals):.3f}s；"
                         f"最快 {label(best)} {best['ttft_avg']:.3f}s / "
                         f"最慢 {label(worst)} {worst['ttft_avg']:.3f}s")

        # 吞吐：最高行 + 平均
        with_qps = [r for r in rows if r.get("qps", 0) > 0]
        if with_qps:
            best_q = max(with_qps, key=lambda r: r["qps"])
            txt = f"🚀 吞吐：最高 {label(best_q)} — {best_q['qps']:.2f} req/s"
            if len(with_qps) > 1:
                txt += f"（平均 {statistics.mean(r['qps'] for r in with_qps):.2f}）"
            lines.append(txt)

        # 延迟稳定性（P95/P50）
        tail_rows = [r for r in rows
                     if r.get("latency_p95", 0) > 0 and r.get("latency_p50", 0) > 0]
        if tail_rows:
            ratio = statistics.mean(r["latency_p95"] / r["latency_p50"] for r in tail_rows)
            lines.append(f"📈 延迟稳定性：P95 约为 P50 的 {ratio:.2f} 倍（{_tail_grade(ratio)}）")

        # 缓存命中率：客户视图只给一个总体百分比（细分口径在 JSON 中）
        cache_rows = [r for r in rows if r.get("cache_hit_rate") is not None]
        if cache_rows:
            c_total = sum(r.get("cached_tokens_total", 0) for r in cache_rows)
            p_total = sum(r.get("cache_prompt_total", 0) for r in cache_rows)
            rate = min(100.0, c_total / p_total * 100) if p_total > 0 else 0.0
            # 样本量：API 只在成功请求里回传 cached_tokens，若上报数远小于成功数需提示
            reported = sum(r.get("cache_reported_requests", 0) for r in cache_rows)
            succeeded = sum(r.get("success", 0) for r in cache_rows)
            note = f"（{reported}/{succeeded} 个成功请求上报 cached_tokens）" \
                if reported and reported < succeeded else ""
            lines.append(f"💾 缓存命中率：{rate:.1f}%{note}")

        # 告警：未全部成功的场景
        risky = sorted((r for r in rows if r.get("success_rate", 100) < 100),
                       key=lambda r: r["success_rate"])
        if risky:
            worst = risky[0]
            shown = "、".join(label(r) for r in risky[:3])
            if len(risky) > 3:
                shown += f" 等 {len(risky)} 个"
            lines.append(f"⚠️ 需关注：{shown} 成功率未达 100%（最低 {worst['success_rate']:.1f}%）")

        row = start_row
        heading = f"📝 结论摘要（{group_name}）" if group_name else "📝 结论摘要"
        c = ws.cell(row=row, column=1, value=heading)
        c.font = Font(name="微软雅黑", bold=True, size=12, color="2F5496")
        c.fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(_flat_cols()))
        row += 1
        for line in lines:
            c = ws.cell(row=row, column=1, value=line)
            c.font = Font(name="微软雅黑", size=10, color="444444")
            c.alignment = Alignment(horizontal="left", vertical="center")
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(_flat_cols()))
            row += 1
        return row - 1

    # ═══════════════════════════════════════════════════
    # 构建 Sheets
    # ═══════════════════════════════════════════════════

    # ① 总览（首个 sheet：顶部结论摘要 + 数据表，打开即见）
    ws_all = wb.create_sheet()
    used = _add_summary(ws_all, all_rows, 1)  # 顶部结论摘要，返回占用行数（无则 0）
    start_row = used + 3 if used else 3
    ds, de = _write_data_sheet(ws_all, all_rows, "总览", start_row=start_row)
    if charts:
        _add_charts(ws_all, all_rows, ds, de)

    # ② 各维度 Sheet（仅当该维度是总览的真子集时才创建，避免重复）
    group_labels = {"输入": "输入长度梯度", "输出": "输出长度梯度", "业务": "业务场景验证"}
    total_count = len(all_rows)
    for prefix in ["输入", "输出", "业务"]:
        if prefix in groups and len(groups[prefix]) >= 2:
            if len(groups[prefix]) == total_count:
                continue  # 该分组就是全部数据，总览已经覆盖，不重复创建
            ws = wb.create_sheet()
            label = group_labels.get(prefix, prefix)
            used = _add_summary(ws, groups[prefix], 1, prefix)
            start_row = used + 3 if used else 3
            ds, de = _write_data_sheet(ws, groups[prefix], label, start_row=start_row)
            if charts:
                _add_charts(ws, groups[prefix], ds, de)
            tab_colors = {"输入": "2F5496", "输出": "2E7D32", "业务": "E65100"}
            ws.sheet_properties.tabColor = tab_colors.get(prefix, "2F5496")
    for prefix, group_rows in sorted(groups.items()):
        if prefix in ("输入", "输出", "业务"):
            continue
        if len(group_rows) >= 2:
            if len(group_rows) == total_count:
                continue
            ws = wb.create_sheet()
            used = _add_summary(ws, group_rows, 1, prefix)
            start_row = used + 3 if used else 3
            ds, de = _write_data_sheet(ws, group_rows, prefix, start_row=start_row)
            if charts:
                _add_charts(ws, group_rows, ds, de)

    # ③ 指标说明
    ws_help = wb.create_sheet("指标说明")
    ws_help.sheet_properties.tabColor = "888888"
    ws_help.column_dimensions["A"].width = 16
    ws_help.column_dimensions["B"].width = 60
    help_header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    for ci, text in enumerate(["指标", "说明"], 1):
        cell = ws_help.cell(row=1, column=ci, value=text)
        cell.font = hdr_font
        cell.fill = help_header_fill
        cell.alignment = hdr_align
        cell.border = thin_border
    ws_help.freeze_panes = "A2"
    for ri, (metric, desc) in enumerate(_METRIC_HELP.items(), 2):
        cell_a = ws_help.cell(row=ri, column=1, value=metric)
        cell_a.font = Font(name="微软雅黑", bold=True, size=10)
        cell_a.alignment = cell_align
        cell_a.border = thin_border
        cell_b = ws_help.cell(row=ri, column=2, value=desc)
        cell_b.font = Font(name="微软雅黑", size=10)
        cell_b.alignment = Alignment(vertical="center")
        cell_b.border = thin_border

    # ── 保存 ──────────────────────────────────────
    wb.save(filepath)
    try:
        print(f"   📗 Excel:     {filepath}")
    except UnicodeEncodeError:
        print(f"   [Excel]      {filepath}")
