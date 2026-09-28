<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  cancelRouteProbeRun, createRouteProbeRun, getRouteProbeAttempt,
  getRouteProbeRun, listModels, listPromptFiles, routeProbePresets,
  routeProbeMarkdown, routeProbeRuns,
} from "../api/client";
import JsonBlock from "../components/JsonBlock.vue";
import StatCard from "../components/StatCard.vue";
import StatusTag from "../components/StatusTag.vue";

const loading = ref(true);
const models = ref([]);
const presets = ref([]);
const promptFiles = ref([]);
const runs = ref([]);
const selectedRun = ref(null);
const selectedAttempt = ref(null);
const attemptDrawer = ref(false);
const progress = reactive({ id: null, visible: false, status: "idle", done: 0, total: 0, current: "", error: "" });

const form = reactive({
  name: "",
  model_id: null,
  preset: "quick",
  prompt_source: "custom",
  prompt: '请只输出以下 JSON，不要解释：\n{"probe":"Q7K2M9","ok":true}',
  prompt_file: "",
  requests: 30,
  concurrency_text: "1",
  stream: false,
  max_tokens: 4,
  temperature: 0,
  timeout: 120,
  unique_prefix: true,
  http2: false,
  logprobs: false,
  extra_params_text: "{}",
  allow_large_cost: false,
});

let pollToken = 0;

const summary = computed(() => selectedRun.value?.summary || {});
const attempts = computed(() => selectedRun.value?.attempts_detail || []);
const signals = computed(() => summary.value.signals || []);
const estimatedTotal = computed(() => {
  const levels = String(form.concurrency_text || "")
    .split(",").map((x) => Number(x.trim())).filter((x) => x > 0);
  return Number(form.requests || 0) * Math.max(levels.length, 1);
});
const estimatedPromptTokens = computed(() => {
  if (form.prompt_source === "file") {
    return promptFiles.value.find((x) => x.file === form.prompt_file)?.tokens_est || 0;
  }
  return Math.ceil((form.prompt || "").length / 1.5);
});
const estimatedTokens = computed(() =>
  estimatedTotal.value * (Number(estimatedPromptTokens.value || 0) + Number(form.max_tokens || 0)));
const highCost = computed(() => estimatedTokens.value > 100000);

function formatTime(value) {
  return value ? String(value).slice(0, 19).replace("T", " ") : "—";
}

function fmt(value, digits = 1, fallback = "—") {
  return value == null ? fallback : Number(value).toFixed(digits);
}

function signalText(signal) {
  return (signal.groups || [])
    .slice(0, 4)
    .map((g) => {
      const samples = (g.samples || []).slice(0, 3)
        .map((s) => `#${s.sequence}`).join("/");
      return `${g.value || "<空>"} × ${g.count}（${(g.ratio * 100).toFixed(1)}%${samples ? `，样例 ${samples}` : ""}）`;
    })
    .join("；") || "未形成稳定分组";
}

function applyPreset(id) {
  if (id === "custom") return;
  const preset = presets.value.find((x) => x.id === id);
  if (!preset) return;
  const promptPatch = preset.id === "failure"
    ? { prompt_source: "file", prompt_file: "" }
    : {
        prompt_source: "custom",
        prompt_file: "",
        prompt: preset.prompt || form.prompt,
      };
  Object.assign(form, {
    preset: preset.id,
    requests: preset.requests,
    concurrency_text: (preset.concurrency_levels || [1]).join(","),
    stream: preset.stream,
    max_tokens: preset.max_tokens,
    unique_prefix: preset.unique_prefix ?? form.unique_prefix,
    ...promptPatch,
  });
}

async function load() {
  loading.value = true;
  try {
    const [m, p, files, r] = await Promise.all([
      listModels(), routeProbePresets(), listPromptFiles(), routeProbeRuns(),
    ]);
    models.value = m;
    presets.value = p;
    promptFiles.value = files;
    runs.value = r;
    if (!form.model_id && m.length) form.model_id = m[0].id;
  } finally {
    loading.value = false;
  }
}

async function refreshRuns() {
  runs.value = await routeProbeRuns();
}

