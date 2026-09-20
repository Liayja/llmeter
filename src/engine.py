"""
大模型 API 压测引擎
====================
纯 Chat Completions，支持流式/非流式。
"""
import time
import json
import gc
import secrets
import statistics
import httpx
import asyncio

try:
    import tiktoken
    TIKTOKEN = True
except ImportError:
    TIKTOKEN = False

# 默认编码器，用于 token 估算
_ENCODER = None

# 默认的 Chat Completions 路径；不同厂商的路径不同（如智谱为 /chat/completions）
DEFAULT_CHAT_PATH = "/v1/chat/completions"


def build_chat_url(base_url: str, endpoint: str | None = None) -> str:
    """拼接 Chat Completions 完整 URL。

    endpoint 为空时用默认 /v1/chat/completions；各厂商路径不同时可显式指定，
    例如智谱：base_url=https://open.bigmodel.cn/api/paas/v4、endpoint=/chat/completions。
    另外兼容几种"用户直接粘贴完整地址/只填到 /v1"的写法，避免拼出重复路径。
    """
    base = (base_url or "").strip().rstrip("/")
    path = (endpoint or DEFAULT_CHAT_PATH).strip()
    # 容错 1：接口路径里填了完整地址（用户常见的填反）→ 直接当完整 URL 用
    if path.startswith(("http://", "https://")):
        return path.rstrip("/")
    # 容错 2：Base URL 为空 → 给出可执行的明确错误，而不是让 httpx 抛 UnsupportedProtocol
    if not base:
        raise ValueError("Base URL 未配置（为空）：请在「资产库 → 连接」里填写，例如 https://api.deepseek.com；"
                         "接口路径只填 /chat/completions 这类路径")
    # 容错 3：Base URL 少了协议前缀（如 open.bigmodel.cn/api/paas）→ 补 https://
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    if not path.startswith("/"):
        path = "/" + path
    # 用户直接粘贴了完整的 chat/completions 地址
    if base.endswith("/chat/completions"):
        return base
    # 显式指定的路径已经包含在 base 末尾
    if base.endswith(path):
        return base
    # base 末尾已含路径首段（如 base=.../v1 + path=/v1/chat/completions，
    # 或 base=.../v4 + path=/v4/chat/completions）：去掉重复段
    first_seg = path.strip("/").split("/")[0]
    if first_seg and base.endswith("/" + first_seg):
        rest = path[len("/" + first_seg):]
        return base + (rest or "")
    return base + path


def build_chat_payload(*, model: str, prompt: str, stream: bool = True,
                       max_tokens: int = 256, temperature: float | None = None,
                       extra_params: dict | None = None) -> dict:
    """构造 Chat Completions 请求体。

    压测与「连通性测试/结构调试」共用这一个函数，保证调试时看到的请求体
    就是压测实际发出的请求体（字段顺序与覆盖规则完全一致）。
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": stream,
    }
    # temperature 仅在显式配置时发送：部分模型对取值有白名单限制，
    # 不配置就不发，让服务端用自己的默认值。
    if temperature is not None:
        payload["temperature"] = temperature
    # extra_params 放最后，允许场景/模型级配置覆盖上面的字段
    if extra_params:
        payload.update(extra_params)
    # stream_options.include_usage 是 OpenAI 扩展字段，用于在流式响应末尾取准确 usage
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


# extra_params 里这些字段必须是数字；写成字符串（如 "max_tokens": "256"）会被
# 不少网关判为非法类型而**静默忽略**，回落到服务端默认值（常表现为"配置没生效"）。
_NUMERIC_EXTRA_PARAMS = {
    "max_tokens": int, "max_completion_tokens": int, "n": int, "seed": int,
    "best_of": int, "top_k": int, "logprobs": int, "top_logprobs": int,
    "temperature": float, "top_p": float,
    "presence_penalty": float, "frequency_penalty": float,
}


def normalize_extra_params(extra_params: dict | None) -> tuple[dict, list[str]]:
    """规范化 extra_params：数字字符串转数字，返回 (参数, 提示列表)。

    例：{"max_tokens": "256"} → {"max_tokens": 256}，并给出提示；
    非数字字符串只提示、不改动（交给服务端报错更安全）。
    """
    if not extra_params:
        return {}, []
    out = dict(extra_params)
    notes: list[str] = []
    for key, value in list(out.items()):
        expected = _NUMERIC_EXTRA_PARAMS.get(key)
        if expected is None or not isinstance(value, str):
            continue
        text = value.strip()
        try:
            converted = expected(float(text))
            out[key] = converted
            notes.append(f'extra_params.{key} 由字符串 "{text}" 规范化为数字 {converted}'
                         "（字符串会被多数网关忽略，导致参数不生效）")
        except ValueError:
            notes.append(f'extra_params.{key} 的值 "{text}" 不是数字，服务端可能忽略该参数')
    return out, notes


def _get_encoder():
    """延迟加载 tiktoken 编码器"""
    global _ENCODER
    if _ENCODER is None and TIKTOKEN:
        try:
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            pass
    return _ENCODER


def count_tokens(text: str) -> int:
    """计算文本的 token 数（优先 tiktoken，回退到字符估算）。

    注意: tiktoken 使用 cl100k_base 编码器（OpenAI 模型专用）。
    对于 GLM / DeepSeek 等非 OpenAI 模型，仅在 API 不返回 usage 时作为
    粗略估算使用，实际 token 数可能偏差 20~50%。
    """
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text))
    # 回退：中文 ~1.5 char/token，英文 ~4 char/token
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def _pct(data: list, p: int) -> float:
    """线性插值百分位数计算"""
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    k = (n - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 >= n:
        return s[-1]
    return s[f] + c * (s[f + 1] - s[f])
def _tpot(r: dict) -> float | None:
    """计算单个请求的 TPOT（Time Per Output Token，每 token 平均生成时间）。

    流式: (latency - TTFT) / (output_tokens - 1)，剔除 prefill 时间后的纯生成速度。
    非流式: latency / output_tokens，含 prefill 的均值。
    """
    out = r.get("output_tokens", 0) or 0
    if out <= 0:
        return None
    lat = r.get("latency", 0)
    ttft = r.get("ttft")
    if ttft is not None and out > 1:
        return (lat - ttft) / (out - 1)
    return lat / out


def _extract_cached_tokens(usage) -> int | None:
    """从 usage.prompt_tokens_details.cached_tokens 读取缓存命中 token 数。

    返回 int（可能为 0，表示该请求没有命中缓存）；
    若 API 未返回该字段（无缓存统计能力），返回 None，与「命中 0 个」区分开。
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        try:
            return int(details["cached_tokens"] or 0)
        except (TypeError, ValueError):
            return None
    return None


