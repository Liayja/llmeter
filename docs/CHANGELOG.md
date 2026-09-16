# 更新日志（CHANGELOG）

本文件记录 llmeter 的功能演进与关键修正。最新的在最上面。

> 相关设计文档：[开环 vs 闭环压测](open-vs-closed-loop.md)

---

## 2026-09-15 — 基准一致性测试（初版，独立于压测）

新增**独立模块**：用官方模型的**基线快照**验证第三方模型资源的行为等价性。与压测完全分开——压测是多并发大量请求测性能指标，本模块是少量用例逐条比对行为。

### 新增：基线与候选执行

- **[`app/services/parity/`](../app/services/parity/)**（与压测的 job_runner 解耦）：
  - `cases.py`：内置用例集 `parity-v1`，初版覆盖 4 个维度共 17 条用例（D1 参数语义 3 条、D2 指纹 6 条、D8 结构/工具 2 条、D9 协议健壮性 6 条）；
  - `executor.py`：单用例执行（完整控制 `messages` / `tools` / `tool_choice`），采集输出文本、usage、tool_calls、耗时、**行为类别**（REJECT / ACCEPT_AS_IS / ERROR_OTHER / UNKNOWN）；
  - `judge.py`：闸门（D0 可比性）→ 逐用例判定 → 维度聚合 → 红旗 → 结论（等价/可疑/不等价/不可比），含可配置权重、阈值与开关；
  - `runner.py` / `store.py`：基线建立、候选运行与数据落库（`parity_baselines` / `parity_baseline_items` / `parity_runs` / `parity_run_items`）。
- **官方只跑一次**：建立基线后，多个候选共用同一份快照，候选运行只跑自己，再按 `case_id` 与基线对齐比较。

### 新增：API 与界面

- `GET /api/parity/dimensions`（维度目录，含用例数与权重）、`POST/GET /api/parity/baselines`、`POST/GET /api/parity/runs`、`GET /api/parity/runs/{id}/markdown`；
- 前端新增**独立页签「一致性基准」**（与「压测」分开）：① 建立基线（勾选维度）② 候选测试 ③ 对比报告（结论 + 红旗 + 维度表 + 逐条用例 + 配置快照 + 导出 Markdown）。

### 判定要点

- **闸门**：用例集/覆盖率/基线时效/候选连通性不通过 → 判「不可比」，报告给出**具体原因与建议**，不显示等价度分数；
- **一票否决**：tokenizer 指纹偏差 >2%、`max_tokens` 系统性不遵守（>20%）、孤儿/悬空 tool 序列行为与官方不同、工具调用完全不可用；
- 权重默认合计 100%（D2 20%、D8 15%、D1 12%、D9 10%），加权总分**仅用于排序**，可在配置中关闭。

### 验证

- 两个 mock 服务（"官方"严格校验 tool 序列 + 遵守 max_tokens；"候选"全放行 + 忽略 max_tokens + tokenizer 偏移 5%）：
  基线 17 条 → 候选运行 → 报告结论 **不等价**（等价度 26.3），红旗正确列出 tokenizer 偏差 4.99%、max_tokens 违规率 33.3%、
  3 条孤儿 tool 行为类别不一致；维度表 D1/D2/D9 红、D8 绿；
- 界面实测：页签渲染 4 个维度卡片（名称 / 用例数 / 权重），基线建立按钮与模型下拉正常。

### 优化：一致性的过程反馈与基线明细查看

- 反馈问题：点击「建立基线」后没有任何反馈，只在跑完才看到结果；基线结果也无法查看具体请求情况。
  - **过程反馈**：后端进度记录补充 `model` / `done` / `total` / `current`（当前用例）/ `last_status`（上一条 HTTP）/ `last_error` / `started_at`；
    前端在建立基线与候选测试时显示进度条与文字（状态、x/y、百分比、当前用例、上一条返回、失败原因与建议），每 1.5 秒刷新一次；
  - **基线明细查看**：基线列表新增「查看」，展示每条用例的**维度 / 用例名 / HTTP 状态 / 行为类别（REJECT、ACCEPT…）/ 入出 token / 耗时 / 输出预览**；
    `GET /api/parity/baselines/{id}` 返回完整明细（含 usage 与错误信息）；
  - 前端静态资源加版本号（`?v=20260915d`）避免浏览器缓存旧脚本；模型下拉旁新增「刷新」按钮，并给出模型数量的提示文案。
