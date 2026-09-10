# 更新日志（CHANGELOG）

本文件记录 llmeter 的功能演进与关键修正。最新的在最上面。

> 相关设计文档：[开环 vs 闭环压测](open-vs-closed-loop.md)

---

## 2026-09-09 — Excel 报表瘦身与结论准确性修正

### 修正：移除冗余的「仪表盘」封面

- **[`src/output.py`](../src/output.py) `save_excel`**：删除「仪表盘」sheet（与数据表内容重复的卡片式汇总）及其全部样式代码。打开 Excel 直接看到的就是「总览」数据表，各维度分组表、指标说明随后，sheet 顺序更精简。

### 修正：成功率列的阈值着色被覆盖

- 成功率列原本用静态红/黄/绿表达健康度（<99% 红、<100% 黄、=100% 绿），但随后又被「通用三色渐变条件格式」整列覆盖，实际显示成了渐变而不是阈值色，静态着色形同虚设。
  - 把「成功率」列从渐变条件格式与「最优值高亮」中排除：渐变只作用于 QPS/TPS/延迟/TTFT/TPOT 等可比数值列，成功率恢复为语义明确的阈值着色。

### 修正：Excel 默认不再生成柱状图

- 柱状图与数据表信息重复（表格自带色阶、最优值高亮，直接读表即可判断性能），多模型 × 多场景时 x 轴还会拥挤。因此 **[`src/output.py`](../src/output.py) `save_excel`** 新增 `charts=False`，**默认不再生成任何柱状图**；**[`bench.py`](../bench.py) `scenarios`** 新增 `--excel-charts` 开关，确需图表时显式开启（与 `--excel` 同用）。

### 修正：结论摘要升级为「指标快评」

- 原 `_add_summary` 按场景名字母排序取「首行 vs 尾行」做对比；在多模型 × 多场景混排的总览表里，首尾往往分属不同模型与不同场景，会产出「A 模型长输入 vs B 模型短输入」这类不可比的结论。
  - 改为「指标快评 + 结论」两层：先给规模/模式，再对**口径明确、不随模型规模漂移**的指标做直观评级——整体成功率（🟢 健康 / 🟡 偶发失败 / 🟠 失败偏高 / 🔴 异常）、长尾比 P95/P50（均匀 → 严重）、流式 TPOT（剔 prefill，优秀 ~ 很慢，标注「量级参考」）；延迟/吞吐不做绝对评级，给「最低 ~ 最高（平均）」量级。
  - 随后列出**延迟最低 / 延迟最高 / 吞吐最高**的具体行（多模型标注「模型 | 场景」）与**成功率未达 100%** 的行清单（含重试/错误提示），摘要标题带分组名（如「结论摘要（输入）」）。

### 新增：Prompt 缓存命中率指标（开关可控）

- **[`src/engine.py`](../src/engine.py)**：从 `usage.prompt_tokens_details.cached_tokens` 采集命中 token 数（仅该字段存在时上报，避免与「命中 0 个」混淆）。
  - 聚合区分两种口径并分开输出：`cache_hit_rate` **整体命中率（token 加权）** = 命中 token 总和 ÷ 上报请求的 prompt 总和（封顶 100%）；`cache_hit_rate_per_req` **平均每请求命中率** = 各请求自身命中率的算术平均。另输出 `cached_tokens_total`（命中总 token）、`cache_prompt_total`（加权分母）、`cache_reported_requests`；API 不返回该字段时不输出这些指标，并在控制台提示。
  - 引擎参数 `track_cache`（默认 **True**），流式与非流式均生效。
- **[`bench.py`](../bench.py)**：`chat` 新增 `--track-cache / --no-track-cache` 开关（默认开）；`scenarios` 通过配置文件（顶层 / `defaults` / 场景）的 `track_cache` 字段控制。
- **[`src/output.py`](../src/output.py)**：面向阅读者的展示（单次报告、对比表「缓存命中」列、Excel、结论摘要）**只给一个「缓存命中率」**，不暴露 token 加权/每请求等后台口径；`cache_hit_rate_per_req`、`cache_prompt_total` 等细分指标保留在 JSON `summary`（后台数据），单次请求命中的 token 数在 JSON `details` 的 `cached_tokens` 字段。
  - 命中率只展示数值、不做跨场景最优/色阶标注：前缀缓存命中与输入长度、是否启用缓存强相关，跨场景标注最优会误导。
- 口径说明：默认按 `cached ⊆ prompt`（OpenAI 口径）；个别厂商把 cached 单独计数不计入 prompt 时命中率会失真（已封顶 100%），见 [metrics.md](metrics.md)。

### 修正：结论摘要移到数据表顶部（解决“看不到摘要”）

