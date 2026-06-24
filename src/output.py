"""
终端打印 + 结果保存
"""
import json
import csv
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
    table.add_row("成功率", f"{m['success_rate']:.1f}%")
    table.add_row("QPS", f"{m['qps']:.2f} req/s")
    if m.get("total_output_tokens", 0) > 0:
        table.add_row("Token 吞吐", f"{m['tps']:.1f} tok/s")
    table.add_section()
    table.add_row("[bold]延迟 (Latency)[/bold]", "")
    for label, key in [("Avg", "latency_avg"), ("P50", "latency_p50"),
                        ("P95", "latency_p95"), ("P99", "latency_p99"),
                        ("Min", "latency_min"), ("Max", "latency_max")]:
        table.add_row(f"  {label}", f"{m[key]:.3f}s")
    if m.get("ttft_avg") is not None:
        table.add_section()
        table.add_row("[bold]首 Token 延迟 (TTFT)[/bold]", "")
        for label, key in [("Avg", "ttft_avg"), ("P50", "ttft_p50"), ("P95", "ttft_p95")]:
            table.add_row(f"  {label}", f"{m[key]:.3f}s")
    if m.get("tpot_avg") is not None:
        table.add_section()
        table.add_row("[bold]每 Token 耗时 (TPOT)[/bold]", "")
        for label, key in [("Avg", "tpot_avg"), ("P50", "tpot_p50"), ("P95", "tpot_p95")]:
            table.add_row(f"  {label}", f"{m[key]*1000:.1f} ms")
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
    print(f" 成功率: {m['success_rate']:.1f}%  |  QPS: {m['qps']:.2f} req/s")
    if m.get("total_output_tokens", 0) > 0:
        print(f" Token 吞吐: {m['tps']:.1f} tok/s")
    print(f" 延迟 — Avg: {m['latency_avg']:.3f}s  P50: {m['latency_p50']:.3f}s  P95: {m['latency_p95']:.3f}s  P99: {m['latency_p99']:.3f}s")
    if m.get("ttft_avg") is not None:
        print(f" TTFT — Avg: {m['ttft_avg']:.3f}s  P50: {m['ttft_p50']:.3f}s  P95: {m['ttft_p95']:.3f}s")
    if m.get("tpot_avg") is not None:
        print(f" TPOT — Avg: {m['tpot_avg']*1000:.1f}ms  P50: {m['tpot_p50']*1000:.1f}ms  P95: {m['tpot_p95']*1000:.1f}ms")
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
    filename = f"{safe_model}_c{cfg['concurrency']}_n{cfg['total_requests']}_{now.strftime('%H%M%S')}.json"
    filepath = date_dir / filename
    output = {
        "meta": {
            "engine": engine_type,
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "concurrency": cfg["concurrency"],
            "total_requests": cfg["total_requests"],
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
_COMPARE_COLS = [
    ("场景",      lambda r: r["name"],                       str),
    ("并发",      lambda r: r["concurrency"],                str),
    ("请求数",    lambda r: r["total"],                      str),
    ("成功率",    lambda r: r["success_rate"],               lambda v: f"{v:.1f}%"),
    ("QPS",       lambda r: r["qps"],                        lambda v: f"{v:.2f}"),
    ("TPS",       lambda r: r["tps"],                        lambda v: f"{v:.1f}"),
    ("延迟Avg",   lambda r: r["latency_avg"],                lambda v: f"{v:.3f}s"),
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


def print_comparison(rows: list, model: str):
    """打印多场景对比表到终端"""
    title = f"📊 多场景压测对比 — {model}"

    if RICH:
        console = Console()
        table = Table(title=title, show_lines=True)
        for header, _, _ in _COMPARE_COLS:
            table.add_column(header, justify="right" if header != "场景" else "left",
                             style="cyan" if header == "场景" else None)
        for r in rows:
            cells = []
            for _, getter, fmt in _COMPARE_COLS:
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
        headers = [h for h, _, _ in _COMPARE_COLS]
        # 计算每列最大宽度：对齐表头和所有行
        all_cells = [headers[:]]  # 第一行是表头
        for r in rows:
            all_cells.append([fmt(getter(r)) for _, getter, fmt in _COMPARE_COLS])
        col_widths = []
        for ci in range(len(headers)):
            max_w = max(len(str(row[ci])) for row in all_cells)
            col_widths.append(max(max_w, len(headers[ci])))
        # 打印表头
        print(" | ".join(f"{headers[i]:>{col_widths[i]}}" for i in range(len(headers))))
        print("-+-".join("-" * w for w in col_widths))
        for row_cells in all_cells[1:]:
            print(" | ".join(f"{str(row_cells[i]):>{col_widths[i]}}" for i in range(len(row_cells))))


def save_comparison(rows: list, model: str, output_dir: str):
    """保存对比报告为 Markdown + CSV"""
    now = datetime.now()
    date_dir = Path(output_dir) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_").replace(":", "_")
    stem = f"comparison_{safe_model}_{now.strftime('%H%M%S')}"

    headers = [h for h, _, _ in _COMPARE_COLS]

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
        cells = [fmt(getter(r)) for _, getter, fmt in _COMPARE_COLS]
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
            writer.writerow([getter(r) for _, getter, _ in _COMPARE_COLS])

    print(f"\n💾 对比报告已保存:")
    print(f"   📄 Markdown: {md_path}")
    print(f"   📊 CSV:      {csv_path}")