- 验证：API 轮询实测 `done` 3/9 → 7/9 → 9/9 且带当前用例与 HTTP 状态；界面「查看」正确渲染 9 条明细（首行：D1 | max_tokens 是否被遵守（256）| 200 | ACCEPT | 24 / 20 token | 0.27s | ok）。

### 新增：资产库「连接 / 模型」支持编辑

- 反馈问题：连接与模型配置好后无法修改，改一个小地方（如 Base URL 写错）只能删除重建。
  - 连接列表与模型列表新增「编辑」按钮：点击后把该条数据**回填到上方表单**，按钮变为「保存修改」，并出现「取消编辑」；
  - 保存时走已有的 `PUT /api/connections/{id}` / `PUT /api/models/{id}`，不再新建记录；
  - **密钥处理**：环境变量模式回显变量名；本机加密模式密钥框留空即**保持原密钥不变**（输入框有对应提示），需要更换时再填入新密钥；
  - 模型编辑支持修改显示名、模型 ID、所属连接、`extra_params`（JSON 会自动格式化回填）。
- 验证：API 层——连接改名/改 URL/改接口路径后密钥保持不变；模型改显示名与 `extra_params` 生效。
  界面层——点击「编辑」后表单正确回填（名称、URL、接口路径、环境变量名、模型名、extra_params），按钮切换为「保存修改」且「取消编辑」可见。

### 修复：Base URL / 接口路径填反导致 UnsupportedProtocol

- 现象：跑基准测试（或压测）时报 `UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol`。
- 原因：连接里 **Base URL 为空、而把完整地址填进了「接口路径」**（实测连接 #5：`base_url=''`、`endpoint='https://api.deepseek.com'`），
  拼接结果是 `/https://api.deepseek.com` 这种相对路径，httpx 无法识别。
  - `src/engine.py build_chat_url()` 增加容错：接口路径是完整地址时直接当完整 URL 使用；Base URL 缺协议前缀自动补 `https://`；
    Base URL 为空时抛出**可执行的明确错误**（提示填哪里），不再让 httpx 抛晦涩异常；
  - `app/routers/connections.py` 新增 `_normalize_urls()`：保存/编辑连接时自动纠正（完整地址填在接口路径 → 搬到 Base URL；缺协议 → 补 https://；接口路径统一加 `/` 前缀），
    并在 Base URL 仍为空时返回 400 明确提示；
  - 前端：Base URL 标注为必填并在保存前校验，接口路径的占位符写明"只填路径"。
- 验证：`build_chat_url("", "https://api.deepseek.com")` → 返回 `https://api.deepseek.com`；
  `build_chat_url("open.bigmodel.cn/api/paas", "/v4/chat/completions")` → `https://open.bigmodel.cn/api/paas/v4/chat/completions`；
  `build_chat_url("", "")` → 明确的 ValueError；保存时三种常见错填均被自动纠正。
- 处置建议：把该连接的 Base URL 改为 `https://api.deepseek.com`、接口路径改为 `/chat/completions`（或直接点一次「编辑 → 保存修改」，会自动纠正）。

### 优化：一致性结果查看的交互（可折叠 / 互斥显示 / 用例下钻）

