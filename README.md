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