async function startRun() {
  if (!form.model_id) return ElMessage.warning("请先选择模型");
  if (form.prompt_source === "file" && !form.prompt_file) {
    return ElMessage.warning("请选择 Prompt 文件");
  }
  if (estimatedTokens.value > 100000 && !form.allow_large_cost) {
    return ElMessage.warning("预计消耗超过 10 万 Token，请在高级设置中确认允许高成本检测");
  }
  const levels = String(form.concurrency_text || "")
    .split(",").map((x) => Number(x.trim())).filter((x) => x > 0);
  if (!levels.length) return ElMessage.warning("至少填写一个并发点");
  let extraParams = {};
  try {
    extraParams = form.extra_params_text.trim() ? JSON.parse(form.extra_params_text) : {};
    if (!extraParams || Array.isArray(extraParams) || typeof extraParams !== "object") throw new Error("必须是 JSON 对象");
  } catch (e) {
    return ElMessage.warning(`extra_params 不是合法 JSON 对象：${e.message}`);
  }
  const token = ++pollToken;
  Object.assign(progress, {
    id: null, visible: true, status: "running", done: 0,
    total: form.requests * levels.length, current: "创建检测任务", error: "",
  });
  try {
    const res = await createRouteProbeRun({
      name: form.name,
      model_id: form.model_id,
      preset: form.preset,
      prompt: form.prompt_source === "custom" ? form.prompt : "",
      prompt_file: form.prompt_source === "file" ? form.prompt_file : "",
      requests: form.requests,
      concurrency_levels: levels,
      stream: form.stream,
      max_tokens: form.max_tokens,
      temperature: form.temperature,
      timeout: form.timeout,
      unique_prefix: form.unique_prefix,
      http2: form.http2,
      logprobs: form.logprobs,
      extra_params: extraParams,
      allow_large_cost: form.allow_large_cost,
    });
    progress.id = res.run_id;
    ElMessage.info("路由指纹检测已开始");
    const run = await waitRun(res.run_id, token);
    if (run?.status === "finished") await openRun(res.run_id);
  } catch (e) {
    Object.assign(progress, { status: "failed", error: e.message });
  }
}