- 反馈三个问题：① 基线明细点开后无法关闭，一直占着页面；② 基线明细与候选报告同时展开，页面被拉得很长；③ 无法查看单条用例的实际请求与响应。
  - **可折叠**：再次点击同一条基线的「查看」即折叠关闭；明细与报告标题栏都新增「关闭」按钮；
  - **互斥显示**：打开基线明细时自动收起候选报告，打开候选报告时自动收起基线明细，并用 `scrollIntoView` 定位到当前查看区域，不再两块叠加；
  - **用例下钻**：每个用例改为可展开区块（`<details>`）——基线明细里展开可见**实际请求体（JSON）+ 响应（HTTP / 行为类别 / 入出 token / 耗时 / 输出内容 / usage）**；
    对比报告里展开可见**实际请求体 + 官方与候选的响应并排对比**（HTTP、行为类别、token、错误、双方输出内容、usage）；
  - 后端：基线明细接口补充 `request`（实际请求体），报告 `cases` 中补充 `request` 字段。
- 验证：浏览器实测——点「查看」展开 9 条用例且首条请求体可读；再次点击同一「查看」→ 收起；打开候选报告后基线明细自动隐藏；报告内 9 条用例均可展开查看请求与双方响应。

### 修复：老库缺列导致「table parity_baseline_items has no column named raw_response」

- 现象：升级后重跑基准测试失败，报 `table parity_baseline_items has no column named raw_response`。
- 原因：新增的 `raw_response`（原始响应体）只写在建表语句里，而 SQLite 的 `CREATE TABLE IF NOT EXISTS` **不会给已存在的表补列**，所以老库缺这一列。
  - `app/db.py` 新增**统一的轻量迁移机制** `_COLUMN_MIGRATIONS`：启动时逐表检查 `PRAGMA table_info`，缺列就 `ALTER TABLE ADD COLUMN` 并打印迁移日志；
    覆盖 `connections.endpoint`、`parity_baselines.status/error`、`parity_baseline_items.tool_calls_json/raw_response`、`parity_run_items.tool_calls_json/raw_response`。
- 验证：构造"旧表结构"的库 → 运行 `init_db()` → 7 个缺失列被自动补齐（含 `raw_response`），随后基线写入与读取正常（`raw_response={"error":{"message":"orphan tool"}}`）。
- 说明：重启平台即自动迁移，**无需删除数据库**；迁移只做加列，不动既有数据。

> 设计依据见本地文档 `docs/基准一致性测试设计.md`（按约定不入库，随功能同步维护）。

---

## 2026-09-10 — 本地 Web 压测平台 M1（跑通最小闭环）

在 CLI 之外新增本地 Web 界面，目标是把「配置 → 压测 → 看结果」从命令行搬到浏览器，引擎与指标口径保持不变。

### 新增：平台骨架（`app/`）

- **FastAPI + uvicorn** 本地服务（仅监听 `127.0.0.1`，默认端口 8765），`python -m app.main` 启动；
- **静态单页前端**（`app/static/`，原生 JS + SSE，无 Node 构建）：压测 / 资产库 / 结果三个页签；
- **SQLite 资产库**（`app/data/llmeter.db`，已 git 忽略）：connections / models / tasks。

### 新增：密钥两种存法

- `env`：库里只存环境变量名，运行时用 `${VAR}` 展开；
- `local`：Windows DPAPI 加密后落库（非 Windows 降级 base64 并提示）；接口一律返回脱敏值（如 `sk-****1234`）。

### 引擎适配（口径不变）

- `LLMBench` 新增可选 `on_progress` 回调与 `request_cancel()`：进度每秒/每请求上报，取消后不再发起新请求；
- CLI 不传回调时输出行为与之前完全一致；
- 结果落盘仍复用 `output.save_results` / `save_excel`，产物结构不变。

### 验证

- mock SSE 服务端到端：建连接（DPAPI 加密）→ 建模型 → 跑 6 请求 → SSE 进度 → 成功率 100%、缓存命中 50% → JSON/Excel 下载；
- 取消验证：10 请求任务在 1.2s 中止，实际只发出 6 个请求，状态 `canceled` 且已跑结果正常保存；
- 真实 HTTP 服务验证：`/api/health` 200、首页与 JS/CSS 正常返回。

### 修复：接口路径可配置（智谱等非 /v1 路径）

