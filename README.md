# llmeter

LLM API 压测基准工具，通过 OpenAI 兼容的 Chat Completions 接口对模型进行性能测试。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化配置文件
cp config/config.yaml.example config/config.yaml        # 编辑填入你的 API Key
cp config/scenarios.yaml.example config/scenarios.yaml

# 单次压测
python bench.py chat -f config/config.yaml

# 多场景对比压测
python bench.py scenarios -f config/scenarios.yaml
```

## 压测指标

每次压测输出 20+ 个聚合指标：

| 类别 | 指标 |
|------|------|
| 基础 | 总请求数、成功数、失败数、**未完成(超时/中断)**、**HTTP错误(4xx/5xx)**、**重试数**、成功率 |
| 吞吐 | QPS（请求/秒）、TPS（Token/秒） |
| 延迟 | Avg / P50 / **P75 / P90** / P95 / **P98** / P99 / **P99.9** / Min / Max |
| TTFT | 首 Token 延迟 Avg / P50 / **P90** / P95 / **P99 / P99.9**（仅流式） |
| Token | 输入/输出 Token 总量 |
| 缓存 | Prompt 缓存命中率、命中 Token（API 返回 cached_tokens 时自动统计，`--no-track-cache` 关闭） |
| 错误 | 失败请求详情（状态码 + 错误消息，前 10 条） |
| 模式 | 开环/闭环、流式/非流式（TPOT 口径标注）、发车/排空两阶段耗时 |

> 默认**流式**（`--no-stream` 显式关闭）并预热 2 个请求；重试后成功的请求会计入成功，
> 追求真实成功率时可设 `retries: 0` 或参考报告中的「重试」计数。

## 输出

- **终端**：rich 彩色表格（回退到纯文本）
- **JSON**：单次压测完整结果（meta + summary + details）
- **Markdown + CSV**：多场景对比报告

结果保存在 `bench_results/YYYY-MM-DD/` 目录下。

## 本地 Web 压测平台（M1 · 开发中）

除了命令行，还可以用本地 Web 界面发起压测（界面操作，引擎与指标口径与 CLI 完全一致）。

```bash
# 1. 准备环境（conda；本机镜像不可达时可加 --override-channels -c https://repo.anaconda.com/pkgs/main）
conda env create -f environment.yml

# 2. 启动平台
conda run -n llmeter python -m app.main          # 默认 http://127.0.0.1:8765
conda run -n llmeter python -m app.main --port 9000   # 换端口
```

当前能力（M1 + M2）：

- **连接管理**：Base URL + 密钥，密钥支持「环境变量引用」与「本机加密存储（Windows DPAPI）」，界面只显示脱敏值；
- **接口路径可配置**：默认 `/v1/chat/completions`；智谱等厂商填 `/chat/completions`（Base URL 用 `https://open.bigmodel.cn/api/paas/v4`），也兼容直接粘贴完整地址；
- **模型管理**：显示名 / 模型 ID / 所属连接 / `extra_params`（推理开关）；
- **单模型压测**：并发、请求数、max_tokens、超时、warmup、retries、流式、缓存命中统计；
- **场景对比（1~N 模型 × 多场景，默认模式）**：最常用的是「单模型跑多场景梯度」（如 input-3k/10k/20k 找拐点），勾选 1 个模型即可；勾选多个模型则自动做横向对比，输出 Markdown / CSV / Excel 对比报告（引擎与 CLI 同口径）；
- **提示词库与场景模板**：提示词集中管理；场景模板可带覆盖参数（max_tokens、timeout、qps 等）；
- **一键批量建场景**：从项目 `prompts/` 目录勾选文件（按 token 数排序）批量生成场景，如前缀填「输入」+ 勾选 `input-3k/10k/20k` → 生成 `输入-3k`、`输入-10k`、`输入-20k`；
- **压测前连通性测试 / 结构调试**：用**与压测完全相同的请求体构造**发一次真实请求，回显请求体（URL、脱敏请求头、完整 payload）与响应体（状态码、耗时、TTFT、usage、原始分片/正文），并给出结构提示——
  usage 是否齐全、有没有 `cached_tokens` / `reasoning_tokens`、流式内容怎么解析、是否命中 401/404/429；
  入口：压测页「🔍 先试跑一次」（用当前表单参数）或资产库模型卡片「调试」；
- **实时进度**：SSE 推送完成数、实时速率、平均延迟、TTFT、ETA、失败数；闭环显示并发是否打满，开环显示在途积压（峰值），支持随时中止；
- **结果**：历史列表、指标详情、对比表、JSON / Excel 下载（产物仍写入 `bench_results/`）；
  缓存命中列在 API 未返回 `cached_tokens` 时显示 `—`，明确表示"未上报"而不是漏指标。

> 平台数据（SQLite 配置库）存放在 `app/data/`，已被 git 忽略。

## 项目结构

```
llmeter/
├── bench.py               # CLI 入口
├── src/
│   ├── engine.py          # 异步压测引擎
│   └── output.py          # 终端打印 + 文件输出
├── config/                # 配置文件
│   ├── config.yaml.example     # 单次压测模板
│   └── scenarios.yaml.example  # 多场景模板
├── prompts/               # 测试 Prompt 集
├── docs/                  # 文档
│   ├── 功能说明.md         # 完整功能说明
│   ├── metrics.md          # 指标详解
│   └── test-scenarios.md   # 测试场景方法论
└── requirements.txt
```

## 文档

- [功能说明](docs/功能说明.md) — 架构、CLI、配置、API 参考
- [指标手册](docs/metrics.md) — 各指标含义、判读标准
- [测试场景设计](docs/test-scenarios.md) — 压测方法论与场景设计
- [开环 vs 闭环压测](docs/open-vs-closed-loop.md) — 两种发压模型的区别、选型与本次更新记录