- 摘要此前写在数据表下方：表短时紧贴表底，表长或开启柱状图时会被推到几十行之后，几乎看不到。
  - 现在每个数据 sheet 的**第一行就是「📝 结论摘要」**（标题带浅黄底色），随后是分组表头、列名与数据；
    冻结窗格设在数据首行，滚动长表时摘要与表头始终钉在顶部。`_write_data_sheet` 增加 `start_row` 参数以支持表头整体下移。

### 修正：结论摘要改版为面向阅读者的「要点式」

- 首行说明「在对比什么」（如 `对比：场景 短输入、中输入、长输入（流式）`，多模型为 `对比：模型 A、B ｜ 场景 …`），不再写「N 行对比」这类内部行话。
- 每个指标一行要点、口径直白：成功率（全部成功 / N 次未成功）、响应速度（最快/最慢场景与差距倍数）、**首字时间 TTFT（流式，此前缺失）**、吞吐最高行、延迟稳定性（P95/P50 倍数 + 稳定/波动评价）、缓存命中率（单值）。需关注的未达标场景单独一行告警。
- 详细口径（token 加权、平均每请求命中率、分母、命中 token 数）从展示中去掉，仅保留在 JSON 后台字段。

### 验证

- 多模型（2 模型 × N 场景）与单模型多分组（输入梯度 + 业务场景）均用 mock 数据生成 xlsx：sheet 顺序 = `总览 → 各维度分组 → 指标说明`，不再有仪表盘。
- 默认生成的 xlsx 无任何柱状图（drawings=0）；开启 `charts=True` 后恢复 4 张图且锚点间距正常。
- 成功率列仅剩静态阈值色（无 colorScale 规则）；摘要正确给出整体成功率评级、长尾/TPOT 评级与最快/最高吞吐行、失败行。
- mock usage（含 `prompt_tokens_details.cached_tokens`）验证：命中率聚合、无字段时自动降级、CLI 开关与配置覆盖均通过；Excel 缓存列与摘要行正常。
- 生成文件经真实电子表格引擎（artifact-tool render）无错误渲染。

---

## 2026-08-17 — Excel 报表排版优化：指标带单位、柱状图去拥挤

针对 `scenarios --excel`（尤其是 `models-comparison.yaml` 多模型 × 多输入场景）生成的 Excel 报表做排版优化，提升可读性。

### 修正：表头单位完整显示

- **[`src/output.py`](../src/output.py) `_write_data_sheet`**：指标表头本就在第二行带单位（如 `延迟P50\n(s)`、`TPOT Avg\n(ms)`），但第二行行高只有 20，两行文字被截断，单位实际上看不全。
  - 列名表头行高 20 → **32**（块分组表头 24 → 26），秒 / 毫秒 / req/s 等单位现在完整可见。

### 优化：柱状图排版

- **[`src/output.py`](../src/output.py) `_add_charts`**
  - **不再重叠**：图表高度 13cm → 9.5cm，纵向间距按高度换算（每图间隔 23 行），此前每 16 行放一张 13cm 高的图，图与图互相压叠是「排版拥挤」的主因。
  - **柱更粗**：`gapWidth` 默认 150 → 80、`overlap` -10，簇状柱形不再细成一条线。
  - **文字不再挤**：轴字号缩小（850/900），分类超过 6 个时 x 轴标签自动旋转 -45°；图例移到底部。
  - **多模型分类唯一**：跨模型对比表里同名场景会重复出现，图表分类改为「模型 | 场景」组合（表格右侧新增样式化的「图例: 模型 | 场景」辅助列），避免多个模型同名柱挤在一起分不清。
  - **QPS 与 TPS 拆成两张图**：两者量纲差异大（QPS 个位数、TPS 成百上千），共用一个 y 轴时 QPS 柱会被压成轴底的一个小点。现拆为「吞吐对比 (QPS)」「吞吐对比 (TPS)」两张独立图表，各自使用自己的坐标轴与单位（req/s、tok/s）。
  - 数据标签仅在分类 ≤ 6 时显示并置于柱顶（`outEnd`），分类多时省略以免糊成一团。

### 验证

- 用 mock 多模型数据（2~3 模型 × N 场景）生成 xlsx：表头行高/单位、辅助列样式、4 张图表锚点间距（10/33/56/79 行）、QPS/TPS 各自独立轴与全量数据点、柱参数（gapWidth/overlap）、轴字号与旋转均已在生成文件内核对。
- 单模型（per-model sheet）回归：不加辅助列、分类仍为「场景」列，行为不变。
- 生成文件经真实电子表格引擎（artifact-tool render）无错误渲染。

---

## 2026-08-13 — 压测准确性修复：失败三分类 / 默认流式 / 重试透明化 / 多分位 / 开环两阶段