def aggregate(results: list[dict], wall_time: float, *, tpot_mode: str | None = None) -> dict:
    """原始结果 → 聚合指标

    tpot_mode: "stream" / "nonstream"，用于标注 TPOT 口径——流式剔除 prefill、
    非流式含 prefill，两者不可直接比较。
    """
    ok = [r for r in results if r["success"]]
    lat = [r["latency"] for r in ok]
    ttfts = [r["ttft"] for r in ok if r.get("ttft")]
    ttft_answers = [r["ttft_answer"] for r in ok if r.get("ttft_answer")]
    thinking_times = [r["thinking_time"] for r in ok if r.get("thinking_time")]
    out_tokens = sum(r.get("output_tokens", 0) for r in ok)
    in_tokens = sum(r.get("input_tokens", 0) for r in ok)
    reasoning_tokens_total = sum(r.get("reasoning_tokens", 0) for r in ok)
    tpot_vals = [v for r in ok if (v := _tpot(r)) is not None]
    # ITL (Inter-Token Latency): 展平所有请求的逐 token 间隔
    itl_all: list[float] = []
    for r in ok:
        itl_all.extend(r.get("itl") or [])
    # ── 失败三分类（对齐 GuideLLM 的 success / incomplete / error）：
    #    incomplete: 请求未到达服务端或未拿到完整响应（超时/连接中断，status_code=0）
    #    error:      服务端明确返回 HTTP 4xx/5xx 错误
    bad = [r for r in results if not r["success"]]
    incomplete = [r for r in bad if r.get("status_code", 0) == 0]
    err_resp = [r for r in bad if r.get("status_code", 0) != 0]
    retried = [r for r in results if r.get("retried")]
    m = {
        "total": len(results),
        "success": len(ok),
        "fail": len(bad),              # 兼容旧字段 = incomplete + error_count
        "incomplete": len(incomplete), # 超时/连接中断（未完成）
        "error_count": len(err_resp),  # HTTP 4xx/5xx（服务端明确报错）
        "retried": len(retried),       # 至少重试过一次的请求数（含最终成功/失败）
        "success_rate": len(ok) / len(results) * 100 if results else 0,
        "qps": len(ok) / wall_time if wall_time > 0 else 0,
        "tps": out_tokens / wall_time if wall_time > 0 else 0,
        "total_input_tokens": in_tokens,
        "total_output_tokens": out_tokens,
        "total_tokens": in_tokens + out_tokens,    # 场景总 token 消耗（≈ 花了多少额度）
        "total_reasoning_tokens": reasoning_tokens_total,
        "avg_input_tokens": round(in_tokens / len(results)) if results else 0,
        "avg_output_tokens": round(out_tokens / len(ok)) if ok else 0,
        "latency_avg": statistics.mean(lat) if lat else 0,
        "latency_std": statistics.stdev(lat) if len(lat) >= 2 else 0,  # 延迟标准差
        "latency_p50": _pct(lat, 50),
        "latency_p75": _pct(lat, 75),
        "latency_p90": _pct(lat, 90),
        "latency_p95": _pct(lat, 95),
        "latency_p98": _pct(lat, 98),
        "latency_p99": _pct(lat, 99),
        "latency_p999": _pct(lat, 99.9),
        "latency_min": min(lat) if lat else 0,
        "latency_max": max(lat) if lat else 0,
        "errors": [
            {"code": r.get("status_code", 0), "msg": r.get("error", "")[:200]}
            for r in bad
        ][:10],
        "tpot_mode": tpot_mode or "",
    }
    if ttfts:
        m["ttft_avg"] = statistics.mean(ttfts)
        m["ttft_p50"] = _pct(ttfts, 50)
        m["ttft_p75"] = _pct(ttfts, 75)
        m["ttft_p90"] = _pct(ttfts, 90)
        m["ttft_p95"] = _pct(ttfts, 95)
        m["ttft_p98"] = _pct(ttfts, 98)
        m["ttft_p99"] = _pct(ttfts, 99)
        m["ttft_p999"] = _pct(ttfts, 99.9)
    if ttft_answers:
        m["ttft_answer_avg"] = statistics.mean(ttft_answers)
        m["ttft_answer_p50"] = _pct(ttft_answers, 50)
        m["ttft_answer_p95"] = _pct(ttft_answers, 95)
    if thinking_times:
        m["thinking_time_avg"] = statistics.mean(thinking_times)
        m["thinking_time_p50"] = _pct(thinking_times, 50)
        m["thinking_time_p95"] = _pct(thinking_times, 95)
    if tpot_vals:
        m["tpot_avg"] = statistics.mean(tpot_vals)
        m["tpot_p50"] = _pct(tpot_vals, 50)
        m["tpot_p95"] = _pct(tpot_vals, 95)
    if itl_all:
        m["itl_avg"] = statistics.mean(itl_all)
        m["itl_p50"] = _pct(itl_all, 50)
        m["itl_p95"] = _pct(itl_all, 95)
        m["itl_max"] = max(itl_all)
    # ── Prompt 缓存命中（仅当请求带 cached_tokens 字段才有意义）──
    # 两个不同口径，分开输出：
    #   cache_hit_rate          整体（token 加权）= 命中 token 总数 ÷ 上报请求的 prompt 总数
    #   cache_hit_rate_per_req  平均每请求 = 各请求自身命中率（cached_i/prompt_i）的算术平均
    # 前者对应成本节约（大头请求权重高），后者反映"平均一个请求能命中多少比例"，
    # 两者不同，不能混为一谈。假设 cached ⊆ prompt（OpenAI 口径），故封顶 100%。
    cache_reported = [r for r in ok if r.get("cached_tokens") is not None]
    if cache_reported:
        cached_total = sum(r["cached_tokens"] for r in cache_reported)
        cache_prompt = sum(r.get("input_tokens", 0) for r in cache_reported)
        per_req = [
            min(100.0, r["cached_tokens"] / r["input_tokens"] * 100)
            for r in cache_reported if r.get("input_tokens", 0) > 0
        ]
        m["cache_reported_requests"] = len(cache_reported)
        m["cached_tokens_total"] = cached_total
        m["cache_prompt_total"] = cache_prompt
        m["cache_hit_rate"] = min(100.0, cached_total / cache_prompt * 100) if cache_prompt > 0 else 0.0
        m["cache_hit_rate_per_req"] = statistics.mean(per_req) if per_req else 0.0
    return m