async function waitRun(id, token) {
  const deadline = Date.now() + 2 * 60 * 60 * 1000;
  while (token === pollToken && Date.now() < deadline) {
    const run = await getRouteProbeRun(id, 100);
    Object.assign(progress, { ...(run.progress || {}), id, status: run.status, error: run.error || "" });
    if (["finished", "failed", "canceled"].includes(run.status)) {
      await refreshRuns();
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

async function stopRun() {
  if (!progress.id) return;
  const res = await cancelRouteProbeRun(progress.id);
  ElMessage[res.ok ? "warning" : "info"](res.ok ? "已请求中止" : "任务已结束");
}

async function openRun(id) {
  selectedRun.value = await getRouteProbeRun(id, 2000);
}

async function openAttempt(row) {
  selectedAttempt.value = await getRouteProbeAttempt(row.id);
  attemptDrawer.value = true;
}

async function exportReport() {
  if (!selectedRun.value) return;
  const res = await routeProbeMarkdown(selectedRun.value.id);
  const blob = new Blob([res.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `route_probe_${selectedRun.value.id}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

onMounted(load);
onBeforeUnmount(() => { pollToken += 1; });
</script>

<template>
  <div v-loading="loading" class="route-page">
    <div class="panel intro">
      <div>
        <b>路由指纹检测</b>
        <span>不依赖上游渠道信息，通过重复请求的响应头、token、模型标识和失败阶段推断多路由。</span>
      </div>
      <el-tag type="warning" effect="plain" size="small">黑盒推断 · 不是压测</el-tag>
    </div>

    <div class="two-col">
      <section class="panel section">
        <div class="section-head">
          <div><b>检测配置</b><span>默认不重试，避免重复请求掩盖首次结果</span></div>
        </div>
        <el-form label-position="top" size="small">
          <div class="form-grid">
            <el-form-item label="模型">
              <el-select v-model="form.model_id" filterable placeholder="选择模型">
                <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="预设">
              <el-select v-model="form.preset" @change="applyPreset">
                <el-option v-for="p in presets" :key="p.id" :label="p.name" :value="p.id" />
                <el-option label="自定义" value="custom" />
              </el-select>
            </el-form-item>
          </div>
          <div class="preset-help">
            {{ form.preset === "custom" ? "自定义参数：请求数越高，低权重路由越容易被发现，但 Token 消耗也会增加。" : (presets.find((p) => p.id === form.preset)?.description || "") }}
          </div>
          <div class="form-grid scale-fields">
            <el-form-item label="每并发点请求数">
              <el-input-number
                v-model="form.requests"
                :min="1"
                :max="10000"
                @change="form.preset = 'custom'"
              />
            </el-form-item>
            <el-form-item label="并发点（逗号分隔）">
              <el-input v-model="form.concurrency_text" placeholder="1,10" />
            </el-form-item>
          </div>
          <el-form-item label="任务名">
            <el-input v-model="form.name" placeholder="可选" />
          </el-form-item>
          <el-radio-group v-model="form.prompt_source" size="small">
            <el-radio-button value="custom">短文本 Prompt</el-radio-button>
            <el-radio-button value="file">Prompt 文件</el-radio-button>
          </el-radio-group>
          <el-form-item v-if="form.prompt_source === 'custom'" label="固定 Prompt">
            <el-input v-model="form.prompt" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item v-else label="Prompt 文件">
            <el-select v-model="form.prompt_file" filterable placeholder="选择 prompts/*.txt">
              <el-option v-for="f in promptFiles" :key="f.file" :label="`${f.name} · ≈${f.tokens_est} token`" :value="f.file" />
            </el-select>
          </el-form-item>
          <el-collapse class="advanced">
            <el-collapse-item title="高级设置（请求规模、输出和网络参数）" name="advanced">
              <div class="form-grid">
                <el-form-item label="max_tokens">
                  <el-input-number v-model="form.max_tokens" :min="1" :max="65536" />
                </el-form-item>
                <el-form-item label="temperature">
                  <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
                </el-form-item>
              </div>
              <div class="form-grid">
                <el-form-item label="超时（秒）">
                  <el-input-number v-model="form.timeout" :min="10" :max="86400" />
                </el-form-item>
                <el-form-item label="extra_params JSON">
                  <el-input v-model="form.extra_params_text" type="textarea" :rows="2" placeholder="{}" />
                </el-form-item>
              </div>
              <div class="switches">
                <el-checkbox v-model="form.stream">流式</el-checkbox>
                <el-checkbox v-model="form.unique_prefix">每请求加唯一前缀（避免缓存干扰）</el-checkbox>
                <el-checkbox v-model="form.http2">HTTP/2</el-checkbox>
                <el-checkbox v-model="form.logprobs">请求 logprobs 指纹（并非所有网关支持）</el-checkbox>
                <el-checkbox v-model="form.allow_large_cost">允许高成本检测</el-checkbox>
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-form>
        <div class="cost-box" :class="{ warn: highCost }">
          <span>预计请求 <b>{{ estimatedTotal }}</b> 次</span>
          <span>估算输入 Token <b>≈{{ estimatedPromptTokens.toLocaleString() }}</b>/请求</span>
          <span>估算总 Token <b>≈{{ estimatedTokens.toLocaleString() }}</b></span>
          <span v-if="highCost" class="cost-warning">已超过 10 万 Token，请确认是否必要</span>
        </div>
        <div class="action-row">
          <span class="muted">默认建议先用短文本做轻量检测</span>
          <span class="spacer" />
          <el-button v-if="progress.status === 'running'" size="small" type="warning" plain @click="stopRun">中止</el-button>
          <el-button type="primary" size="small" :disabled="progress.status === 'running'" @click="startRun">开始检测</el-button>
        </div>
        <div v-if="progress.visible" class="progress-box">
          <div class="progress-title"><b>检测进度</b><StatusTag :value="progress.status" /><span>{{ progress.done }}/{{ progress.total }}</span></div>
          <el-progress :percentage="progress.total ? progress.done / progress.total * 100 : 0" :stroke-width="8" />
          <div v-if="progress.current" class="progress-detail">{{ progress.current }}</div>
          <div v-if="progress.error" class="error-text">{{ progress.error }}</div>
        </div>
      </section>

      <section class="panel section">
        <div class="section-head">
          <div><b>检测结论</b><span>基于当前原始观测的置信度推断</span></div>
          <div v-if="selectedRun" class="head-actions">
            <el-button link type="primary" size="small" @click="exportReport">导出证据报告</el-button>
            <StatusTag :value="selectedRun.verdict || selectedRun.status" />
          </div>
        </div>
        <template v-if="selectedRun">
          <div class="hero">
            <div>
              <span>结论</span>
              <b>{{ selectedRun.verdict || "—" }}</b>
              <small>{{ selectedRun.name }} · {{ selectedRun.model_label }}</small>
            </div>
            <div class="confidence">
              <span>置信度</span>
              <b>{{ selectedRun.confidence }}%</b>
              <small>证据质量：{{ summary.evidence_quality || "—" }}</small>
            </div>
          </div>
          <div class="stats">
            <StatCard label="探测请求" :value="summary.total || 0" unit="次" />
            <StatCard label="失败请求" :value="summary.errors || 0" unit="次" :tone="summary.errors ? 'warning' : 'success'" />
            <StatCard label="延迟 P50" :value="fmt(summary.latency_ms?.p50, 0)" unit="ms" />
            <StatCard label="延迟 P95" :value="fmt(summary.latency_ms?.p95, 0)" unit="ms" />
          </div>
          <div v-if="summary.reasons?.length" class="reasons">
            <div v-for="r in summary.reasons" :key="r">· {{ r }}</div>
          </div>
          <div v-if="summary.confidence_breakdown?.length" class="confidence-breakdown">
            <span v-for="item in summary.confidence_breakdown" :key="item.name">
              {{ item.name }} +{{ item.points }}/{{ item.max }}
            </span>
          </div>
          <el-table :data="signals" size="small" stripe>
            <el-table-column prop="name" label="指纹维度" width="120" />
            <el-table-column label="分组" min-width="300">
              <template #default="{ row }">{{ signalText(row) }}</template>
            </el-table-column>
            <el-table-column prop="strength" label="置信贡献" width="88" />
          </el-table>
        </template>
        <el-empty v-else description="选择一次检测任务后查看结论" :image-size="70" />
      </section>
    </div>

    <section class="panel section">
      <div class="section-head">
        <div><b>历史检测</b><span>{{ runs.length }} 条</span></div>
        <el-button link type="primary" size="small" @click="refreshRuns">刷新</el-button>
      </div>
      <el-table :data="runs" size="small" stripe max-height="250">
        <el-table-column prop="name" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column prop="model_label" label="模型" min-width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><StatusTag :value="row.verdict || row.status" /></template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" width="80">
          <template #default="{ row }">{{ row.confidence }}%</template>
        </el-table-column>
        <el-table-column prop="attempts" label="请求" width="70" />
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.finished_at || row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="72">
          <template #default="{ row }"><el-button link type="primary" size="small" @click="openRun(row.id)">查看</el-button></template>
        </el-table-column>
      </el-table>
    </section>

    <section v-if="selectedRun" class="panel section">
      <div class="section-head">
        <div><b>原始探测记录</b><span>按请求展示路由和错误指纹</span></div>
      </div>
      <el-table :data="attempts" size="small" stripe max-height="520">
        <el-table-column prop="sequence" label="#" width="52" />
        <el-table-column prop="concurrency" label="并发" width="58" />
        <el-table-column prop="status_code" label="HTTP" width="64" />
        <el-table-column prop="response_model" label="模型标识" min-width="110" show-overflow-tooltip />
        <el-table-column prop="system_fingerprint" label="system_fingerprint" min-width="130" show-overflow-tooltip />
        <el-table-column prop="id_prefix" label="ID前缀" width="90" />
        <el-table-column prop="prompt_tokens" label="输入Token" width="86" />
        <el-table-column prop="remote_ip" label="远端IP" width="120" />
        <el-table-column prop="http_version" label="HTTP" width="70" />
        <el-table-column label="延迟" width="82">
          <template #default="{ row }">{{ fmt(row.latency_ms, 0) }}ms</template>
        </el-table-column>
        <el-table-column prop="error_phase" label="错误阶段" width="90" />
        <el-table-column label="logprobs" width="78">
          <template #default="{ row }">{{ row.logprobs_available ? "有" : "—" }}</template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="150" show-overflow-tooltip />
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ row }"><el-button link type="primary" size="small" @click="openAttempt(row)">详情</el-button></template>
        </el-table-column>
      </el-table>
    </section>

    <el-drawer v-model="attemptDrawer" title="单次路由探测详情" size="820px">
      <template v-if="selectedAttempt">
        <div class="drawer-head">
          <div>
            <b>请求 #{{ selectedAttempt.sequence }} · 并发 {{ selectedAttempt.concurrency }}</b>
            <span>{{ selectedAttempt.client_request_id }}</span>
          </div>
          <StatusTag :value="selectedAttempt.status_code ? 'success' : 'failed'" />
        </div>
        <div class="detail-grid">
          <div><span>HTTP</span><b>{{ selectedAttempt.status_code ?? "—" }}</b></div>
          <div><span>远端 IP</span><b>{{ selectedAttempt.remote_ip || "—" }}</b></div>
          <div><span>HTTP 版本</span><b>{{ selectedAttempt.http_version || "—" }}</b></div>
          <div><span>错误阶段</span><b>{{ selectedAttempt.error_phase || "—" }}</b></div>
          <div><span>错误类型</span><b>{{ selectedAttempt.error_type || "—" }}</b></div>
          <div><span>TTFE / TTFT</span><b>{{ selectedAttempt.ttfe_ms ?? "—" }} / {{ selectedAttempt.ttft_ms ?? "—" }} ms</b></div>
        </div>
        <div class="drawer-section"><div class="sub-title">响应头</div><JsonBlock :value="selectedAttempt.response_headers" :rows="14" /></div>
        <div class="drawer-section"><div class="sub-title">Usage</div><JsonBlock :value="selectedAttempt.usage" :rows="10" /></div>
        <div class="drawer-section"><div class="sub-title">实际请求</div><JsonBlock :value="selectedAttempt.request" :rows="14" /></div>
        <div class="drawer-section"><div class="sub-title">原始响应 / 错误</div><JsonBlock :value="selectedAttempt.raw_response || selectedAttempt.error" :rows="18" /></div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.route-page { display: grid; gap: 12px; }
.panel { background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.intro { display: flex; align-items: center; justify-content: space-between; padding: 12px; }
.intro > div { display: grid; gap: 2px; }
.intro span, .muted { color: var(--c-text-3); font-size: var(--fs-xs); }
.two-col { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, .85fr); gap: 12px; align-items: start; }
.section { padding: 12px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; }
.section-head > div { display: grid; gap: 2px; }
.section-head .head-actions { display: flex; align-items: center; gap: 8px; }
.section-head b { font-size: var(--fs-base); }
.section-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.form-grid :deep(.el-select) { width: 100%; }
.preset-help { margin: -3px 0 9px; color: var(--c-text-3); font-size: var(--fs-xs); }
.scale-fields { margin-top: 4px; }
.advanced { margin-top: 4px; border-top: 1px solid var(--c-border); }
.advanced :deep(.el-collapse-item__header) { font-size: var(--fs-xs); color: var(--c-text-3); }
.advanced :deep(.el-collapse-item__content) { padding-bottom: 4px; }
.cost-box { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface-2); color: var(--c-text-3); font-size: var(--fs-xs); }
.cost-box b { color: var(--c-text-1); }
.cost-box.warn { border-color: var(--c-warning); background: var(--c-warning-bg); }
.cost-warning { color: var(--c-warning); font-weight: 600; }
.switches { display: flex; flex-wrap: wrap; gap: 12px; }
.action-row { display: flex; align-items: center; margin-top: 10px; }
.spacer { flex: 1; }
.progress-box { margin-top: 10px; padding: 9px 10px; border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface-2); }
.progress-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: var(--fs-xs); }
.progress-detail, .error-text { margin-top: 4px; color: var(--c-text-3); font-size: var(--fs-xs); }
.error-text { color: var(--c-danger); }
.hero { display: flex; align-items: center; justify-content: space-between; padding: 12px; border: 1px solid var(--c-border); border-left: 5px solid var(--c-warning); border-radius: var(--r-md); background: var(--c-surface-2); }
.hero > div:first-child { display: grid; gap: 2px; }
.hero span, .hero small, .confidence span { color: var(--c-text-3); font-size: var(--fs-xs); }
.hero b { font-size: 19px; }
.confidence { display: grid; text-align: right; }
.confidence b { color: var(--c-primary); font-size: 24px; }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 10px 0; }
.reasons { margin: 8px 0; padding: 8px 10px; border-radius: var(--r-md); background: var(--c-warning-bg); color: var(--c-warning); font-size: var(--fs-xs); }
.confidence-breakdown { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 8px; }
.confidence-breakdown span { padding: 3px 7px; border-radius: var(--r-sm); background: var(--c-surface-2); color: var(--c-text-3); font-size: 11px; }
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
.drawer-head > div { display: grid; gap: 2px; }
.drawer-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 10px; }
.detail-grid > div { display: grid; gap: 2px; padding: 7px; border: 1px solid var(--c-border); border-radius: var(--r-sm); }
.detail-grid span { color: var(--c-text-3); font-size: var(--fs-xs); }
.detail-grid b { font-size: var(--fs-sm); overflow-wrap: anywhere; }
.drawer-section { margin-top: 10px; }
.sub-title { margin-bottom: 5px; color: var(--c-text-2); font-size: var(--fs-xs); font-weight: 600; }
@media (max-width: 1150px) { .two-col { grid-template-columns: 1fr; } }
</style>