- 此前引擎把地址硬编码为 `{base_url}/v1/chat/completions`，智谱官方（`https://open.bigmodel.cn/api/paas/v4/chat/completions`）会 404。
  - `src/engine.py` 新增 `build_chat_url()` 与 `endpoint` 参数：默认仍是 `/v1/chat/completions`，可按厂商覆盖（智谱填 `/chat/completions`）；
  - 兼容 `base_url` 已带 `/v1`、或用户直接粘贴完整 `.../chat/completions` 地址的写法，避免拼出重复路径；
  - `bench.py` 新增 `--endpoint`（配置文件同名字段）；平台「连接」新增「接口路径」输入框，连通性测试与压测共用同一拼接规则。
- 验证：mock 服务只接受 `/api/paas/v4/chat/completions`，连接测试与 4 个压测请求全部命中该路径。

### 修复：scenarios 模式漏传接口路径（智谱仍打到 /v1）

- 实测发现「多模型 × 多场景」任务仍会打到 `/v1/chat/completions`：`run_scenarios` 组装引擎参数时**漏传了 `endpoint`**，于是回落到默认路径（单模型 chat 模式正常）。
  - `app/services/runner_bridge.py`：scenarios 模式补齐 `endpoint` 透传；
  - 保存的 JSON `meta` 新增 `endpoint` 与 `chat_url` 字段，后续排查可直接看到实际请求地址；
  - `build_chat_url` 的重复段判断从「仅 /v1」推广到任意首段，兼容 `base_url=.../v4` + `endpoint=/v4/chat/completions` 这类写法。
- 验证：`base_url=.../api/paas` + `endpoint=/v4/chat/completions` 下，scenarios 与 chat 两种模式的所有请求均命中 `/api/paas/v4/chat/completions`。

## 2026-09-10 — 本地 Web 压测平台 M2（多模型 × 多场景）

### 新增：多模型 × 多场景对比

- **[`app/services/runner_bridge.py`](../app/services/runner_bridge.py) `run_scenarios`**：遍历「模型 × 场景」笛卡尔积逐个执行，复用 `LLMBench` 与 `output` 的对比报告函数，指标口径与 CLI 完全一致；
  - 每个场景单独落 JSON（文件名追加场景名，避免同秒覆盖），最后生成 Markdown / CSV / Excel 对比报告与 comparison JSON；
  - 支持中途取消：当前场景停下、已完成结果照常出报告。
- **[`app/routers/tasks.py`](../app/routers/tasks.py)**：任务支持 `mode=scenarios`（`model_ids[]` + `scenario_ids[]`），快照中只保存连接 ID，明文密钥在启动时才解析。

### 新增：提示词库与场景模板

- 数据表 `prompts` / `scenarios`，对应 API `/api/prompts`、`/api/scenarios`；
- 场景模板支持覆盖参数（`max_tokens`、`timeout`、`qps`、`duration` 等 JSON），界面可从提示词库选择提示词；
- 前端「压测」页新增模式切换（单模型 / 多模型 × 多场景），「资产库」页新增提示词库与场景模板两个面板；结果页对 scenarios 任务展示对比表。

### 验证

- mock 服务下 2 模型 × 3 场景 = 6 组全部成功，对比行 6 条、组合无重复，comparison JSON / Excel 下载正常；
- 静态页与新增接口（`/api/prompts`、`/api/scenarios`）在真实 HTTP 服务下均返回 200。

### 优化：单模型 × 多场景作为主用法

- 实测最常用的压测形态是「**单模型跑多场景**」（如输入长度梯度找拐点），而不是多模型横向对比。
  - 界面默认模式改为「场景对比（1~N 模型 × 多场景）」：选 1 个模型即单模型多场景，选多个模型即横向对比，不再需要区分两种模式；
  - 「单次压测（1 模型 · 1 提示词）」保留为次选模式。