# ═══════════════════════════════════════
# 引擎
# ═══════════════════════════════════════
# 瞬时可重试的状态码
_RETRYABLE_CODES = {429, 500, 502, 503}


class LLMBench:
    """大模型 API 压测引擎"""
    def __init__(self, *, base_url: str, api_key: str, model: str, prompt: str,
                 concurrency: int = 10, total_requests: int = 100,
                 max_tokens: int = 256, temperature: float | None = None,
                 stream: bool = True, timeout: int = 120, output_dir: str = "./results",
                 retries: int = 2, retry_backoff: float = 1.0,
                 read_timeout: float | None = None,
                 unique_prefix: bool = False,
                 verbose: bool = False,
                 extra_params: dict | None = None,
                 http2: bool = False,
                 warmup: int = 2,
                 track_cache: bool = True,
                 qps: float | None = None,
                 duration: int | None = None,
                 endpoint: str | None = None,
                 on_progress=None):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.prompt = prompt
        self.concurrency = concurrency
        self.total_requests = total_requests
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.stream = stream
        self.timeout = timeout
        self.read_timeout = read_timeout
        self.unique_prefix = unique_prefix
        self.output_dir = output_dir
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.verbose = verbose
        # 规范化 extra_params（数字字符串 → 数字），并保留提示供 run() 打印
        self.extra_params, self._param_notes = normalize_extra_params(extra_params)
        self.http2 = http2
        self.warmup = warmup
        self.track_cache = track_cache
        # 接口路径：默认 /v1/chat/completions，可按厂商覆盖（如智谱 /chat/completions）
        self.endpoint = endpoint
        self.chat_url = build_chat_url(self.base_url, endpoint)
        # ── 与本地 Web 平台的对接钩子（CLI 不传时行为完全不变）──
        #   on_progress: 每完成一个请求 / 每秒监控时回调一次 dict；为 None 时走终端打印
        #   request_cancel(): 置位后不再发起新请求，在途请求自然收尾
        self._on_progress = on_progress
        self._cancelled = False
        self._started_at: float | None = None   # 本轮开始的单调时钟，用于实时速率/ETA
        self._finish_times: list[float] = []     # 各请求完成时刻，用于滑动窗口速率
        self._retry_events: list[dict] = []      # 最近的重试事件，供 Web 日志展示原因
        self._retry_seq = 0
        # ── 发压模型：
        #    qps 未设 → 闭环(closed-loop)：Semaphore 限制在途 concurrency 个，
        #               一个回来才补下一个，测「N 路并发下的饱和吞吐/延迟」。
        #    qps 已设 → 开环(open-loop)：按固定到达率发车，不管服务器多慢，
        #               测「稳定 X QPS 流量下服务器能否扛住」，暴露真实积压与尾延迟
        #               (规避闭环的 coordinated omission)。
        self.qps = qps
        self.duration = duration
        # 开环 + 指定时长 → 总请求数 = qps × duration（对齐 loadtest 语义）
        if self.qps and self.duration:
            self.total_requests = round(self.qps * self.duration)
        self.results: list[dict] = []
        self._verbose_used = False  # 仅首请求打印诊断信息
        # ── 分离超时：connect/write/pool 短(快速发现连接卡死)，
        #    read 用 read_timeout 或 timeout。流式下 read = 相邻 chunk 最大间隔，
        #    因此长生成(8192 token)不会误判为超时，而连接卡死能快速失败。
        connect_t = min(10.0, float(timeout))
        self._timeout_cfg = httpx.Timeout(
            connect=connect_t,
            read=float(read_timeout) if read_timeout is not None else float(timeout),
            write=connect_t,
            pool=connect_t,
        )
        # ── 并发水位计数器（asyncio 单线程协程，无需锁）
        self._active = 0      # 当前占并发槽的请求数
        self._peak = 0        # 历史峰值
        self._completed = 0
        self._failed = 0
    async def run(self) -> dict:
        open_loop = bool(self.qps)
        if not self.stream:
            print("⚠️  非流式模式：TTFT / ITL 指标将不可用（非流式 API 无法测量首 token 时间）")
        # extra_params 会覆盖顶层字段（设计如此），但静默覆盖极易误判"配置没生效"，
        # 这里在开跑前把冲突显式打出来。
        if self.extra_params:
            base = build_chat_payload(model=self.model, prompt="", stream=self.stream,
                                      max_tokens=self.max_tokens,
                                      temperature=self.temperature)
            conflicts = [f"{k}: {base[k]} → {self.extra_params[k]}"
                         for k in self.extra_params
                         if k in base and base[k] != self.extra_params[k]]
            if conflicts:
                print("⚠️  extra_params 覆盖了以下参数（实际以覆盖后的值为准）："
                      + "；".join(conflicts))
        for note in self._param_notes:
            print(f"⚠️  {note}")
        self.results = []
        self._active = 0
        self._peak = 0
        self._completed = 0
        self._failed = 0
        # ── HTTP/1.1(默认) vs HTTP/2:
        #    HTTP/2 会把同一 origin 的并发请求多路复用到「一条」TCP 连接上,
        #    并发压测下 N 个"客户端"其实共享一个拥塞窗口,测不出真实并行度。
        #    默认走 HTTP/1.1,让连接池为每个并发请求开独立 TCP 连接(真并行)。
        # ── 连接池上限:
        #    闭环: 同步放大到 concurrency*2,否则空闲连接被回收后重建。
        #    开环: 不设上限(None)。若限制连接数,超额请求会在连接池排队,
        #          等于把开环退化成闭环——积压被连接池悄悄吸收,测不出真实到达率压力。
        if open_loop:
            sem = asyncio.Semaphore(10 ** 9)  # 开环不靠信号量限流,仅用于复用 _active 水位统计
            limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
        else:
            sem = asyncio.Semaphore(self.concurrency)
            limits = httpx.Limits(
                max_connections=self.concurrency * 2,
                max_keepalive_connections=self.concurrency * 2,
            )
        async with httpx.AsyncClient(http2=self.http2, limits=limits) as client:
            # ── 预热:先打 warmup 个丢弃请求,把 TCP+TLS 握手和连接池建立
            #    从正式计测的首批 TTFT 中剔除(否则冷连接握手会污染首个 TTFT)。
            if self.warmup > 0:
                print(f"🔥 预热 {self.warmup} 个请求（不计入结果）...", flush=True)
                warm = [self._request(client, sem, -1) for _ in range(self.warmup)]
                await asyncio.gather(*warm, return_exceptions=True)
                # 重置被预热污染的水位计数器
                self._active = 0
                self._peak = 0
                self._completed = 0
                self._failed = 0
                self._retry_events = []
                self._retry_seq = 0
            # 计时从预热之后开始：预热请求不产出结果，若计入会低估速率、放大 ETA
            self._started_at = time.perf_counter()
            self._finish_times = []
            # 后台水位监控：每秒打印完成数/活跃流/峰值/TTFT均值/失败数
            monitor = asyncio.create_task(self._monitor())
            dispatch_phase = drain_phase = 0.0
            try:
                gc.disable()
                if open_loop:
                    dispatch_phase, drain_phase, wall = await self._dispatch_open(client, sem)
                else:
                    wall = await self._dispatch_closed(client, sem)
            finally:
                gc.enable()
                monitor.cancel()
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass
        if open_loop:
            actual_qps = len(self.results) / wall if wall > 0 else 0
            print(f"⏱️  总耗时: {wall:.1f}s（发车 {dispatch_phase:.1f}s + 排空 {drain_phase:.1f}s）"
                  f"  | 目标 {self.qps} QPS → 实际完成 {actual_qps:.2f} req/s"
                  f"  | 在途峰值(积压): {self._peak}")
        else:
            print(f"⏱️  总耗时: {wall:.1f}s  | 并发峰值: {self._peak}/{self.concurrency}")
        metrics = aggregate(self.results, wall, tpot_mode="stream" if self.stream else "nonstream")
        metrics["concurrency_peak"] = self._peak
        # 服务端是否遵守 max_tokens：只统计拿到 usage 的成功请求，避免本地估算误报
        over_limit = [r for r in self.results
                      if r.get("success") and r.get("usage_reported")
                      and r.get("output_tokens", 0) > self.max_tokens > 0]
        metrics["max_tokens_requested"] = self.max_tokens
        metrics["output_tokens_over_limit"] = len(over_limit)
        metrics["output_tokens_max"] = max((r.get("output_tokens", 0) for r in over_limit), default=0)
        if over_limit:
            print(f"⚠️  服务端未遵守 max_tokens：请求上限 {self.max_tokens}，"
                  f"但有 {len(over_limit)} 个请求输出超过该值（最大 {metrics['output_tokens_max']}）"
                  "——通常是网关忽略该参数或使用默认上限，建议用「调试」看请求体并向服务商确认")
        if open_loop:
            metrics["mode"] = "open"
            metrics["qps_target"] = self.qps
            metrics["concurrency_target"] = None
            metrics["dispatch_phase"] = dispatch_phase
            metrics["drain_phase"] = drain_phase
            metrics["qps_dispatch"] = len(self.results) / dispatch_phase if dispatch_phase > 0 else 0
        else:
            metrics["mode"] = "closed"
            metrics["concurrency_target"] = self.concurrency
        if self.track_cache and "cache_reported_requests" not in metrics:
            print("ℹ️  未检测到 prompt_tokens_details.cached_tokens 字段，缓存命中率不可用"
                  "（可加 --no-track-cache 关闭采集）")
        metrics["canceled"] = self._cancelled
        if self._on_progress is not None:
            self._emit_progress(final=True)   # 给 Web 端补一个结束事件（CLI 不输出）
        return metrics

    def request_cancel(self) -> None:
        """请求中止压测：不再发起新请求，已在途的请求等其自然结束。"""
        self._cancelled = True

    def _emit_progress(self, final: bool = False) -> None:
        """上报进度：有 on_progress 回调则回调，否则打印到终端。

        回调载荷同时包含实时速率、延迟、ETA、成功/失败/重试和 token 吞吐，
        供 Web 指标面板使用；终端日志不再重复打印 ETA，避免把"估算值"当结果刷屏。
        active/peak 仍然保留：开环模式下它们表示在途积压（判断服务器是否扛得住）。
        """
        now = time.perf_counter()
        elapsed = (now - self._started_at) if self._started_at else 0.0
        ok_results = [r for r in self.results if r.get("success")]
        success_count = len(ok_results)
        retried_count = sum(1 for r in self.results if r.get("retried"))
        input_tokens_total = sum(r.get("input_tokens") or 0 for r in self.results)
        output_tokens_total = sum(r.get("output_tokens") or 0 for r in self.results)
        cached_values = [r.get("cached_tokens") for r in self.results
                         if r.get("cached_tokens") is not None]
        cached_tokens_total = sum(cached_values)
        cached_prompt_total = sum(
            (r.get("input_tokens") or 0) for r in self.results
            if r.get("cached_tokens") is not None
        )
        cache_hit_rate = (
            min(100.0, cached_tokens_total / cached_prompt_total * 100)
            if cached_prompt_total > 0 else None
        )
        output_tps = (output_tokens_total / elapsed) if elapsed > 0 else 0.0
        ttfts = [r["ttft"] for r in self.results if r.get("ttft") is not None]
        ttft_avg = statistics.mean(ttfts) if ttfts else None
        latencies = [r["latency"] for r in ok_results if r.get("latency")]
        latency_avg = statistics.mean(latencies) if latencies else None
        # 速率优先用最近 20s 滑动窗口（贴近当前速度）；窗口未填充时退回累计平均。
        # ETA 在样本太少时不给数，避免开跑初期出现"上千秒"这种误导值。
        overall_rate = (self._completed / elapsed) if elapsed > 0 and self._completed else 0.0
        window = 20.0
        span = min(window, elapsed)
        recent_count = sum(1 for t in self._finish_times if now - t <= window)
        rate = (recent_count / span) if span >= 3 and recent_count > 1 else overall_rate
        remaining = max(self.total_requests - self._completed, 0)
        eta = (remaining / rate) if rate > 0 and self._completed >= 3 else None
        last_result = self.results[-1] if self.results else {}
        payload = {
            "completed": self._completed,
            "total": self.total_requests,
            "success": success_count,
            "failed": self._failed,
            "retried": retried_count,
            "active": self._active,
            "peak": self._peak,
            "mode": "open" if self.qps else "closed",
            "concurrency_target": self.concurrency,
            "qps_target": self.qps,
            "elapsed": round(elapsed, 2),
            "rate": round(rate, 2),
            "rate_overall": round(overall_rate, 2),
            "eta": round(eta, 1) if eta is not None else None,
            "avg_latency": latency_avg,
            "ttft_avg": ttft_avg,
            "input_tokens": input_tokens_total,
            "output_tokens": output_tokens_total,
            "output_tps": round(output_tps, 2),
            "cached_tokens": cached_tokens_total,
            "cache_hit_rate": round(cache_hit_rate, 1) if cache_hit_rate is not None else None,
            "last_status": last_result.get("status_code"),
            "last_error": (last_result.get("error") or "")[:200],
            "last_retry": self._retry_events[-1] if self._retry_events else None,
            "retry_events": self._retry_events[-10:],
            "canceled": self._cancelled,
            "final": final,
        }
        if self._on_progress is not None:
            try:
                self._on_progress(payload)
            except Exception:
                pass  # 回调异常不能影响压测本身
            return
        ttft_str = f"{ttft_avg:.2f}s" if ttft_avg is not None else "—"
        if self.qps:
            # 开环：在途/峰值体现积压，是最关键的信号
            state = f"在途 {self._active}(峰值 {self._peak})"
        else:
            # 闭环：稳态即并发上限；只有没打满时才提示，避免刷屏无意义的 5/5
            state = (f"并发 {self._active}/{self.concurrency}"
                     if self._active < self.concurrency else f"并发已满 {self.concurrency}")
        print(
            f"  ⏳ {self._completed}/{self.total_requests} | "
            f"{state} | {rate:.2f} req/s | "
            f"TTFT {ttft_str} | 成功 {success_count} | 失败 {self._failed} | "
            f"重试 {retried_count} | 输出 {output_tps:.1f} tok/s",
            flush=True,
        )

    async def _run_and_collect(self, client, sem, idx: int) -> dict:
        """跑一个请求并登记结果 + 更新完成/失败计数（两种模式共用）。"""
        if self._cancelled:
            return {"success": False, "latency": 0, "error": "canceled",
                    "status_code": 0, "canceled": True}
        r = await self._request(client, sem, idx)
        if r.get("canceled"):
            return r  # 取消导致未发出的请求不登记、不计入完成数
        self.results.append(r)
        self._completed += 1
        self._finish_times.append(time.perf_counter())
        if not r["success"]:
            self._failed += 1
        self._emit_progress()
        return r

    async def _dispatch_closed(self, client, sem) -> float:
        """闭环发压：一次性排入全部请求，靠 Semaphore(concurrency) 限制在途数。"""
        t0 = time.perf_counter()
        tasks = []
        for i in range(self.total_requests):
            if self._cancelled:
                break
            tasks.append(asyncio.create_task(self._run_and_collect(client, sem, i)))
        await asyncio.gather(*tasks, return_exceptions=True)
        return time.perf_counter() - t0

    async def _dispatch_open(self, client, sem) -> tuple[float, float, float]:
        """开环发压：按固定到达率 qps 发车，不等前一个返回。

        用目标时间戳对齐发车(target = t0 + (i+1)/qps)以避免累积漂移；若服务器扛不住、
        发车已落后进度(sleep<=0)则立即发下一个——积压体现在 _active/_peak 水位上。

        返回 (发车耗时, 排空耗时, 总耗时)：
        - 发车耗时：从第一个请求到「最后一个请求发出」；
        - 排空耗时：发完车后等待所有在途请求返回的时间；
        - 总耗时 = 发车 + 排空，实际完成 QPS = success / 总耗时 会略低于目标到达率，
          这正是收尾排空的固有开销，报告中分开展示以便区分「发车阶段」与「排空阶段」。
        """
        interval = 1.0 / self.qps
        tasks = []
        t0 = time.perf_counter()
        for i in range(self.total_requests):
            if self._cancelled:
                break
            tasks.append(asyncio.create_task(self._run_and_collect(client, sem, i)))
            target = t0 + (i + 1) * interval
            sleep = target - time.perf_counter()
            if sleep > 0:
                await asyncio.sleep(sleep)
        dispatch_wall = time.perf_counter() - t0
        await asyncio.gather(*tasks, return_exceptions=True)
        total_wall = time.perf_counter() - t0
        return dispatch_wall, total_wall - dispatch_wall, total_wall

    async def _monitor(self):
        """每秒打印并发水位状态（完成数/活跃流/峰值/TTFT均值/失败数）。

        用于并发流式压测时直观判断：是否打满并发、有无排队堆积、TTFT 是否劣化。
        """
        try:
            while True:
                await asyncio.sleep(1)
                self._emit_progress()
                if self._completed >= self.total_requests or self._cancelled:
                    break
        except asyncio.CancelledError:
            return
    async def _request(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, idx: int) -> dict:
        # 防 prefix cache:每个请求在文本前加唯一 nonce 前缀,破坏缓存。
        # 代价:前缀本身也被算入 prefill(约 12 token),对 400k 量级可忽略。
        # 注:部分厂商对 user-role 离散 prompt 缓存,反而可能比同前缀更慢。
        if self.unique_prefix:
            nonce = secrets.token_hex(6)
            prompt = f"[req-{nonce}]\n{self.prompt}"
        else:
            prompt = self.prompt
        payload = build_chat_payload(
            model=self.model, prompt=prompt, stream=self.stream,
            max_tokens=self.max_tokens, temperature=self.temperature,
            extra_params=self.extra_params,
        )
        # api_key 为空时（本地/无需鉴权的自定义服务）不发送 Authorization 头
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.chat_url
        last_error = None
        retried = False  # 本请求是否至少重试过一次（计入 aggregate 的 retried 指标）
        for attempt in range(self.retries + 1):
            async with sem:
                # 排队等待期间可能已被取消：拿到槽位后再确认一次，避免继续发请求
                if self._cancelled:
                    return {"success": False, "latency": 0, "error": "canceled",
                            "status_code": 0, "canceled": True}
                # 占用并发槽：更新水位计数器（asyncio 单线程，无需锁）
                self._active += 1
                if self._active > self._peak:
                    self._peak = self._active
                t0 = time.perf_counter()
                try:
                    if self.stream:
                        result = await self._stream(client, url, payload, headers, t0)
                    else:
                        result = await self._nonstream(client, url, payload, headers, t0)
                except Exception as e:
                    result = {
                        "success": False,
                        "latency": time.perf_counter() - t0,
                        "error": str(e)[:500],
                        "status_code": 0,
                    }
                finally:
                    self._active -= 1
            # 非重试场景直接返回
            if result["success"]:
                result["retried"] = retried
                return result
            if attempt >= self.retries:
                result["retried"] = retried
                return result
            # 仅瞬时错误重试（限流/服务端错误/连接失败）
            code = result.get("status_code", 0)
            if code not in _RETRYABLE_CODES and code != 0:
                result["retried"] = retried
                return result
            # 退避等待后重试
            last_error = result.get("error", "")
            retried = True
            backoff = self.retry_backoff * (2 ** attempt)
            self._retry_seq += 1
            self._retry_events.append({
                "seq": self._retry_seq,
                "request_index": idx,
                "attempt": attempt + 1,
                "next_attempt": attempt + 2,
                "status_code": code,
                "error": (result.get("error") or "")[:300],
                "backoff": round(backoff, 2),
            })
            if len(self._retry_events) > 50:
                del self._retry_events[:-50]
            await asyncio.sleep(backoff)
        return {  # 理论上不会到这里，兜底
            "success": False,
            "latency": 0,
            "error": last_error or "retry exhausted",
            "status_code": 0,
            "retried": retried,
        }
    async def _stream(self, client, url, payload, headers, t0):
        """流式请求（信号量已由 _request 获取）

        健壮解析：支持 delta.content / message.content / text 等多种字段格式。
        通过 stream_options.include_usage 获取准确 token 数（回退到客户端估算）。
        """
        ttft = None        # 首 token 时间：reasoning_content 或 content 均触发
        ttft_answer = None # 首答案 token 时间：仅 content 触发（推理模型下 > ttft）
        ttfe = None  # Time To First Event: 首个 SSE chunk（含 role-only chunk）
        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        cached_tokens = None  # prompt 前缀缓存命中的 token 数（仅 track_cache 时采集）
        usage_reported = False  # 是否拿到服务端返回的 usage（判断 token 数是否可信）
        chunk_count = 0
        # ITL (Inter-Token Latency): 相邻内容 token 之间的间隔
        last_content_t = None
        itl_vals: list[float] = []
        try:
            async with client.stream("POST", url, json=payload,
                                     headers=headers, timeout=self._timeout_cfg) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    return {
                        "success": False,
                        "latency": time.perf_counter() - t0,
                        "error": body.decode(errors="replace")[:500],
                        "status_code": r.status_code,
                    }
                async for line in r.aiter_lines():
                    # 兼容 "data: " 和 "data:"（无空格）两种 SSE 格式
                    if not line.startswith("data:"):
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                    else:
                        data = line[5:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    chunk_count += 1

                    # 首 SSE 事件时间（用于诊断 prefill/连接延迟）
                    if ttfe is None:
                        ttfe = time.perf_counter() - t0

                    # verbose: 仅首请求打印前 5 个 chunk 诊断
                    do_verbose = self.verbose and not self._verbose_used
                    if do_verbose and chunk_count <= 5:
                        chunk_preview = json.dumps(chunk, ensure_ascii=False)
                        if len(chunk_preview) > 400:
                            chunk_preview = chunk_preview[:400] + "..."
                        print(f"  [verbose] chunk #{chunk_count}: {chunk_preview}")

                    # choices 可能缺失/为空/为 [null]（部分网关的非标准格式），
                    # 逐层兜底成 {}，避免后续 .get 打到 None。
                    choices = chunk.get("choices") or [{}]

                    # 从 usage chunk 提取准确 token 数（OpenAI/DeepSeek stream_options）
                    usage = chunk.get("usage")
                    if usage:
                        usage_reported = True
                        output_tokens = usage.get("completion_tokens", 0)
                        input_tokens = usage.get("prompt_tokens", 0)
                        # completion_tokens_details 可能为 null（键存在但值为 None），
                        # 此时 .get(默认{}) 仍返回 None，需再兜一次。
                        details = usage.get("completion_tokens_details") or {}
                        reasoning_tokens = details.get("reasoning_tokens", 0)
                        if self.track_cache:
                            cached_tokens = _extract_cached_tokens(usage)

                    # 尝试多种内容字段格式
                    msg = choices[0] or {}
                    content = ""
                    delta = msg.get("delta") or {}
                    if delta:
                        content = delta.get("content", "") or ""
                        if not content:
                            content = delta.get("text", "") or ""
                    if not content:
                        message = msg.get("message") or {}
                        content = message.get("content", "") or ""

                    # 推理内容（reasoning_content）：用于校准 TTFT，不计入答案文本
                    reasoning_content = ""
                    if delta:
                        reasoning_content = delta.get("reasoning_content", "") or ""

                    now_t = time.perf_counter()

                    # TTFT：第一个有效 token，推理 token 也算
                    if ttft is None and (content or reasoning_content):
                        ttft = now_t - t0

                    if content:
                        # 首答案 token
                        if ttft_answer is None:
                            ttft_answer = now_t - t0
                        # ITL: 本内容 chunk 与上一个内容 chunk 的时间差
                        if last_content_t is not None:
                            itl_vals.append(now_t - last_content_t)
                        last_content_t = now_t
                        text_parts.append(content)

            # verbose: 诊断摘要
            if self.verbose and not self._verbose_used:
                self._verbose_used = True
                print(f"  [verbose] 共收到 {chunk_count} 个 chunk，累积文本 {len(''.join(text_parts))} 字符")
                if ttfe is not None:
                    print(f"  [verbose] TTFE(首事件): {ttfe*1000:.0f}ms")
                if ttft is not None:
                    print(f"  [verbose] TTFT(首token,含推理): {ttft*1000:.0f}ms")
                if ttft_answer is not None and ttft_answer != ttft:
                    print(f"  [verbose] TTFT_answer(首答案): {ttft_answer*1000:.0f}ms  "
                          f"思考耗时: {(ttft_answer - ttft)*1000:.0f}ms")
                elif chunk_count > 0 and ttft is None:
                    print(f"  [verbose] ⚠️ 未在 chunk 中提取到文本内容，请检查上方 chunk 格式")

            full_text = "".join(text_parts)
            # 优先用 API 返回的准确值，回退到客户端估算
            if full_text:
                if not output_tokens:
                    output_tokens = count_tokens(full_text)
                if not input_tokens:
                    input_tokens = count_tokens(self.prompt)
            # TTFT 兜底：无任何 token 时用首个 SSE 事件时间
            res = {
                "success": True,
                "latency": time.perf_counter() - t0,
                "ttft": ttft if ttft is not None else ttfe,
                "ttft_answer": ttft_answer,
                "ttfe": ttfe,
                "thinking_time": (ttft_answer - ttft) if (ttft is not None and ttft_answer is not None and ttft_answer > ttft) else None,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "itl": itl_vals,
                "usage_reported": usage_reported,
            }
            if self.track_cache:
                res["cached_tokens"] = cached_tokens
            return res
        except Exception as e:
            return {
                "success": False,
                "latency": time.perf_counter() - t0,
                "error": str(e)[:500],
            }
    async def _nonstream(self, client, url, payload, headers, t0):
        """非流式请求（信号量已由 _request 获取）"""
        try:
            r = await client.post(url, json=payload, headers=headers, timeout=self._timeout_cfg)
            t = time.perf_counter() - t0
            if r.status_code != 200:
                return {
                    "success": False,
                    "latency": t,
                    "error": r.text[:500],
                    "status_code": r.status_code,
                }
            d = r.json()
            usage = d.get("usage", {})
            res = {
                "success": True,
                "latency": t,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "usage_reported": bool(usage),
            }
            if self.track_cache:
                res["cached_tokens"] = _extract_cached_tokens(usage)
            return res
        except Exception as e:
            return {
                "success": False,
                "latency": time.perf_counter() - t0,
                "error": str(e)[:500],
            }
