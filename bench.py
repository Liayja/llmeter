"""
llmeter — LLM 性能测量工具

单次压测:
  python bench.py chat -f config/config.yaml
  python bench.py chat -b https://api.openai.com -k sk-xxx -m gpt-4o -c 10 -n 100
  python bench.py chat -f config/config.yaml -c 50           # 配置文件 + 命令行覆盖

多场景对比压测:
  python bench.py scenarios -f config/scenarios.yaml      # 跑完所有场景并生成对比表
  python bench.py scenarios -f config/scenarios.yaml -s "短输入,翻译"  # 精确匹配
  python bench.py scenarios -f config/scenarios.yaml -s "输入-"       # 前缀匹配整组
  python bench.py scenarios -f config/scenarios.yaml -s "并发-"       # 只跑并发梯度组
"""

from pathlib import Path
import os
import sys
import argparse
import asyncio

# 将 src 目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).parent / "src"))
from output import (
    print_report, save_results,
    print_comparison, save_comparison, save_excel, _row_from_result,
)
from engine import LLMBench

# ── argparse 默认值（用于判断 CLI 是否显式传入） ──────────────────
_DEFAULTS = {
    "base_url": None,
    "api_key": None,
    "model": None,
    "prompt": "Hello, world!",
    "prompt_file": None,
    "concurrency": 10,
    "requests": 100,
    "timeout": 30,
    "output_dir": "bench_results",
    "max_tokens": 2048,
    "temperature": 0.7,
    "stream": False,
    "retries": 2,
    "retry_backoff": 1.0,
    "read_timeout": None,
    "unique_prefix": False,
    "verbose": False,
    "extra_params": None,
    "http2": False,
    "warmup": 0,
}

# 配置文件中的字段名同时也是 argparse dest 名
_CONF_KEYS = [
    "base_url", "api_key", "model", "prompt", "prompt_file",
    "concurrency", "requests", "timeout", "output_dir",
    "max_tokens", "temperature", "stream", "retries", "retry_backoff",
    "read_timeout", "unique_prefix", "verbose", "extra_params",
    "http2", "warmup",
]

# 引擎注册表
_ENGINES = {"chat": LLMBench}


def _expand_env(obj):
    """递归展开配置中的 ${ENV_VAR} / $ENV_VAR 引用。

    让 api_key / base_url 等敏感字段可以写成 ${OMNIX_API_KEY} 从环境变量注入，
    避免明文密钥落进配置文件被误提交。未定义的变量保持原样（os.path.expandvars 行为）。
    """
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def load_config_file(path: Path) -> dict:
    """从 YAML / JSON 配置文件加载参数（支持 ${ENV_VAR} 环境变量展开）"""
    if not path.exists():
        print(f"❌ 配置文件不存在: {path}")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    ext = path.suffix.lower()

    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            print("❌ 配置文件为 YAML 格式，需要安装 PyYAML: pip install pyyaml")
            sys.exit(1)
        cfg = yaml.safe_load(text) or {}

    elif ext == ".json":
        import json
        cfg = json.loads(text)

    else:
        print(f"❌ 不支持的配置文件格式: {ext}，请使用 .yaml / .yml / .json")
        sys.exit(1)

    return _expand_env(cfg)


def merge_config(args: argparse.Namespace, file_cfg: dict) -> None:
    """将配置文件中的值合并到 args，命令行参数优先级更高"""
    for key in _CONF_KEYS:
        file_val = file_cfg.get(key)

        if file_val is None:
            continue

        # 如果 CLI 值等于 argparse 默认值，说明用户没有显式传入，用配置文件的值
        if getattr(args, key) == _DEFAULTS[key]:
            setattr(args, key, file_val)


def resolve_prompt(prompt: str, prompt_file: str | None) -> str:
    """根据 prompt / prompt_file 解析出最终的 Prompt 文本"""
    if prompt_file:
        fp = Path(prompt_file)
        if not fp.exists():
            print(f"❌ Prompt 文件不存在: {fp}")
            sys.exit(1)
        return fp.read_text(encoding="utf-8").strip()
    return prompt


