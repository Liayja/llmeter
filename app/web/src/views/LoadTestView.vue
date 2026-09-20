<script setup>
import { computed, onActivated, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  cancelTask, createTask, getTask, listModels, listScenarios, probeModel, startTask,
} from "../api/client";
import JsonBlock from "../components/JsonBlock.vue";
import StatCard from "../components/StatCard.vue";

defineOptions({ name: "LoadTestView" });

const STORAGE_KEY = "llmeter:loadtest:session";
const mode = ref("scenarios");
const loading = ref(true);
const running = ref(false);
const taskId = ref(null);
const status = ref("idle");
const statusText = ref("空闲");
const logLines = ref([]);
const eventSource = ref(null);
const models = ref([]);
const scenarios = ref([]);
const selectedModels = ref([]);
const selectedScenarios = ref([]);
const activeGroups = ref(["basic", "advanced"]);

const params = reactive({
  name: "",
  concurrency: 5,
  concurrency_levels_text: "1,10,30",
  requests: 100,
  max_tokens: 256,
  timeout: 60,
  warmup: 2,
  retries: 2,
  stream: true,
  track_cache: true,
  unique_prefix: false,
  http2: false,
  prompt: "以春为题写一首五言绝句，要求有意境。",
});

const progress = reactive({
  completed: 0,
  total: 0,
  success: 0,
  failed: 0,
  retried: 0,
  rate: null,
  rate_overall: null,
  avg_latency: null,
  ttft_avg: null,
  input_tokens: 0,
  output_tokens: 0,
  output_tps: 0,
  cached_tokens: 0,
  cache_hit_rate: null,
  last_retry: null,
  retry_events: [],
  elapsed: 0,
  eta: null,
  active: 0,
  peak: 0,
  concurrency_target: params.concurrency,
  scene: "",
  scene_index: 0,
  scenes_total: 0,
  mode: "closed",
  qps_target: null,
});

let lastLogMilestone = -1;
let lastLogAt = 0;
let lastFailedSeen = 0;
let lastRetriedSeen = 0;
let lastRetrySeq = 0;
let lastSceneKey = "";
let resumeBusy = false;

const progressPct = computed(() => {
  if (!progress.total) return 0;
  return Math.min(100, progress.completed / progress.total * 100);
});
const canStart = computed(() => {
  if (running.value) return false;
  if (mode.value === "chat") return selectedModels.value.length === 1;
  return selectedModels.value.length > 0 && selectedScenarios.value.length > 0;
});
const etaText = computed(() => progress.eta == null ? "计算中" : `${format(progress.eta, 0)} 秒`);
const concurrencyLevels = computed(() => {
  const values = params.concurrency_levels_text
    .split(/[,，\s]+/)
    .map((v) => Number(v.trim()))
    .filter((v) => Number.isInteger(v) && v > 0);
  return [...new Set(values)].sort((a, b) => a - b);
});
const matrixRuns = computed(() => {
  if (mode.value === "chat") return 1;
  return selectedModels.value.length * selectedScenarios.value.length * concurrencyLevels.value.length;
});
const matrixTotalRequests = computed(() => matrixRuns.value * Math.max(1, params.requests));

const probe = reactive({
  visible: false,
  running: false,
  form: { model_id: null, prompt: "ping", max_tokens: 32, stream: false, extra_text: "" },
  result: null,
});

function format(v, digits = 2, fallback = "—") {
  return v == null || Number.isNaN(Number(v)) ? fallback : Number(v).toFixed(digits);
}

function saveSession() {
  if (!taskId.value) return;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      savedAt: Date.now(),
      taskId: taskId.value,
      status: status.value,
      statusText: statusText.value,
      running: running.value,
      mode: mode.value,
      activeGroups: [...activeGroups.value],
      params: { ...params },
      selectedModels: [...selectedModels.value],
      selectedScenarios: [...selectedScenarios.value],
      progress: { ...progress },
      logLines: logLines.value.slice(-200),
      lastRetrySeq,
    }));
  } catch {
    // sessionStorage 不可用时不影响任务本身，keep-alive 仍能保持当前页面状态。
  }
}

function restoreSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data?.taskId || Date.now() - Number(data.savedAt || 0) > 24 * 60 * 60 * 1000) {
      sessionStorage.removeItem(STORAGE_KEY);
      return false;
    }
    taskId.value = Number(data.taskId);
    status.value = data.status || "idle";
    statusText.value = data.statusText || "已恢复上次任务";
    running.value = Boolean(data.running);
    mode.value = data.mode === "chat" ? "chat" : "scenarios";
    if (Array.isArray(data.activeGroups)) activeGroups.value = data.activeGroups;
    if (data.params) Object.assign(params, data.params);
    if (Array.isArray(data.selectedModels)) selectedModels.value = data.selectedModels;
    if (Array.isArray(data.selectedScenarios)) selectedScenarios.value = data.selectedScenarios;
    if (data.progress) Object.assign(progress, data.progress);
    if (Array.isArray(data.logLines)) logLines.value = data.logLines;
    if (Number.isFinite(Number(data.lastRetrySeq))) lastRetrySeq = Number(data.lastRetrySeq);
    return true;
  } catch {
    return false;
  }
}

function appendLog(text) {
  const now = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  logLines.value.push(`[${now}] ${text}`);
  if (logLines.value.length > 200) logLines.value.splice(0, logLines.value.length - 200);
}

function resetProgress() {
  Object.assign(progress, {
    completed: 0, total: 0, success: 0, failed: 0, retried: 0,
    rate: null, rate_overall: null, avg_latency: null, ttft_avg: null,
    input_tokens: 0, output_tokens: 0, output_tps: 0,
    cached_tokens: 0, cache_hit_rate: null, elapsed: 0, eta: null,
    last_retry: null, retry_events: [],
    active: 0, peak: 0, concurrency_target: params.concurrency,
    scene: "", scene_index: 0, scenes_total: 0, mode: "closed", qps_target: null,
  });
  lastLogMilestone = -1;
  lastLogAt = 0;
  lastFailedSeen = 0;
  lastRetriedSeen = 0;
  lastRetrySeq = 0;
  lastSceneKey = "";
}

async function load() {
  loading.value = true;
  try {
    [models.value, scenarios.value] = await Promise.all([listModels(), listScenarios()]);
    if (mode.value === "chat" && !selectedModels.value.length && models.value.length) {
      selectedModels.value = [models.value[0].id];
    }
  } finally {
    loading.value = false;
  }
}

function closeEvents() {
  if (eventSource.value) {
    eventSource.value.close();
    eventSource.value = null;
  }
}

function onStatus(data) {
  status.value = data.status;
  if (data.status === "running") {
    statusText.value = "压测运行中";
    appendLog("任务已启动，开始发送请求");
  } else if (data.status === "success") {
    running.value = false;
    const isScenario = mode.value === "scenarios";
    statusText.value = isScenario
      ? `完成：${data.success ?? 0}/${data.total ?? 0} 组无失败`
      : `完成：成功 ${data.success ?? progress.success}/${data.total ?? progress.total}`;
    appendLog(
      isScenario
        ? `任务完成 | 组合无失败 ${data.success ?? 0}/${data.total ?? 0}` +
          ` | 最后场景请求成功 ${progress.success}/${progress.completed}` +
          ` | 失败 ${progress.failed} | 重试 ${progress.retried}` +
          ` | 耗时 ${format(progress.elapsed, 1)}s`
        : `任务完成 | 成功 ${data.success ?? progress.success}/${data.total ?? progress.total}` +
          ` | 失败 ${progress.failed} | 重试 ${progress.retried}` +
          ` | 耗时 ${format(progress.elapsed, 1)}s` +
          ` | 平均 QPS ${format(progress.rate_overall)} req/s` +
          ` | TTFT ${format(progress.ttft_avg, 3)}s`,
    );
    closeEvents();
  } else if (data.status === "canceled") {
    running.value = false;
    statusText.value = "已中止，已完成结果已保存";
    appendLog(
      `任务已中止 | 已完成 ${progress.completed}/${progress.total}` +
      ` | 成功 ${progress.success} | 失败 ${progress.failed} | 重试 ${progress.retried}` +
      ` | 耗时 ${format(progress.elapsed, 1)}s，已完成结果已保存`,
    );
    closeEvents();
  } else if (data.status === "failed") {
    running.value = false;
    statusText.value = `失败：${data.error || "请查看日志"}`;
    appendLog(`任务失败：${data.error || "未知错误"}`);
    closeEvents();
  }
  saveSession();
}

