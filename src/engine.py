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


def aggregate(results: list[dict], wall_time: float) -> dict:
    """原始结果 → 聚合指标"""
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
    m = {
        "total": len(results),
        "success": len(ok),
        "fail": len(results) - len(ok),
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
        "latency_p95": _pct(lat, 95),
        "latency_p99": _pct(lat, 99),
        "latency_min": min(lat) if lat else 0,
        "latency_max": max(lat) if lat else 0,
        "errors": [
            {"code": r.get("status_code", 0), "msg": r.get("error", "")[:200]}
            for r in results if not r["success"]
        ][:10],
    }
    if ttfts:
        m["ttft_avg"] = statistics.mean(ttfts)
        m["ttft_p50"] = _pct(ttfts, 50)
        m["ttft_p95"] = _pct(ttfts, 95)
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
                 max_tokens: int = 256, temperature: float = 0.0,
                 stream: bool = True, timeout: int = 120, output_dir: str = "./results",
                 retries: int = 2, retry_backoff: float = 1.0,
                 read_timeout: float | None = None,
                 unique_prefix: bool = False,
                 verbose: bool = False,
                 extra_params: dict | None = None,
                 http2: bool = False,
                 warmup: int = 0):
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
        self.extra_params = extra_params or {}
        self.http2 = http2
        self.warmup = warmup
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
        if not self.stream:
            print("⚠️  非流式模式：TTFT / ITL 指标将不可用（非流式 API 无法测量首 token 时间）")
        sem = asyncio.Semaphore(self.concurrency)
        self.results = []
        self._active = 0
        self._peak = 0
        self._completed = 0
        self._failed = 0
        # ── HTTP/1.1(默认) vs HTTP/2:
        #    HTTP/2 会把同一 origin 的并发请求多路复用到「一条」TCP 连接上,
        #    并发压测下 N 个"客户端"其实共享一个拥塞窗口,测不出真实并行度。
        #    默认走 HTTP/1.1,让连接池为每个并发请求开独立 TCP 连接(真并行)。
        #    keepalive 上限同步放大到 concurrency*2,否则空闲连接会被回收后重建。
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
            # 后台水位监控：每秒打印完成数/活跃流/峰值/TTFT均值/失败数
            monitor = asyncio.create_task(self._monitor())
            try:
                gc.disable()
                t0 = time.perf_counter()
                tasks = [self._request(client, sem, i) for i in range(self.total_requests)]
                for coro in asyncio.as_completed(tasks):
                    r = await coro
                    self.results.append(r)
                    self._completed += 1
                    if not r["success"]:
                        self._failed += 1
                wall = time.perf_counter() - t0
            finally:
                gc.enable()
                monitor.cancel()
                try:
                    await monitor
                except asyncio.CancelledError:
                    pass
        print(f"⏱️  总耗时: {wall:.1f}s  | 并发峰值: {self._peak}/{self.concurrency}")
        metrics = aggregate(self.results, wall)
        metrics["concurrency_peak"] = self._peak
        metrics["concurrency_target"] = self.concurrency
        return metrics

    async def _monitor(self):
        """每秒打印并发水位状态（完成数/活跃流/峰值/TTFT均值/失败数）。

        用于并发流式压测时直观判断：是否打满并发、有无排队堆积、TTFT 是否劣化。
        """
        try:
            while True:
                await asyncio.sleep(1)
                # results 在主循环 append，列表推导无 await，单线程下原子读取
                ttfts = [r["ttft"] for r in self.results if r.get("ttft") is not None]
                ttft_str = f"{statistics.mean(ttfts):.2f}s" if ttfts else "—"
                print(
                    f"  ⏳ {self._completed}/{self.total_requests} | "
                    f"活跃流 {self._active} | 峰值 {self._peak} | "
                    f"TTFT均值 {ttft_str} | 失败 {self._failed}",
                    flush=True,
                )
                if self._completed >= self.total_requests:
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
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": self.stream,
            **self.extra_params,
        }
        # stream_options.include_usage 是 OpenAI 扩展字段，用于在流式响应
        # 末尾获取准确 token 数。部分代理/非 OpenAI API 可能不支持，此时
        # token 统计回退到客户端估算（见 count_tokens 注意事项）。
        if self.stream:
            payload["stream_options"] = {"include_usage": True}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/v1/chat/completions"
        last_error = None
        for attempt in range(self.retries + 1):
            async with sem:
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
                return result
            if attempt >= self.retries:
                return result
            # 仅瞬时错误重试（限流/服务端错误/连接失败）
            code = result.get("status_code", 0)
            if code not in _RETRYABLE_CODES and code != 0:
                return result
            # 退避等待后重试
            last_error = result.get("error", "")
            await asyncio.sleep(self.retry_backoff * (2 ** attempt))
        return {  # 理论上不会到这里，兜底
            "success": False,
            "latency": 0,
            "error": last_error or "retry exhausted",
            "status_code": 0,
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

                    choices = chunk.get("choices") or [{}]

                    # 从 usage chunk 提取准确 token 数（OpenAI/DeepSeek stream_options）
                    usage = chunk.get("usage")
                    if usage:
                        output_tokens = usage.get("completion_tokens", 0)
                        input_tokens = usage.get("prompt_tokens", 0)
                        reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

                    # 尝试多种内容字段格式
                    msg = choices[0]
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
            return {
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
            }
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
            return {
                "success": True,
                "latency": t,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        except Exception as e:
            return {
                "success": False,
                "latency": time.perf_counter() - t0,
                "error": str(e)[:500],
            }