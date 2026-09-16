"""内置用例集（P1：D1 参数语义 / D2 指纹 / D8 结构工具 / D9 协议健壮性）。

用例是"静态定义"：每个用例给出完整请求体片段与判定规则；P1 先代码内置，后续再迁到数据库由界面维护。
"""
from typing import Any

CASE_SET = "parity-v1"

# ── D2 指纹：用于比对 tokenizer / 模板（prompt_tokens 应高度一致）──
_FP_TEXTS = [
    ("zh-short", "请用一句话介绍杭州。"),
    ("zh-long", "请阅读下面的材料并总结要点：" + "分布式系统的核心挑战包括一致性、可用性与分区容忍性。" * 20),
    ("en", "Summarize the following in one sentence: " + "Latency and throughput must be balanced. " * 15),
    ("code", "解释这段代码的作用：\n" + "def fib(n):\n    return n if n < 2 else fib(n-1) + fib(n-2)\n" * 6),
    ("emoji", "把下面这句话翻译成英文，并保留表情：🚀 我们上线了新功能 🎉，性能提升了 3 倍 ✅。"),
    ("mixed", "请对比 Go 与 Rust 的并发模型，要求：中文回答，200 字以内。Goroutine vs async/await。"),
]

# ── D9 协议健壮性：异常 tool 序列，判定"行为类别"是否与官方一致 ──
_TOOL_A = {"id": "call_A", "type": "function",
           "function": {"name": "get_weather", "arguments": '{"city":"杭州"}'}}
_TOOL_B = {"id": "call_B", "type": "function",
           "function": {"name": "get_time", "arguments": '{"city":"杭州"}'}}
_TOOLS = [{"type": "function", "function": {
    "name": "get_weather",
    "description": "查询城市天气",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]


def _tool_case(cid: str, name: str, messages: list[dict], extra: dict | None = None) -> dict:
    payload: dict[str, Any] = {"messages": messages, "max_tokens": 64, "temperature": 0}
    if extra:
        payload.update(extra)
    return {"id": cid, "dimension": "D9", "name": name, "payload": payload, "judge": "behavior_class"}


CASES: list[dict] = [
    # ── D1 接口与参数语义 ──────────────────────────────
    {"id": "D1-max-tokens", "dimension": "D1", "name": "max_tokens 是否被遵守（256）",
     "payload": {"messages": [{"role": "user", "content": "写一段约 300 字的短文，主题自选。"}],
                 "max_tokens": 256, "temperature": 0},
     "judge": "max_tokens_compliance"},
    {"id": "D1-thinking-fields", "dimension": "D1", "name": "usage 是否返回 reasoning_tokens",
     "payload": {"messages": [{"role": "user", "content": "9.11 和 9.9 哪个大？说明理由。"}],
                 "max_tokens": 128, "temperature": 0},
     "judge": "reasoning_field"},
    {"id": "D1-system-role", "dimension": "D1", "name": "system 角色是否被尊重（只用英文）",
     "payload": {"messages": [{"role": "system", "content": "You must answer in English only."},
                              {"role": "user", "content": "介绍一下杭州。"}],
                 "max_tokens": 96, "temperature": 0},
     "judge": "system_role"},

    # ── D2 指纹（tokenizer / 模板）────────────────────
    *[{"id": f"D2-fp-{key}", "dimension": "D2", "name": f"tokenizer 指纹：{key}",
       "payload": {"messages": [{"role": "user", "content": text}],
                   "max_tokens": 16, "temperature": 0},
       "judge": "tokenizer_fingerprint"}
      for key, text in _FP_TEXTS],

    # ── D8 结构与工具调用 ─────────────────────────────
    {"id": "D8-json-output", "dimension": "D8", "name": "JSON 结构化输出",
     "payload": {"messages": [{"role": "user",
                               "content": '只输出 JSON，不要任何解释：{"city":"杭州","weather":"晴","temp_c":20}'}],
                 "max_tokens": 96, "temperature": 0},
     "judge": "json_output"},
    {"id": "D8-tool-call", "dimension": "D8", "name": "function calling（强制调用工具）",
     "payload": {"messages": [{"role": "user", "content": "杭州今天天气怎么样？"}],
                 "tools": _TOOLS, "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
                 "max_tokens": 128, "temperature": 0},
     "judge": "tool_call"},

    # ── D9 协议健壮性（孤儿 tool 等）───────────────────
    _tool_case("D9-orphan-tool", "孤儿 tool 结果（无对应 tool_calls）",
               [{"role": "user", "content": "杭州天气如何？"},
                {"role": "tool", "tool_call_id": "call_X", "content": '{"weather":"晴"}'}]),
    _tool_case("D9-id-mismatch", "tool_call_id 不匹配",
               [{"role": "user", "content": "杭州天气如何？"},
                {"role": "assistant", "content": None, "tool_calls": [_TOOL_A]},
                {"role": "tool", "tool_call_id": "call_WRONG", "content": '{"weather":"晴"}'}]),
    _tool_case("D9-dangling-call", "悬空 tool_calls（缺结果）",
               [{"role": "user", "content": "杭州天气如何？"},
                {"role": "assistant", "content": None, "tool_calls": [_TOOL_A]},
                {"role": "user", "content": "那明天呢？"}]),
    _tool_case("D9-dup-result", "重复 tool 结果",
               [{"role": "user", "content": "杭州天气如何？"},
                {"role": "assistant", "content": None, "tool_calls": [_TOOL_A]},
                {"role": "tool", "tool_call_id": "call_A", "content": '{"weather":"晴"}'},
                {"role": "tool", "tool_call_id": "call_A", "content": '{"weather":"晴"}'}]),
    _tool_case("D9-parallel", "并行 tool_calls + 两个结果",
               [{"role": "user", "content": "杭州的天气和时间？"},
                {"role": "assistant", "content": None, "tool_calls": [_TOOL_A, _TOOL_B]},
                {"role": "tool", "tool_call_id": "call_A", "content": '{"weather":"晴"}'},
                {"role": "tool", "tool_call_id": "call_B", "content": '{"time":"10:00"}'}]),
    _tool_case("D9-tool-choice-missing", "tool_choice 指向不存在的工具",
               [{"role": "user", "content": "查一下杭州天气"}],
               extra={"tools": _TOOLS,
                      "tool_choice": {"type": "function", "function": {"name": "not_exist"}}}),
]

DIMENSIONS = {
    "D1": {"name": "接口与参数语义", "weight": 12, "cases": [c for c in CASES if c["dimension"] == "D1"]},
    "D2": {"name": "指纹一致性", "weight": 20, "cases": [c for c in CASES if c["dimension"] == "D2"]},
    "D8": {"name": "结构与工具调用", "weight": 15, "cases": [c for c in CASES if c["dimension"] == "D8"]},
    "D9": {"name": "协议健壮性（孤儿 tool 等）", "weight": 10,
           "cases": [c for c in CASES if c["dimension"] == "D9"]},
}


def cases_for(dimensions: list[str]) -> list[dict]:
    return [c for c in CASES if c["dimension"] in set(dimensions)]