function progressSummary(p, pct) {
  const scene = p.scene
    ? `场景 ${p.scene_index}/${p.scenes_total} ${p.scene}`
    : "当前任务";
  return `进度 ${p.completed ?? 0}/${p.total ?? 0} (${pct.toFixed(0)}%)` +
    ` | ${scene}` +
    ` | QPS ${format(p.rate)} req/s` +
    ` | TTFT ${format(p.ttft_avg, 3)}s` +
    ` | 平均延迟 ${format(p.avg_latency, 3)}s` +
    ` | 成功 ${p.success ?? 0}` +
    ` | 失败 ${p.failed ?? 0}` +
    ` | 重试 ${p.retried ?? 0}`;
}

function retryCodeText(code) {
  const n = Number(code);
  if (n === 429) return "HTTP 429 限流";
  if (n === 0 || Number.isNaN(n)) return "连接失败或超时";
  if (n >= 500) return `HTTP ${n} 服务端错误`;
  return `HTTP ${n}`;
}

function retryReason(event) {
  let raw = String(event?.error || "").trim();
  try {
    const parsed = JSON.parse(raw);
    raw = parsed?.error?.message || parsed?.message || raw;
  } catch {
    // 非 JSON 错误体保持原文。
  }
  raw = raw.replace(/\s+/g, " ").trim();
  return raw ? raw.slice(0, 220) : "服务端未返回错误正文";
}

function logRetryEvents(events) {
  const fresh = (events || [])
    .filter((event) => Number(event?.seq) > lastRetrySeq)
    .sort((a, b) => Number(a.seq) - Number(b.seq));
  if (!fresh.length) return false;
  fresh.slice(-5).forEach((event) => {
    appendLog(
      `重试 | 请求 #${event.request_index} 第 ${event.attempt} 次失败` +
      ` | ${retryCodeText(event.status_code)}` +
      ` | ${event.backoff ?? "?"}s 后进行第 ${event.next_attempt} 次尝试` +
      ` | 原因：${retryReason(event)}`,
    );
  });
  lastRetrySeq = Number(fresh[fresh.length - 1].seq);
  return true;
}

function onProgress(p) {
  const sceneKey = `${p.scene_index || 0}:${p.scene || ""}`;
  if (sceneKey && sceneKey !== lastSceneKey) {
    lastSceneKey = sceneKey;
    lastLogMilestone = -1;
    lastLogAt = Date.now();
    appendLog(
      `场景开始 | ${p.scene_index || "?"}/${p.scenes_total || "?"} ${p.scene || "未命名场景"}`,
    );
  }

  Object.assign(progress, p);
  const pct = p.total ? Math.min(100, (p.completed || 0) / p.total * 100) : 0;
  const failedDelta = (p.failed || 0) - lastFailedSeen;
  const retriedDelta = (p.retried || 0) - lastRetriedSeen;
  if (failedDelta > 0) {
    appendLog(
      `警告 | 新增失败 ${failedDelta} 个，累计失败 ${p.failed} 个` +
      (p.last_error ? ` | 最近错误：${p.last_error}` : ""),
    );
  }
  const hasRetryPayload = Array.isArray(p.retry_events) && p.retry_events.length > 0;
  const detailedRetryLogged = logRetryEvents(p.retry_events);
  if (retriedDelta > 0 && !detailedRetryLogged && !hasRetryPayload) {
    appendLog(`提示 | 新增重试 ${retriedDelta} 次，累计重试 ${p.retried} 次`);
  }
  lastFailedSeen = p.failed || 0;
  lastRetriedSeen = p.retried || 0;

  const milestone = Math.floor(pct / 10) * 10;
  const now = Date.now();
  const shouldLog = (p.completed > 0 && milestone >= lastLogMilestone + 10)
    || (p.completed > 0 && now - lastLogAt >= 30000);
  if (shouldLog) {
    appendLog(progressSummary(p, pct));
    lastLogMilestone = milestone;
    lastLogAt = now;
  }
  saveSession();
}

function attachTask(id) {
  closeEvents();
  const es = new EventSource(`/api/tasks/${id}/events`);
  eventSource.value = es;
  es.addEventListener("status", (e) => {
    try { onStatus(JSON.parse(e.data)); } catch { /* ignore malformed event */ }
  });
  es.addEventListener("progress", (e) => {
    try { onProgress(JSON.parse(e.data)); } catch { /* ignore malformed event */ }
  });
  es.addEventListener("error", (e) => {
    if (!e.data) return;
    try {
      const d = JSON.parse(e.data);
      appendLog(`错误：${d.message || "未知错误"}`);
    } catch { /* connection-level error */ }
  });
  es.onerror = () => {
    if (["success", "failed", "canceled"].includes(status.value)) closeEvents();
  };
}