- 新增「一键批量建场景」：`GET /api/prompts/files` 列出项目 `prompts/` 目录并按 token 数排序，`POST /api/scenarios/bulk` 批量生成场景；
  命名会去掉冗余前缀（`prompts/input-3k.txt` + 前缀「输入」→ `输入-3k`）。
- 验证：从 prompts 目录批量建 3 个场景 → 单模型 × 3 场景跑通，对比行 3 条、缓存列有值。

### 修复：缓存命中列的"空值"观感

- API 未返回 `prompt_tokens_details.cached_tokens` 时，Excel 缓存列原本是**空白单元格**，看起来像漏了指标；
  现改为显示 `—`（明确表示未上报），有数据时仍写数值（如 40 表示 40%）。

### 优化：运行中进度改为按模式展示有效指标

- 反馈「并发 5 时活跃/峰值一直显示 5，看不出区别」：闭环并发下这两个数稳态就等于并发上限，确实没有信息量。
  - 进度事件新增 `mode` / `rate`（实时完成速率）/ `elapsed` / `eta` / `avg_latency` / `concurrency_target` / `qps_target`；
  - **闭环**：显示「并发 x/目标（已打满 / 峰值未打满）」+ 实时速率 + 平均延迟 + TTFT + ETA + 失败数；只有**没打满**时才提示峰值，避免刷屏；
  - **开环**：保留「在途/峰值积压」——这正是判断服务器扛不扛得住的关键信号；
  - CLI 的每秒进度行同步改版（与平台共用同一 `_emit_progress`）。
- 示例（闭环并发 3）：`⏳ 6/12 | 并发已满 3 | 5.50 req/s | TTFT 0.34s | ETA 1s | 失败 0`

### 新增：压测前连通性测试与请求/响应结构回显

- 需求：正式压测前先打一发，看清**请求体与响应体结构**（模型名、extra_params、usage 字段是否齐全），避免直接压测才发现配置或响应格式不对。
- **[`src/engine.py`](../src/engine.py)**：抽出 `build_chat_payload()`，压测与调试共用同一个请求体构造函数，保证"调试看到的 = 压测发出的"。
- **[`app/services/probe.py`](../app/services/probe.py)**：单次探测服务，返回
  - 请求：URL、脱敏请求头、完整 payload、prompt token 估算；
  - 响应：状态码、耗时、TTFT（流式）、关键响应头（含 `x-ratelimit-*` / `retry-after`）、usage、正文或 SSE 分片；
  - 提示：`✅ HTTP 200`、usage 是否齐全、是否支持 `cached_tokens` / `reasoning_tokens`、流式解析方式（`choices[0].delta.content`）、非标准结构告警。
- **API**：`POST /api/models/{id}/probe`（可覆盖 prompt / max_tokens / stream / extra_params）；连接级 `POST /api/connections/{id}/test` 改为复用同一探测服务并返回提示。
- **界面**：压测页新增「🔍 先试跑一次（连通性 + 请求/响应结构）」（带当前表单参数），资产库模型卡片新增「调试」入口；弹窗内并排显示请求体与响应体，附结构提示。
- 验证：mock 服务下非流式与流式各验证一次——请求体正确带回 `extra_params`、响应 usage/cached_tokens/reasoning_tokens 正常解析、TTFT 与分片统计正确、密钥在回显中脱敏。

### 修复：调试弹窗关不掉（CSS 优先级）

- 现象：连通性测试弹窗点右上角「关闭」没反应。
- 原因：`.modal { display: flex }` 属于作者样式，会覆盖浏览器默认的 `[hidden] { display: none }`，
  于是 `element.hidden = true` 设了属性但元素照样显示。同一个坑还影响「密钥方式」切换时环境变量/密钥输入框的显隐。
  - 在 `app/static/css/app.css` 增加 `[hidden] { display: none !important; }` 兜底，一次性修好所有 `hidden` 显隐；
  - 弹窗补充三种关闭方式：右上角按钮、底部「关闭」按钮、点击遮罩空白处、按 Esc。