基于与 **EvalScope perf、GuideLLM** 的对比分析（见 [业界压测工具对比分析](业界压测工具对比分析.md)），对影响测试结果**准确性**与**可读性**的 P0/P1 项逐条修复。核心目标：让默认配置开箱即测 TTFT、让失败原因可区分、让重试不再偷偷抬高成功率、让开环 QPS 口径可解释。

### 背景

对比分析发现 llmeter 的测量方法学是站得住的（计时起点、开环防 coordinated omission、token 计数优先级均与业界一致），但有 4 个系统性偏差风险：

1. 默认 `stream=False` → TTFT / ITL 开箱不可测（EvalScope 默认 `stream=True`）；
2. 重试成功混入 `success` → 限流被掩盖，成功率 / QPS 虚高；
3. 失败只有 success/fail 两类 → 超时与 4xx/5xx 混在一起，SLA 解读失真；
4. 开环 wall 含收尾排空 → 实际 QPS 系统性略低，且无法区分「发车阶段」与「排空阶段」。

### 修正：默认配置（流式 / 预热）

- **[`bench.py`](../bench.py) / [`src/engine.py`](../src/engine.py)**
  - `stream` 默认改为 **True**（与 EvalScope 一致），TTFT / ITL 开箱即测；新增 `--no-stream` 显式关闭（argparse `BooleanOptionalAction`），配置文件 `stream: false` 仍可覆盖默认值。
  - `warmup` 默认改为 **2**（此前 README/文档声称"内置 2 次预热"，实际默认 0，名不符实）；预热请求不计入结果，剔除冷连接握手对首批 TTFT 的污染。

### 新增：失败三分类与重试透明化

- **[`src/engine.py`](../src/engine.py) `aggregate()`**（对齐 GuideLLM 的 success / incomplete / error）
  - `incomplete`：请求未到达服务端或未拿到完整响应（超时 / 连接中断，`status_code=0`）；
  - `error_count`：服务端明确返回 HTTP 4xx/5xx；
  - `retried`：至少重试过一次的请求数（含最终成功/失败）。`_request()` 在每个返回结果上打 `retried` 标记，JSON `details` 里每条请求都可见；
  - 兼容旧字段：`fail` 保留 = `incomplete + error_count`，`success_rate` 口径不变。

### 新增：多分位延迟指标

- **[`src/engine.py`](../src/engine.py) `aggregate()`**：延迟新增 `latency_p75 / p90 / p98 / p999`，TTFT 新增 `ttft_p75 / p90 / p98 / p99 / p999`（对标 EvalScope 默认 10/25/50/75/90/95/98/99% 多分位输出），单次报告与 JSON 同步展示。

### 修正：开环 wall 口径 —— 两阶段拆分

- **[`src/engine.py`](../src/engine.py) `_dispatch_open()`**：返回 `(发车耗时, 排空耗时, 总耗时)` 三元组。
  - 发车耗时：第一个请求 → 最后一个请求发出；
  - 排空耗时：发完车后等待所有在途请求返回；
  - `qps`（= success / 总耗时）语义不变，另新增 `dispatch_phase` / `drain_phase` / `qps_dispatch`（实际发车速率）指标，报告打印「发车 Xs + 排空 Ys」，便于判断 QPS 缺口是「服务端跟不上」还是「收尾排空固有开销」。

### 新增：TPOT 口径标注

- metrics 增加 `tpot_mode: "stream" / "nonstream"`。对比表新增「模式」列，单次报告 TPOT 小节标注「流式口径（剔除 prefill）/ 非流式口径（含 prefill）」，避免跨模式误比 TPOT / TTFT。

### 同步：报告与导出

- **[`src/output.py`](../src/output.py)**
  - 单次报告（rich / plain）：展示 `未完成(超时/中断)`、`HTTP错误(4xx/5xx)`、`重试` 计数；延迟与 TTFT 多分位；TPOT 口径；开环两阶段耗时与实际发车速率。
  - 对比表新增「模式」「重试」「未完成」「HTTP错误」四列；Excel 报表与「指标说明」sheet 同步（含成功率/TPOT 的判读注意）。
  - JSON `meta` 补充 `stream` / `warmup` / `retries`，结果文件自描述。

### 验证

- `aggregate()` 单元断言：三分类、重试计数、多分位、tpot_mode 全部通过。
- rich / plain 报告、对比表、Excel 导出、JSON 落盘均在 mock 数据下端到端渲染通过（开环与闭环两种模式）。
- `bench.py chat --help` 确认 `--stream | --no-stream` 双开关；`merge_config` 优先级验证：配置文件 `stream:false` 可覆盖默认值、`--no-stream` 可压过配置文件。