async function resumeRunningTask() {
  if (resumeBusy || !taskId.value) return;
  resumeBusy = true;
  try {
    const task = await getTask(taskId.value);
    if (task.status === "running" || (task.status === "queued" && running.value)) {
      running.value = true;
      status.value = task.status;
      statusText.value = "已恢复后台运行中的压测任务";
      if (!eventSource.value || eventSource.value.readyState === EventSource.CLOSED) {
        attachTask(taskId.value);
      }
    } else {
      running.value = false;
      status.value = task.status;
      if (task.status === "success") {
        statusText.value = mode.value === "scenarios"
          ? `已完成：${task.success}/${task.total} 组无失败`
          : `已完成：成功 ${task.success}/${task.total}`;
      } else if (task.status === "canceled") {
        statusText.value = "上次任务已中止";
      } else if (task.status === "queued") {
        statusText.value = "上次任务未成功启动";
      } else {
        statusText.value = `上次任务失败：${task.error || "请查看日志"}`;
      }
      closeEvents();
    }
  } catch (e) {
    running.value = false;
    statusText.value = `无法恢复任务 #${taskId.value}：${e.message}`;
    appendLog(statusText.value);
    closeEvents();
  } finally {
    resumeBusy = false;
    saveSession();
  }
}

async function runLoadTest() {
  if (!canStart.value) {
    return ElMessage.warning(mode.value === "chat" ? "请选择一个模型" : "请至少选择一个模型和一个场景");
  }
  if (mode.value === "scenarios" && !concurrencyLevels.value.length) {
    return ElMessage.warning("请至少填写一个有效并发点，例如 1,10,30");
  }
  const base = {
    name: params.name,
    concurrency: params.concurrency,
    requests: params.requests,
    max_tokens: params.max_tokens,
    timeout: params.timeout,
    warmup: params.warmup,
    retries: params.retries,
    stream: params.stream,
    track_cache: params.track_cache,
    unique_prefix: params.unique_prefix,
    http2: params.http2,
    prompt: params.prompt,
  };
  const body = mode.value === "chat"
    ? { ...base, mode: "chat", model_id: selectedModels.value[0] }
    : {
        ...base,
        mode: "scenarios",
        model_ids: [...selectedModels.value],
        scenario_ids: [...selectedScenarios.value],
        concurrency_levels: [...concurrencyLevels.value],
      };
  try {
    const task = await createTask(body);
    taskId.value = task.task_id;
    status.value = "queued";
    statusText.value = "任务已创建，正在启动…";
    resetProgress();
    logLines.value = [];
    const modelText = selectedModels.value.map((id) => {
      const model = models.value.find((m) => m.id === id);
      return model ? model.label : `#${id}`;
    }).join("、");
    const targetText = mode.value === "chat"
      ? `模型 ${modelText || "未选择"}`
      : `模型 ${modelText || "未选择"} | 场景 ${selectedScenarios.value.length} 个` +
        ` | 并发点 ${concurrencyLevels.value.join("/")}`;
    appendLog(
      `任务 #${task.task_id} 已创建：${task.name} | ${targetText}` +
      ` | ${mode.value === "chat" ? `并发 ${params.concurrency}` : `测试组合 ${matrixRuns.value} 组`}` +
      ` | 每点请求数 ${params.requests}` +
      ` | 流式 ${params.stream ? "是" : "否"} | warmup ${params.warmup}`,
    );
    await startTask(task.task_id);
    running.value = true;
    attachTask(task.task_id);
    saveSession();
  } catch (e) {
    running.value = false;
    status.value = "failed";
    statusText.value = e.message;
    saveSession();
  }
}

async function stopLoadTest() {
  if (!taskId.value || !running.value) return;
  await cancelTask(taskId.value);
  appendLog("已发送中止请求，等待当前请求收尾…");
  saveSession();
}

function openProbe() {
  if (!models.value.length) return ElMessage.warning("请先到资产库添加模型");
  probe.form.model_id = selectedModels.value[0] || models.value[0].id;
  probe.form.prompt = params.prompt;
  probe.form.max_tokens = Math.min(params.max_tokens, 512);
  probe.form.stream = params.stream;
  probe.form.extra_text = "";
  probe.result = null;
  probe.visible = true;
}