- 说明：调试是**完全独立**的一次请求（`POST /api/models/{id}/probe`），既不创建也不启动压测任务，看完随时关闭即可，不存在"必须先压测"的流程。
- 验证：真实浏览器（in-app browser）实测——打开弹窗后，点关闭按钮 / 按 Esc / 点遮罩空白处均能关闭；密钥方式切换后两个输入框显隐正确；探测接口调用后任务列表为空。

### 优化：调试弹窗区分「接口原生」与「客户端计算」

- 反馈「响应体是有封装还是原生返回？为什么里面还有 TTFT」——TTFT 不是接口字段，是客户端测量值，混在一起容易误读。
  - 响应区拆成两块：**① HTTP 原文（接口原样返回，未加工）**：`status_code` / 关键响应头 / `raw`（非流式为响应正文原文，流式为原始 SSE 行，含 `[DONE]`）；
    **② 客户端解析 / 计算（非接口字段）**：`usage`（来自接口）、`elapsed`、`ttft`、`chunk_count`、`text`、`reasoning_text`；
  - `probe` 服务新增 `raw` / `raw_truncated` 字段；提示文案注明「TTFT 为客户端测量：发请求 → 首个内容分片」。
- 验证：mock 服务下非流式 `raw` 为响应正文原文，流式 `raw` 为 5 行原始 SSE（含 `[DONE]`）；真实浏览器中两个分栏均能正确填充。

### 优化：调试结果改为结构化展示（不再堆 JSON 文本）

- 反馈「即使是原生响应，也要让人看得懂，不能一片密密麻麻的文字」。
  - 顶部指标卡：HTTP 状态（绿/红）、总耗时、TTFT（标注"客户端测量"）、SSE 分片数、输入/输出 token、缓存命中 token；
  - 响应头与 usage 改为**两列键值表**，不再让用户在一串 JSON 里找字段；
  - 推理内容与回答内容**分块用正常字号展示**（可读、可复制），不再是等宽密文；
  - SSE 分片**逐条列出**（`#1 内容 "…"` / `#2 usage …` / 末条 `[DONE]`），每条可展开查看原始 JSON；
  - 完整 HTTP 原文与解析后 JSON 收进**默认折叠**的 `<details>`，需要排查时再展开，兼顾"看得懂"与"可追溯"。
- 验证：真实浏览器下流式请求渲染出 6 个指标卡、10 行键值表、推理/回答两个内容块、6 条分片条目，原文折叠默认收起。

### 修复：ETA 被大幅高估（如 4/100 显示 1354s）

- 现象：`4/100 | 并发 5/5 | 0.07 req/s | TTFT 3.42s | ETA 1354s`，ETA 明显不合理。
- 原因不是单位，而是算法：ETA = 剩余数 ÷ **从运行开始到现在**的累计平均速率，而这个速率被两件事压低了——
  1. **预热时间算进了分母**：`_started_at` 在预热之前打点，但预热请求不计入完成数（默认 2 个）；
  2. **累计平均在开跑初期必然偏低**：启动、建连、首批慢请求都摊在很小的样本上。
  - 计时改为**预热之后**开始（预热不再污染 elapsed/速率）；
  - 速率改为**最近 20s 滑动窗口**（窗口未填充时退回累计平均），并在进度里同时给出 `rate`（近 20s）与 `rate_overall`（累计）；
  - 完成数 < 3 时不给 ETA，界面/终端显示「计算中」，避免出现上千秒的误导值。
- 验证：mock 每请求 1s、并发 2、预热 2、共 8 个请求——结束时 `elapsed=4.04s`（墙钟 5.17s，已排除 1s 预热），
  中间样本 `rate≈1.48 req/s`、`ETA≈3.4s`（与剩余量/速率一致），前两个样本显示「计算中」。

### 修复：max_tokens 配置"没生效"（extra_params 静默覆盖）

