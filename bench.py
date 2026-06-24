"""
llmeter — LLM 性能测量工具

单次压测:
  python bench.py chat -f config/config.yaml
  python bench.py chat -b https://api.openai.com -k sk-xxx -m gpt-4o -c 10 -n 100
  python bench.py chat -f config/config.yaml -c 50           # 配置文件 + 命令行覆盖

多场景对比压测:
  python bench.py scenarios -f config/scenarios.yaml      # 跑完所有场景并生成对比表
"""

from pathlib import Path
import sys
import argparse
import asyncio

# 将 src 目录加入模块搜索路径
sys.path.insert(0, str(Path(__file__).parent / "src"))
from output import (
    print_report, save_results,
    print_comparison, save_comparison, _row_from_result,
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
}

# 配置文件中的字段名同时也是 argparse dest 名
_CONF_KEYS = [
    "base_url", "api_key", "model", "prompt", "prompt_file",
    "concurrency", "requests", "timeout", "output_dir",
    "max_tokens", "temperature", "stream", "retries", "retry_backoff",
]

# 引擎注册表
_ENGINES = {"chat": LLMBench}


def load_config_file(path: Path) -> dict:
    """从 YAML / JSON 配置文件加载参数"""
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
        return yaml.safe_load(text) or {}

    elif ext == ".json":
        import json
        return json.loads(text)

    else:
        print(f"❌ 不支持的配置文件格式: {ext}，请使用 .yaml / .yml / .json")
        sys.exit(1)


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
    }


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
    common = {k: v for k, v in file_cfg.items() if k not in ("scenarios", "defaults")}

    rows = []
    total = len(scenarios)
    for i, sc in enumerate(scenarios, 1):
        name = sc.get("name", f"场景{i}")
        print(f"\n{'━'*60}")
        print(f"▶ [{i}/{total}] 场景: {name}")
        print(f"{'━'*60}")

        # 合并优先级: 场景 > defaults > common > 全局默认
        raw = {**_DEFAULTS, **common, **defaults, **sc}
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
        row["_output_dir"] = cfg["output_dir"]
        rows.append(row)

    if not rows:
        print("\n❌ 没有任何场景成功完成，无法生成对比报告")
        return 1

    # ── 生成对比报告 ──────────────────────────────────────
    # 从实际执行的首个场景中取 model / output_dir（考虑场景层覆盖）
    first_row = rows[0]
    model = first_row.get("_model") or common.get("model") or defaults.get("model") or "unknown"
    output_dir = first_row.get("_output_dir") or common.get("output_dir") or defaults.get("output_dir") or _DEFAULTS["output_dir"]
    print(f"\n{'═'*60}")
    print(f"✅ 全部 {len(rows)}/{total} 个场景完成")
    print(f"{'═'*60}")
    print_comparison(rows, model)
    save_comparison(rows, model, output_dir)

    # 任一场景有失败则返回非 0
    any_fail = any(r["fail"] > 0 for r in rows)
    return 1 if any_fail else 0


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
    p_sc.set_defaults(func=cmd_scenarios)

    args = p.parse_args()
    exit_code = await args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())