async function runProbe() {
  if (!probe.form.model_id) return ElMessage.warning("请选择模型");
  let extra = null;
  if (probe.form.extra_text.trim()) {
    try { extra = JSON.parse(probe.form.extra_text); }
    catch { return ElMessage.error("extra_params 不是合法 JSON"); }
  }
  probe.running = true;
  try {
    probe.result = await probeModel(probe.form.model_id, {
      prompt: probe.form.prompt,
      max_tokens: probe.form.max_tokens,
      stream: probe.form.stream,
      extra_params: extra,
    });
    (probe.result.hints || []).slice(0, 3).forEach((h) =>
      ElMessage({ message: h, type: "info", duration: 5000 }));
  } finally {
    probe.running = false;
  }
}

onMounted(async () => {
  restoreSession();
  await load();
  await resumeRunningTask();
});
onActivated(async () => {
  // keep-alive 返回压测页时重新拉取资产，确保新生成的 txt 场景立即出现在下拉列表。
  await load();
  await resumeRunningTask();
});
onBeforeUnmount(closeEvents);
</script>

<template>
  <div v-loading="loading" class="page">
    <div class="toolbar panel">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="scenarios">输入阶梯 / 多场景矩阵</el-radio-button>
        <el-radio-button value="chat">单次文本压测</el-radio-button>
      </el-radio-group>
      <el-divider direction="vertical" />
      <el-tag :type="running ? 'primary' : status === 'failed' ? 'danger' : 'info'" effect="light">
        {{ statusText }}
      </el-tag>
      <span class="spacer" />
      <el-button size="small" @click="load">刷新资产</el-button>
      <el-button size="small" :disabled="running" @click="openProbe">先试跑一次</el-button>
      <el-button size="small" type="danger" plain :disabled="!running" @click="stopLoadTest">中止</el-button>
      <el-button size="small" type="primary" :disabled="!canStart" @click="runLoadTest">开始压测</el-button>
    </div>

    <div class="workspace">
      <section class="panel config">
        <div class="section-title">压测配置</div>
        <el-collapse v-model="activeGroups">
          <el-collapse-item name="basic" title="目标与规模">
            <el-form label-position="top" size="small" class="compact-form">
              <el-form-item v-if="mode === 'chat'" label="模型">
                <el-select v-model="selectedModels" multiple collapse-tags placeholder="选择模型">
                  <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
                </el-select>
              </el-form-item>
              <template v-else>
                <el-form-item label="参与模型">
                  <el-select v-model="selectedModels" multiple collapse-tags placeholder="可多选">
                    <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
                  </el-select>
                </el-form-item>
                <el-form-item label="测试场景">
                  <el-select v-model="selectedScenarios" multiple collapse-tags placeholder="可多选">
                    <el-option
                      v-for="s in scenarios" :key="s.id"
                      :label="`${s.name} · ≈${(s.prompt_tokens_est || 0).toLocaleString()} token`"
                      :value="s.id"
                    />
                  </el-select>
                  <div v-if="!scenarios.length" class="field-hint">还没有场景，请到资产库创建。</div>
                  <div v-else class="field-hint">
                    建议按输入档位选择；报告会用模型实际返回的 prompt_tokens 校准输入规模。
                  </div>
                </el-form-item>
              </template>
              <el-form-item label="任务名称">
                <el-input v-model="params.name" placeholder="可选，例如 GLM-5.3 基线" />
              </el-form-item>
              <el-form-item v-if="mode === 'chat'" label="Prompt">
                <el-input v-model="params.prompt" type="textarea" :rows="4" />
              </el-form-item>
              <div class="form-grid">
                <el-form-item v-if="mode === 'chat'" label="并发数">
                  <el-input-number v-model="params.concurrency" :min="1" :max="500" controls-position="right" />
                </el-form-item>
                <el-form-item v-else label="并发点">
                  <el-input v-model="params.concurrency_levels_text" placeholder="例如 1,10,30" />
                  <div class="field-hint">每个点单独运行，用于观察吞吐拐点和 TTFT 退化。</div>
                </el-form-item>
                <el-form-item :label="mode === 'chat' ? '请求数' : '每点请求数'">
                  <el-input-number v-model="params.requests" :min="1" :max="100000" controls-position="right" />
                </el-form-item>
                <el-form-item label="max_tokens">
                  <el-input-number v-model="params.max_tokens" :min="1" :max="65536" controls-position="right" />
                </el-form-item>
                <el-form-item label="超时（秒）">
                  <el-input-number v-model="params.timeout" :min="1" :max="3600" controls-position="right" />
                </el-form-item>
              </div>
              <div v-if="mode === 'scenarios'" class="matrix-hint">
                阶梯矩阵：{{ selectedModels.length }} 模型 × {{ selectedScenarios.length }} 场景 ×
                {{ concurrencyLevels.length }} 并发点 = <b>{{ matrixRuns }}</b> 个测试组合，
                预计共请求 <b>{{ matrixTotalRequests.toLocaleString() }}</b> 次（不含 warmup）。
              </div>
            </el-form>
          </el-collapse-item>

          <el-collapse-item name="advanced" title="高级参数">
            <el-form label-position="top" size="small" class="compact-form">
              <div class="form-grid">
                <el-form-item label="warmup">
                  <el-input-number v-model="params.warmup" :min="0" :max="100" controls-position="right" />
                </el-form-item>
                <el-form-item label="retries">
                  <el-input-number v-model="params.retries" :min="0" :max="20" controls-position="right" />
                </el-form-item>
              </div>
              <div class="switch-list">
                <label><el-switch v-model="params.stream" size="small" /> 流式请求（统计 TTFT）</label>
                <label><el-switch v-model="params.track_cache" size="small" /> 统计缓存命中 token</label>
                <label><el-switch v-model="params.unique_prefix" size="small" /> 破坏 prefix cache</label>
                <label><el-switch v-model="params.http2" size="small" /> 使用 HTTP/2</label>
              </div>
            </el-form>
          </el-collapse-item>
        </el-collapse>
      </section>

      <section class="panel live">
        <div class="section-title">
          实时运行
          <span v-if="progress.scene" class="scene">
            当前场景 {{ progress.scene_index }}/{{ progress.scenes_total }} · {{ progress.scene }}
          </span>
        </div>
        <el-progress
          :percentage="progressPct"
          :stroke-width="12"
          :status="status === 'failed' ? 'exception' : status === 'success' ? 'success' : undefined"
          :format="() => `${progress.completed}/${progress.total} (${progressPct.toFixed(0)}%)`"
        />
        <div class="stats">
          <StatCard label="实时速率" :value="format(progress.rate)" unit="req/s" hint="近 20 秒窗口" tone="primary" />
          <StatCard label="TTFT 均值" :value="format(progress.ttft_avg, 3)" unit="s" hint="首 token 延迟" />
          <StatCard label="平均延迟" :value="format(progress.avg_latency, 3)" unit="s" hint="已完成请求" />
          <StatCard label="已用时 / 预计剩余" :value="`${format(progress.elapsed, 0)} / ${etaText}`" hint="预计剩余仅为粗略估算" />
          <StatCard label="成功 / 重试" :value="`${progress.success} / ${progress.retried}`" unit="次" :tone="progress.retried ? 'warning' : 'success'" />
          <StatCard label="失败请求" :value="progress.failed" unit="次" :tone="progress.failed ? 'danger' : 'success'" />
          <StatCard label="输出吞吐" :value="format(progress.output_tps, 2)" unit="token/s" hint="服务端 usage 累计" />
          <StatCard
            v-if="params.track_cache"
            label="缓存命中率"
            :value="format(progress.cache_hit_rate, 1)"
            unit="%"
            :hint="`命中 ${progress.cached_tokens} token`"
            tone="warning"
          />
          <StatCard
            v-if="progress.mode === 'open'"
            label="在途 / 峰值积压"
            :value="`${progress.active} / ${progress.peak}`"
            :hint="`目标 ${progress.qps_target ?? '—'} QPS`"
          />
        </div>
        <div class="log-head">
          <span>运行日志</span>
          <el-button link size="small" :disabled="!logLines.length" @click="logLines = []">清空</el-button>
        </div>
        <div class="log">
          <div v-if="!logLines.length" class="log-empty">启动后记录状态变化、场景切换、阶段性进度、重试和异常；连续指标请查看上方指标卡。</div>
          <div v-for="(line, i) in logLines" :key="i">{{ line }}</div>
        </div>
      </section>
    </div>

    <el-drawer v-model="probe.visible" title="连通性与请求结构调试" size="720px">
      <el-form label-position="top" size="small">
        <div class="form-grid">
          <el-form-item label="模型">
            <el-select v-model="probe.form.model_id" filterable>
              <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="max_tokens">
            <el-input-number v-model="probe.form.max_tokens" :min="1" :max="65536" controls-position="right" />
          </el-form-item>
        </div>
        <el-form-item label="Prompt"><el-input v-model="probe.form.prompt" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="额外参数 extra_params">
          <el-input v-model="probe.form.extra_text" type="textarea" :rows="2" placeholder='留空使用模型配置，例如 {"thinking":{"type":"disabled"}}' />
        </el-form-item>
        <el-form-item>
          <el-switch v-model="probe.form.stream" size="small" active-text="流式（展示 TTFT 与 SSE）" />
          <el-button class="probe-run" type="primary" size="small" :loading="probe.running" @click="runProbe">
            发送测试请求
          </el-button>
        </el-form-item>
      </el-form>

      <template v-if="probe.result">
        <div class="stats probe-stats">
          <StatCard label="HTTP 状态" :value="probe.result.response?.status_code ?? '—'"
                    :tone="probe.result.response?.status_code === 200 ? 'success' : 'danger'" />
          <StatCard label="总耗时" :value="format(probe.result.response?.elapsed, 3)" unit="s" />
          <StatCard v-if="probe.result.response?.stream" label="TTFT"
                    :value="format(probe.result.response?.ttft, 3)" unit="s" />
          <StatCard label="输入 / 输出 token"
                    :value="`${probe.result.response?.usage?.prompt_tokens ?? '—'} / ${probe.result.response?.usage?.completion_tokens ?? '—'}`" />
          <StatCard label="缓存命中 token"
                    :value="probe.result.response?.usage?.prompt_tokens_details?.cached_tokens ?? '—'" unit="token" />
        </div>
        <div class="probe-section">
          <div class="sub-title">实际请求体 · {{ probe.result.request?.url }}</div>
          <JsonBlock :value="probe.result.request?.body" :rows="16" />
        </div>
        <div class="probe-section">
          <div class="sub-title">实际响应 · 原始返回</div>
          <JsonBlock
            :value="probe.result.response?.raw || probe.result.response"
            :fallback="probe.result.response?.error || ''"
            :rows="20"
          />
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { min-height: 100%; }
.panel { background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.toolbar { display: flex; align-items: center; gap: var(--sp-2); padding: 8px 12px; margin-bottom: 12px; }
.spacer { flex: 1; }
.workspace { display: grid; grid-template-columns: minmax(360px, 5fr) minmax(520px, 8fr); gap: 12px; align-items: start; }
.config, .live { padding: 12px; }
.section-title { display: flex; align-items: baseline; gap: 10px; min-height: 28px; font-weight: 600; color: var(--c-text-1); }
.scene { color: var(--c-text-3); font-size: var(--fs-xs); font-weight: 400; }
.compact-form :deep(.el-form-item) { margin-bottom: 10px; }
.compact-form :deep(.el-select), .compact-form :deep(.el-input-number) { width: 100%; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.switch-list { display: grid; gap: 8px; color: var(--c-text-2); font-size: var(--fs-sm); }
.switch-list label { display: flex; align-items: center; gap: 8px; }
.field-hint { margin-top: 4px; color: var(--c-text-3); font-size: var(--fs-xs); }
.matrix-hint {
  padding: 7px 9px; margin-bottom: 8px; color: var(--c-text-2);
  background: var(--c-surface-2); border-left: 3px solid var(--c-primary);
  border-radius: var(--r-sm); font-size: var(--fs-xs); line-height: 1.55;
}
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(136px, 1fr)); gap: 8px; margin-top: 12px; }
.log-head { display: flex; align-items: center; justify-content: space-between; margin: 12px 0 6px; color: var(--c-text-2); font-size: var(--fs-sm); }
.log {
  height: 260px; overflow: auto; padding: 8px 10px; background: #1e2430; border-radius: var(--r-md);
  color: #c8d3e0; font: 12px/1.55 "Cascadia Mono", Consolas, monospace;
}
.log-empty { color: #7f8ea3; }
.probe-run { margin-left: 12px; }
.probe-stats { margin-bottom: 12px; }
.probe-section { margin-top: 12px; }
.sub-title { margin-bottom: 5px; color: var(--c-text-2); font-size: var(--fs-xs); font-weight: 600; }
@media (max-width: 1100px) {
  .workspace { grid-template-columns: 1fr; }
}
</style>