- 现象：表单/场景里配 `max_tokens: 256`，模型后台调用日志里实际输出 1024。
- 复现结论：请求体构造里 `extra_params` 是**最后合并**的（设计上允许模型/场景级覆盖顶层字段），
  若模型的 `extra_params` 里写了 `max_tokens`，就会静默盖掉表单值——实测发出的 body 为 `"max_tokens": 1024`。
  - 探测（调试）结果：新增冲突提示，例如
    `⚠️ extra_params 覆盖了这些参数：max_tokens：256 → 1024`（放在提示首条，最显眼）；
  - 压测开跑时（CLI 与平台日志）同样打印覆盖告警，避免"配置没生效"的误解；
  - 界面表单标注：模型 `extra_params` 与场景「覆盖参数」会覆盖压测页的同名表单参数；
  - 结果 JSON 的 `meta` 新增 `max_tokens` / `temperature` / `extra_params`（此前不落盘，事后无法核对）。
- 验证：① 冲突场景告警正确且实际发送 1024；② `extra_params` 不含 `max_tokens` 时表单值 256 正常生效；
  ③ 引擎（CLI/平台共用）开跑日志打印 `max_tokens: 256 → 1024` 告警。

### 修复：`"max_tokens": "256"` 这类字符串值导致参数被服务端忽略

- 反馈：场景 `extra_params` 里写的是 `{"max_tokens": "256"}`，模型后台看到的输出却是 1024。
- 原因：**值是字符串不是数字**。OpenAI 兼容接口要求 `max_tokens` 为整数，很多网关遇到非法类型时
  不报错而是**静默忽略**，回落到服务端默认长度（表现为"配置没生效"）。旧代码原样透传，没有类型校验。
  - `src/engine.py` 新增 `normalize_extra_params()`：把数值字段的数字字符串自动转成数字
    （`"256"` → `256`、`"0.7"` → `0.7`），覆盖 `max_tokens` / `max_completion_tokens` / `n` / `seed` /
    `temperature` / `top_p` / `presence_penalty` / `frequency_penalty` / `top_k` 等；
    非数字字符串（如 `"abc"`）保持原值并给出告警（交给服务端报错更安全）。
  - 规范化发生在构造请求体之前，**CLI 与平台共用**；探测提示与压测开跑日志都会说明发生了什么；
  - 界面 `extra_params` 输入框标注"数值要写成数字，如 `"max_tokens": 256`，写成 `"256"` 会被服务端忽略"。
- 验证：`{"max_tokens": "256"}` → 实际发送 `256`（int）并输出规范化提示；`{"max_tokens": "abc"}` → 保留原值 + 告警。

### 修复：场景名与提示词内容规模错位（如"输入-20k"实际发的是 10k 内容）

- 现象：`输入-20k` 场景实际请求的输入 token 与 `输入-10k` 完全一致（都是 4750）。
- 排查结论：场景库里该场景的提示词**内容**就是 10k 那份（9,797 字符 ≈ 7,094 token，和 `输入-10k` 一模一样），
  而 `prompts/input-20k.txt` 实际是 23,828 字符 ≈ 21,277 token。
  场景是在表单里"选提示词库 → 复制内容进场景"创建的，选错了提示词后**名字与内容脱钩**，界面上无从发现。
  - `GET /api/scenarios` 新增 `prompt_source` / `prompt_chars` / `prompt_tokens_est`；
    `GET /api/prompts` 新增 `chars` / `tokens_est`；
  - 场景列表新增「提示词来源」「规模(≈token)」两列——名字写 20k、实际 7,094 token 一眼可见；
  - 场景表单选中提示词后实时提示「已选提示词：X · N 字符 ≈ M token」；
  - 保存时若场景名里的数字（如 `20k`）与所选提示词规模相差超过 40%，弹确认框拦截（可强行继续）。
- 验证：浏览器实测——列表显示 `输入-20k | 内置文本（复制内容） | 7,094 (9,797 字符)`；
  以「输入-20k-测试 + 输入-10k 提示词」保存时弹出确认框，取消后未创建场景。
- 建议用法：批量建场景走「从 prompts 目录导入」（存 `prompt_file` 引用，内容随文件走，不会错位）。

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
