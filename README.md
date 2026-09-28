# llmeter

面向第三方大模型 API 的本地测试与验收平台，支持通过命令行或 Web 界面完成：

- Chat Completions 压测：并发、阶梯输入、多场景和多模型对比
- 一致性基准：官方模型只跑一次，第三方候选模型复用基线做行为比较
- 多模态测试：图片、视频、本地文件和公网 URL 的单次可行性探测
- 路由检测：用响应头、模型标识、tokenizer、ID 前缀、logprobs 和错误阶段做黑盒多路由推断

平台默认监听本机 `127.0.0.1`，密钥不会返回浏览器。

---

## 一、快速开始

### 1. 本地 Web 平台（推荐）

```bash
# 准备 Python 环境
conda env create -f environment.yml

# 启动后端和 Vue3 静态页面
conda run -n llmeter python -m app.main
```

默认访问：

[http://127.0.0.1:8781](http://127.0.0.1:8781)

常用启动参数：

```bash
# 更换端口
conda run -n llmeter python -m app.main --port 9000

# 开发模式（自动重载）
conda run -n llmeter python -m app.main --reload
```

正常使用时只需要启动后端，FastAPI 会直接托管已经构建好的 Vue3 页面。

> 默认端口为 `8781`。如果 Windows 端口被其他程序占用，可以使用 `--port` 更换。

### 2. 前端开发模式

只有修改 Vue3 前端代码时才需要启动 Vite：

```bash
# 终端 1：后端
conda run -n llmeter python -m app.main

# 终端 2：前端热更新
cd app/web
npm run dev
```

开发页面默认是：

[http://127.0.0.1:5273](http://127.0.0.1:5273)

Vite 会把 `/api` 请求代理到后端 `8781`。

### 3. CLI 模式

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化配置文件
cp config/config.yaml.example config/config.yaml
cp config/scenarios.yaml.example config/scenarios.yaml

# 单次压测
python bench.py chat -f config/config.yaml

# 多场景 / 多模型对比压测
python bench.py scenarios -f config/scenarios.yaml
```

---

## 二、平台功能

### 压测

- 单模型单场景
- 多模型 × 多场景
- 输入阶梯场景
- 多并发点矩阵，例如 `1,10,30`
- 流式 / 非流式
- 开环 / 闭环
- 请求数、并发数、warmup、retry、timeout、read timeout、cache
- SSE 实时进度、ETA、TTFT、吞吐、失败数和重试原因

### 一致性基准

- 官方模型建立基线，只运行一次
- 第三方候选模型复用同一基线
- D1 参数语义、D2 tokenizer 指纹、D8 工具/结构输出、D9 协议健壮性
- 网络错误、鉴权、限流、5xx 自动标记为不可判断
- 支持基线/候选中止、原始请求和原始响应查看

### 多模态测试

- 本地图片 / 视频上传
- 公网图片 / 视频 URL
- 图片 `image_url`、视频 `video_url` 单次探测
- 自定义 Prompt、期望关键词、流式、`max_tokens`、温度和 `extra_params`
- 显示原始大小、Base64 大小和预计请求体
- 大 inline 请求发送前预检，防止无意义的超大请求

### 路由检测

- 不需要上游提供渠道 ID
- 记录完整关键响应头、响应 ID 前缀、模型标识、`system_fingerprint`、`prompt_tokens`
- 可选 `logprobs` 指纹
- 记录 TTFE、TTFT、延迟、远端 IP、HTTP 版本和错误阶段
- 默认低消耗：短 JSON Prompt、少量短请求
- 提供连接路由、Tokenizer 指纹、标准确认和长输入/流式复现预设
- 输出证据分组、置信度、证据质量等级和 Markdown 报告

### 资产库

- 连接管理：Base URL、接口路径、环境变量密钥或本机加密密钥
- 模型管理：模型 ID、所属连接、`extra_params`
- 提示词库
- 场景模板
- 从 `prompts/*.txt` 批量创建输入梯度场景

---

## 三、模块边界

| 模块 | 用途 | 不应该用来做什么 |
|---|---|---|
| 压测 | 多并发、性能、吞吐、稳定性 | 不能用来证明模型身份 |
| 一致性基准 | 比较官方模型与候选模型行为 | 不是性能压测 |
| 多模态测试 | 验证单次图片/视频输入是否可行 | 不做并发和批量能力矩阵 |
| 路由检测 | 黑盒推断多个后端/路由指纹 | 不能直接读取上游渠道表 |

路由检测的结论是客户端可观测证据推断，不代表已经读取了上游内部路由配置。

---

## 四、压测指标

每次压测会输出 20+ 个聚合指标：

| 类别 | 指标 |
|---|---|
| 基础 | 总请求数、成功数、失败数、未完成、HTTP 错误、重试数、成功率 |
| 吞吐 | QPS、TPS |
| 延迟 | Avg / P50 / P75 / P90 / P95 / P98 / P99 / P99.9 / Min / Max |
| TTFT | Avg / P50 / P90 / P95 / P99 / P99.9，仅流式可测 |
| Token | 输入、输出、总 token |
| 缓存 | `cached_tokens` 命中 rate 和 token 总数 |
| 错误 | 失败状态码和错误正文 |
| 模式 | 开环 / 闭环、流式 / 非流式、发车 / 排空阶段 |

> 重试后成功的请求会计入成功。追求真实成功率时，建议 `retries=0`。

---

## 五、输出与存储

结果保存在：

```text
bench_results/YYYY-MM-DD/
```

支持：

- 终端 rich 表格
- JSON 原始结果
- Markdown / CSV 对比报告
- Excel 报告

平台本地数据：

```text
app/data/llmeter.db        # SQLite 配置库
app/data/mm_media/         # 多模态媒体资产
```

这些运行时数据不会提交到 Git。

---

## 六、项目结构

```text
llmeter/
├── bench.py                       # CLI 入口
├── src/
│   ├── engine.py                  # 压测引擎
│   └── output.py                  # 终端和文件报告
├── app/
│   ├── main.py                    # FastAPI 入口
│   ├── db.py                      # SQLite schema 和迁移
│   ├── routers/                   # 压测、一致性、多模态、路由检测 API
│   ├── services/
│   │   ├── parity/                # 一致性基准执行与判定
│   │   ├── multimodal/            # 多模态媒体和探测
│   │   └── route_probe/           # 路由指纹检测
│   ├── static/                    # 旧静态页面 / legacy
│   └── web/
│       ├── src/views/             # Vue3 页面
│       ├── src/components/        # Vue3 公共组件
│       └── src/api/               # 前端 API 客户端
├── config/                        # CLI 配置示例
├── prompts/                       # Prompt 和长文本素材
├── docs/                          # 文档
└── bench_results/                 # 压测产物，Git 忽略
```

---

## 七、文档

- [功能说明](docs/功能说明.md) — CLI、配置、接口和架构
- [路由检测功能说明](docs/路由检测功能说明.md) — 黑盒多路由推断、参数、指纹和结果解读
- [指标手册](docs/metrics.md) — 指标定义和判读方法
- [测试场景设计](docs/test-scenarios.md) — 压测场景和测试方法论
- [开环 vs 闭环压测](docs/open-vs-closed-loop.md) — 两种发压模型和选型

---

## 八、已知边界

- 压测结果是否代表真实上游性能，取决于当前代理、网关和渠道配置。
- `1M token` 上下文能力不等于允许 1M 字节请求体；反向代理和网关可能先返回 `413 Payload Too Large`。
- 重试会掩盖首次失败；路由检测和真实性测试建议关闭重试。
- 多模态视频当前只做 `video_url` 可行性探测，供应商文件上传和抽帧适配属于后续能力。
- 路由检测不能证明上游一定有多个渠道，只能根据稳定指纹分裂给出置信度推断。