def build_engine_config(raw: dict) -> dict:
    """组装引擎实例化所需的配置（统一入口，单次/多场景共用）"""
    return {
        "base_url": raw["base_url"].strip("/"),
        "api_key": raw["api_key"],
        "model": raw["model"],
        "prompt": resolve_prompt(raw.get("prompt", _DEFAULTS["prompt"]), raw.get("prompt_file")),
        "concurrency": raw["concurrency"],
        "total_requests": raw["requests"],
        "timeout": raw["timeout"],
        "output_dir": raw["output_dir"],
        "max_tokens": raw["max_tokens"],
        "temperature": raw["temperature"],
        "stream": raw["stream"],
        "retries": raw.get("retries", _DEFAULTS["retries"]),
        "retry_backoff": raw.get("retry_backoff", _DEFAULTS["retry_backoff"]),
        "read_timeout": raw.get("read_timeout", _DEFAULTS["read_timeout"]),
        "unique_prefix": raw.get("unique_prefix", _DEFAULTS["unique_prefix"]),
        "verbose": raw.get("verbose", _DEFAULTS["verbose"]),
        "extra_params": raw.get("extra_params") or None,
        "http2": raw.get("http2", _DEFAULTS["http2"]),
        "warmup": raw.get("warmup", _DEFAULTS["warmup"]),
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """深合并两个 dict（override 优先）。用于跨层合并 extra_params，
    使模型级推理开关与场景级参数能叠加而非互相整体覆盖。"""
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _merge_extra_params(*layers: dict) -> dict | None:
    """按顺序深合并各层的 extra_params（靠后的层优先）。"""
    ep: dict = {}
    for layer in layers:
        val = layer.get("extra_params")
        if isinstance(val, dict):
            ep = _deep_merge(ep, val)
    return ep or None


def validate_required(raw: dict) -> None:
    """校验必填字段"""
    for key, label in [("api_key", "API Key"), ("base_url", "base-url"), ("model", "model")]:
        if not raw.get(key):
            print(f"❌ {label} 不能为空（请通过命令行或配置文件提供）")
            sys.exit(1)


async def run_one(cfg: dict, engine_type: str, *, save: bool = True) -> dict:
    """运行单个压测，返回 metrics"""
    engine = _ENGINES[engine_type](**cfg)
    print(f"🚀 压测：{engine_type}/{cfg['model']} 并发={cfg['concurrency']} 请求={cfg['total_requests']}")
    print(f"    Prompt: {cfg['prompt'][:60]}{'...' if len(cfg['prompt']) > 60 else ''}")

    metrics = await engine.run()
    print_report(metrics, cfg["model"], engine_type)
    if save:
        save_results(metrics, engine.results, cfg, engine_type)
    return metrics


# ═══════════════════════════════════════════════════
# 子命令: chat（单次压测）
# ═══════════════════════════════════════════════════
async def cmd_chat(args: argparse.Namespace) -> int:
    if args.config:
        merge_config(args, load_config_file(Path(args.config)))

    raw = vars(args)
    validate_required(raw)
    cfg = build_engine_config(raw)

    metrics = await run_one(cfg, "chat")
    return 0 if metrics["fail"] == 0 else 1


# ═══════════════════════════════════════════════════
# 子命令: scenarios（多场景对比压测）
# ═══════════════════════════════════════════════════
async def cmd_scenarios(args: argparse.Namespace) -> int:
    file_cfg = load_config_file(Path(args.config))

    # 公共参数（顶层） + 默认参数（defaults） + 场景列表（scenarios）
    scenarios = file_cfg.get("scenarios")
    if not scenarios:
        print("❌ 配置文件中未找到 scenarios 列表")
        sys.exit(1)

    defaults = file_cfg.get("defaults", {})
    # 顶层公共字段（base_url/api_key/model 等）
    common = {k: v for k, v in file_cfg.items()
              if k not in ("scenarios", "defaults", "models")}

    # ── 模型列表：多模型时对 模型 × 场景 做笛卡尔积 ──────────────
    #   未提供 models 时回退为单模型（[{}]，model 取 common/defaults）。
    #   每个模型可带自己的 model/base_url/api_key/extra_params，
    #   从而不同模型的推理开关（thinking / reasoning_effort）互不干扰。
    models = file_cfg.get("models") or [{}]

    # ── 场景过滤：支持按名称选择场景 ──────────────────────────
    #   -s "短输入"       → 精确匹配
    #   -s "输入-"        → 前缀匹配（匹配所有 输入-* 场景）
    #   -s "输入-,并发-"  → 混合使用
    if args.scenarios:
        selected = [s.strip() for s in args.scenarios.split(",") if s.strip()]
        filtered = []
        for sc in scenarios:
            name = sc.get("name", "")
            for sel in selected:
                if sel.endswith("-") or sel.endswith("*"):
                    # 前缀匹配：去尾缀后匹配开头
                    prefix = sel[:-1]
                    if name.startswith(prefix):
                        filtered.append(sc)
                        break
                elif name == sel:
                    filtered.append(sc)
                    break
        if not filtered:
            print(f"❌ 未找到匹配的场景: {args.scenarios}")
            print(f"   可用场景: {', '.join(sc.get('name', '?') for sc in scenarios)}")
            sys.exit(1)
        # 报告匹配情况
        matched_names = {sc["name"] for sc in filtered}
        for sel in selected:
            is_prefix = sel.endswith("-") or sel.endswith("*")
            if is_prefix:
                prefix = sel[:-1]
                hits = [n for n in matched_names if n.startswith(prefix)]
                if not hits:
                    print(f"⚠️  前缀「{sel}」未匹配任何场景")
            else:
                if sel not in matched_names:
                    print(f"⚠️  场景「{sel}」未在配置中找到，已跳过")
        scenarios = filtered
        print(f"🎯 已选择 {len(scenarios)} 个场景: {', '.join(sc['name'] for sc in scenarios)}")

    rows = []
    total = len(models) * len(scenarios)
    idx = 0
    for model_entry in models:
        # 模型显示名（name 优先，回退到 model 字段）。注意从 model_entry 剥掉 name，
        # 避免它在合并时污染场景的 name；extra_params 单独深合并。
        model_label = (
            model_entry.get("name")
            or model_entry.get("model")
            or common.get("model") or defaults.get("model") or "model"
        )
        model_fields = {k: v for k, v in model_entry.items()
                        if k not in ("name", "extra_params")}

        for sc in scenarios:
            idx += 1
            name = sc.get("name", f"场景{idx}")
            sc_fields = {k: v for k, v in sc.items() if k != "extra_params"}
            print(f"\n{'━'*60}")
            if len(models) > 1:
                print(f"▶ [{idx}/{total}] 模型: {model_label}  |  场景: {name}")
            else:
                print(f"▶ [{idx}/{total}] 场景: {name}")
            print(f"{'━'*60}")

            # 合并优先级: 场景 > 模型 > defaults > common > 全局默认
            raw = {**_DEFAULTS, **common, **defaults, **model_fields, **sc_fields}
            # extra_params 跨层深合并（靠后优先）：common < defaults < 模型 < 场景
            raw["extra_params"] = _merge_extra_params(common, defaults, model_entry, sc)
            # CLI verbose 覆盖配置文件中的值
            if args.verbose:
                raw["verbose"] = True
            validate_required(raw)
            cfg = build_engine_config(raw)

            try:
                metrics = await run_one(cfg, "chat")
            except Exception as e:
                import traceback
                print(f"❌ 场景「{name}」执行失败: {e}")
                traceback.print_exc()
                continue

            row = _row_from_result(name, metrics, cfg["concurrency"])
            row["_model"] = cfg["model"]
            row["model_label"] = model_label
            row["_output_dir"] = cfg["output_dir"]
            rows.append(row)

    if not rows:
        print("\n❌ 没有任何场景成功完成，无法生成对比报告")
        return 1

    # ── 生成对比报告 ──────────────────────────────────────
    output_dir = rows[0].get("_output_dir") or common.get("output_dir") \
        or defaults.get("output_dir") or _DEFAULTS["output_dir"]

    if len(models) == 1:
        # 单模型：保持原有按维度分组的报告
        model = rows[0].get("_model") or "unknown"
        report_scenarios(rows, model, output_dir, excel=args.excel)
    else:
        # 多模型：① 每个模型各自的维度报告；② 跨模型对比（按场景分组，带模型列）
        labels = []
        for r in rows:
            if r["model_label"] not in labels:
                labels.append(r["model_label"])
        for label in labels:
            mrows = [r for r in rows if r["model_label"] == label]
            print(f"\n{'█'*60}")
            print(f"█ 模型 {label} 分维度报告")
            print(f"{'█'*60}")
            report_scenarios(mrows, label, output_dir, excel=False)

        print(f"\n{'█'*60}")
        print(f"█ 跨模型对比（同场景横向比较 {len(labels)} 个模型）")
        print(f"{'█'*60}")
        report_scenarios(rows, "跨模型", output_dir, excel=args.excel, show_model=True)

    # 任一场景有失败则返回非 0
    any_fail = any(r["fail"] > 0 for r in rows)
    return 1 if any_fail else 0


def report_scenarios(rows: list, model: str, output_dir: str, *,
                     excel: bool = False, show_model: bool = False) -> None:
    """按名称前缀（输入-/输出-/并发-/业务-）分组输出对比表。

    show_model=True 时在表格最左加一列「模型」，并把同场景不同模型的行排在一起，
    用于跨模型横向对比。
    """
    def _group_prefix(name: str) -> str:
        dash = name.find("-")
        return name[:dash] if dash > 0 else "其他"

    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(_group_prefix(row["name"]), []).append(row)

    print(f"\n{'═'*60}")
    print(f"✅ 共 {len(rows)} 条结果，{len(groups)} 个维度")
    print(f"{'═'*60}")

    def _sort(rs: list) -> list:
        return sorted(rs, key=lambda r: (r["name"], r.get("model_label", ""))) if show_model else rs

    # 每个维度独立输出对比表
    for prefix, group_rows in sorted(groups.items()):
        group_rows = _sort(group_rows)
        if len(group_rows) >= 2:
            subtitle = f"{model} — {prefix}"
            print_comparison(group_rows, subtitle, show_model=show_model)
            save_comparison(group_rows, subtitle, output_dir, show_model=show_model)
        else:
            r = group_rows[0]
            model_tag = f"[{r.get('model_label')}] " if show_model else ""
            print(f"\n  [{prefix}] {model_tag}{r['name']}: 成功率 {r['success_rate']:.1f}%  "
                  f"P50={r['latency_p50']:.3f}s  QPS={r['qps']:.2f}")

    # 多维度时额外输出总对比表
    if len(groups) >= 2:
        print_comparison(_sort(rows), model, show_model=show_model)
        save_comparison(_sort(rows), model, output_dir, show_model=show_model)

    # Excel 报表（可选，面向非技术人员）
    if excel:
        print(f"\n{'─'*60}")
        print(f"📗 生成 Excel 报表...")
        save_excel(_sort(rows), groups, model, output_dir, show_model=show_model)


def add_common_args(p: argparse.ArgumentParser) -> None:
    """为单次压测子命令添加通用参数"""
    p.add_argument("-f", "--config", default=None, help="配置文件路径 (.yaml / .json)")
    p.add_argument("-b", "--base-url", default=_DEFAULTS["base_url"], help="API 基础 URL")
    p.add_argument("-k", "--api-key", default=_DEFAULTS["api_key"], help="API Key")
    p.add_argument("-m", "--model", default=_DEFAULTS["model"], help="模型名称，如 gpt-4o")
    p.add_argument("-p", "--prompt", default=_DEFAULTS["prompt"], help="压测使用的 Prompt")
    p.add_argument("--prompt-file", default=_DEFAULTS["prompt_file"], help="从文件加载 Prompt，优先级高于 --prompt")
    p.add_argument("-c", "--concurrency", type=int, default=_DEFAULTS["concurrency"], help="并发数(默认10)")
    p.add_argument("-n", "--requests", type=int, default=_DEFAULTS["requests"], help="总请求数(默认100)")
    p.add_argument("-t", "--timeout", type=int, default=_DEFAULTS["timeout"], help="单请求超时时间，单位秒")
    p.add_argument("-o", "--output-dir", default=_DEFAULTS["output_dir"], help="结果输出目录")
    p.add_argument("--max-tokens", type=int, default=_DEFAULTS["max_tokens"], help="生成文本的最大 token 数")
    p.add_argument("--temperature", type=float, default=_DEFAULTS["temperature"], help="生成文本的温度")
    p.add_argument("--stream", action="store_true", help="是否使用流式响应")
    p.add_argument("--read-timeout", type=float, default=_DEFAULTS["read_timeout"],
                   help="流式 read 阶段(相邻 chunk 间隔)超时,默认同 --timeout")
    p.add_argument("--unique-prefix", action="store_true", default=_DEFAULTS["unique_prefix"],
                   help="每个请求加唯一 nonce 前缀,破坏服务端 prefix cache(冷 prefill 压测用)")
    p.add_argument("--http2", action="store_true", default=_DEFAULTS["http2"],
                   help="启用 HTTP/2(默认 HTTP/1.1)。注意 H2 会把并发多路复用到单连接,"
                        "并发压测建议保持关闭以获得真实并行连接")
    p.add_argument("--warmup", type=int, default=_DEFAULTS["warmup"],
                   help="正式计测前先打 N 个丢弃请求预热连接,剔除首批 TTFT 中的 TCP/TLS 握手")
    p.add_argument("-v", "--verbose", action="store_true", default=_DEFAULTS["verbose"],
                   help="打印首个请求的原始 chunk 格式，用于诊断解析问题")


async def main():
    p = argparse.ArgumentParser(description="llmeter — LLM 性能测量工具")
    sub = p.add_subparsers(dest="command", required=True)

    # chat: 单次压测
    p_chat = sub.add_parser("chat", help="单次压测")
    add_common_args(p_chat)
    p_chat.set_defaults(func=cmd_chat)

    # scenarios: 多场景对比压测
    p_sc = sub.add_parser("scenarios", help="多场景对比压测")
    p_sc.add_argument("-f", "--config", required=True, help="场景配置文件路径 (.yaml / .json)")
    p_sc.add_argument("-s", "--scenarios", default="",
                      help="只运行指定场景，多个用逗号分隔。支持前缀匹配（如 输入- 匹配 输入-短/输入-中等）")
    p_sc.add_argument("-v", "--verbose", action="store_true", default=_DEFAULTS["verbose"],
                      help="打印首个请求的原始 chunk 格式，用于诊断解析问题")
    p_sc.add_argument("--excel", action="store_true",
                      help="额外生成格式化的 Excel 报表（含图表，适合非技术人员阅读）")
    p_sc.set_defaults(func=cmd_scenarios)

    args = p.parse_args()
    exit_code = await args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())
