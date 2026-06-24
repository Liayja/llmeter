"""
大模型 API 压测引擎
====================
纯 Chat Completions，支持流式/非流式。
"""
import time
import json
import gc
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
    """计算文本的 token 数（优先 tiktoken，回退到字符估算）"""
    enc = _get_encoder()
    if enc:
        return len(enc.encode(text))
    # 回退：中文 ~1.5 char/token，英文 ~4 char/token
    # 简单策略：统计中文字符数
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
    out_tokens = sum(r.get("output_tokens", 0) for r in ok)
    in_tokens = sum(r.get("input_tokens", 0) for r in ok)
    tpot_vals = [v for r in ok if (v := _tpot(r)) is not None]
    m = {
        "total": len(results),
        "success": len(ok),
        "fail": len(results) - len(ok),
        "success_rate": len(ok) / len(results) * 100 if results else 0,
        "qps": len(ok) / wall_time if wall_time > 0 else 0,
        "tps": out_tokens / wall_time if wall_time > 0 else 0,
        "total_input_tokens": in_tokens,
        "total_output_tokens": out_tokens,
        "latency_avg": statistics.mean(lat) if lat else 0,
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
    if tpot_vals:
        m["tpot_avg"] = statistics.mean(tpot_vals)
        m["tpot_p50"] = _pct(tpot_vals, 50)
        m["tpot_p95"] = _pct(tpot_vals, 95)
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
                 retries: int = 2, retry_backoff: float = 1.0):
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
        self.output_dir = output_dir
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.results: list[dict] = []
    async def run(self) -> dict:
        sem = asyncio.Semaphore(self.concurrency)
        self.results = []
        async with httpx.AsyncClient(http2=True, limits=httpx.Limits(
            max_connections=self.concurrency * 2
        )) as client:
            # 预热：复用同一客户端连接池，避免压测时冷启动
            print("⏳ 预热中...")
            warm_sem = asyncio.Semaphore(2)
            await asyncio.gather(*[
                self._request(client, warm_sem, -1) for _ in range(2)
            ])

            gc.disable()
            t0 = time.perf_counter()
            tasks = [self._request(client, sem, i) for i in range(self.total_requests)]
            done = 0
            for coro in asyncio.as_completed(tasks):
                self.results.append(await coro)
                done += 1
                if done % max(1, self.total_requests // 10) == 0:
                    print(f"  进度: {done}/{self.total_requests}")
            wall = time.perf_counter() - t0
            gc.enable()
        print(f"⏱️  总耗时: {wall:.1f}s")
        return aggregate(self.results, wall)
    async def _request(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, idx: int) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": self.prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": self.stream,
        }
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
        ttft = None
        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        try:
            async with client.stream("POST", url, json=payload,
                                     headers=headers, timeout=self.timeout) as r:
                if r.status_code != 200:
                    body = await r.aread()
                    return {
                        "success": False,
                        "latency": time.perf_counter() - t0,
                        "error": body.decode(errors="replace")[:500],
                        "status_code": r.status_code,
                    }
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or [{}]

                    # 从 usage chunk 提取准确 token 数（OpenAI/DeepSeek stream_options）
                    usage = chunk.get("usage")
                    if usage:
                        output_tokens = usage.get("completion_tokens", 0)
                        input_tokens = usage.get("prompt_tokens", 0)

                    # 尝试多种内容字段格式
                    msg = choices[0]
                    content = ""
                    delta = msg.get("delta") or {}
                    if delta:
                        content = delta.get("content", "")
                        if not content:
                            content = delta.get("text", "")
                    if not content:
                        message = msg.get("message") or {}
                        content = message.get("content", "")

                    if content:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        text_parts.append(content)

            full_text = "".join(text_parts)
            # 优先用 API 返回的准确值，回退到客户端估算
            if full_text:
                if not output_tokens:
                    output_tokens = count_tokens(full_text)
                if not input_tokens:
                    input_tokens = count_tokens(self.prompt)
            return {
                "success": True,
                "latency": time.perf_counter() - t0,
                "ttft": ttft,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
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
            r = await client.post(url, json=payload, headers=headers, timeout=self.timeout)
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