> **建议的真实成功率跑法**：追求无重试的真实成功率时，压测命令加 `--no-stream` 之外的 `retries: 0`（配置文件或 `--retries 0`），或读取报告中「重试」计数来解读成功率。

---

## 2026-07-30 — 开环恒定 QPS 压测能力

本次为 llmeter 新增**开环恒定 QPS（open-loop）压测模型**，并围绕它修正报告展示、输出文件命名与报告去重。

### 背景

此前 llmeter 只有**闭环并发（closed-loop）**：`Semaphore` 限制在途 N 个，一个返回才补下一个。它测的是「N 路并发下的饱和吞吐/延迟」，但**无法复现「稳定 X QPS 流量」的压力**，且受 coordinated omission 影响会**低估尾延迟**（服务器一变慢，客户端自动少发请求）。两种模型测的东西不同、互不能替代，详见 [开环 vs 闭环压测](open-vs-closed-loop.md)。

### 新增：开环恒定 QPS 模式

- **[`src/engine.py`](../src/engine.py)**
  - 新增引擎参数 `qps` / `duration`；设了 `qps` 即切开环。
  - `run()` 按模式分流到 `_dispatch_open`（按固定到达率定速发车、不等前一个返回）与 `_dispatch_closed`（`Semaphore` 限流，原有行为）。
  - 开环发车用**目标时间戳对齐**（`t0 + (i+1)/qps`）避免累积漂移；服务器落后时立即补发，积压体现在 `_active` / 在途峰值水位。
  - 开环连接池 `max_connections=None`（不设上限），避免超额请求在连接池排队、把开环悄悄退化成闭环而失真。
  - `qps + duration` 自动换算总请求数 = `qps × duration`（对齐传统 loadtest 语义）。
  - 结果 metrics 携带 `mode` / `qps_target` / `concurrency_peak`；收尾打印区分「目标 QPS → 实际完成 QPS + 在途峰值(积压)」。
- **[`bench.py`](../bench.py)**
  - `chat` 子命令新增 `--qps` / `--duration`；参数合并链与 `build_engine_config` 同步透传（scenarios 场景配置亦可用）。
  - 启动横幅按模式显示「开环 QPS=.. 时长=..s→..请求」或「并发=.. 请求=..」。

### 修正：报告不再展示无意义的并发值

开环下 `concurrency`（默认 10）此前被无条件写进对比表「并发」列，误导为「限了 10 并发」。

- **[`src/output.py`](../src/output.py)**
  - 「并发」列改为 mode-aware：闭环显示设定上限；开环显示 `~N` 实测在途峰值（`~` 标明是积压水位、非设定值）。
  - 新增「目标QPS」列：开环显示设定到达率，闭环为 `—`，使开环结果自解释「目标 vs 实际」。
  - 加固 Excel 列块映射：由**位置下标**改为**按列头名选列**，此后 `_COMPARE_COLS` 增删列不再错位。
  - `print_report` 的单场景报告同样区分开环/闭环展示。
  - 「指标说明」sheet 补充「目标QPS」「并发」两列的解释。

### 优化：输出文件名反映发压模式

- **[`src/output.py`](../src/output.py) `save_results`**：JSON 结果文件名按模式命名，避免开环时出现误导性的 `c10` 默认并发标签。
  - 闭环：`{model}_c{并发}_n{请求数}_{时间}.json`
  - 开环：`{model}_qps{QPS}_d{时长}_n{请求数}_{时间}.json`
  - 同时在文件 `meta` 中写入 `mode` / `qps` / `duration`，结果自描述。

### 优化：单维度报告去重

- **[`bench.py`](../bench.py) `report_scenarios`**：只有一个维度分组时，「分组表」与「总表」内容完全相同（仅标题差 ` — 维度`），此前会生成两份重复文件（如 `comparison_模型.md` 与 `comparison_模型 — QPS.md`）。
  - 现在当分组数为 1 时**跳过分组表，只保留总表**。
  - 因此只跑单模型、单维度（如整组 `QPS-*`）时，只会得到**一份**对比文件，看它即可。

### 新增示例配置

- **[`config/qps-openloop.yaml.example`](../config/qps-openloop.yaml.example)**：开环 QPS 阶梯示例（已脱敏，复制为 `config/qps-openloop.yaml` 后使用），`retries: 0`、`warmup: 2`、`stream: true`，注释说明如何按「实际 QPS vs 目标 + 积压是否发散」定位可持续吞吐拐点。

### 验证

- 本地 mock SSE 服务端端到端验证：闭环在途峰值被压在 `concurrency`；开环 `qps×duration` 换算正确、在途峰值突破闭环并发上限（证明确实不受限、能暴露积压）。
- 对比表（rich + 纯文本）与 Excel 导出在开环/闭环混合行下均正常渲染，列序与新列内容正确。
