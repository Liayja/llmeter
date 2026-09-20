<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  cancelBaseline, cancelParityRun, createBaseline, createParityRun, deleteBaseline,
  getBaseline, getParityRun,
  listModels, parityBaselines, parityDimensions, parityMarkdown, parityRuns,
} from "../api/client";
import JsonBlock from "../components/JsonBlock.vue";
import StatCard from "../components/StatCard.vue";
import StatusTag from "../components/StatusTag.vue";

const loading = ref(true);
const models = ref([]);
const dimensions = ref([]);
const baselines = ref([]);
const runs = ref([]);
const selectedDims = ref([]);

const baselineForm = reactive({ name: "", model_id: null });
const runForm = reactive({ name: "", baseline_id: null, model_id: null });
const baselineProgress = reactive({ id: null, visible: false, status: "idle", done: 0, total: 0, current: "", last_status: null, last_error: "", error: "" });
const runProgress = reactive({ id: null, visible: false, status: "idle", done: 0, total: 0, current: "", last_status: null, last_error: "", error: "" });
const baselineDrawer = reactive({ visible: false, loading: false, data: null });
const reportDrawer = reactive({ visible: false, loading: false, run: null });

let pollToken = 0;

const dimDescriptions = {
  D1: "检查官方接口的参数语义是否一致：max_tokens、system 角色、usage 与推理字段。",
  D2: "用固定文本对比 tokenizer 指纹；偏差过大通常意味着 tokenizer 或模板实现不同。",
  D8: "验证 JSON 结构化输出和 function calling 的工具名、参数、触发行为。",
  D9: "模拟孤儿 tool、错误 tool_call_id 等异常协议，观察拒绝方式是否与官方一致。",
};

const selectedCaseCount = computed(() =>
  dimensions.value.filter((d) => selectedDims.value.includes(d.id)).reduce((n, d) => n + d.cases, 0));
const readyBaselines = computed(() => baselines.value.filter((b) => b.status === "ready"));
const report = computed(() => reportDrawer.run?.report || {});

function dimMeta(id) {
  return dimensions.value.find((d) => d.id === id) || { id, name: id, weight: 0, cases: 0 };
}

function formatTime(value) {
  return value ? String(value).slice(0, 19).replace("T", " ") : "—";
}

function formatRaw(value, fallback = "") {
  if (value == null || value === "") return fallback || "（该记录没有保存原始响应）";
  return value;
}

function progressPct(p) {
  return p.total ? Math.min(100, p.done / p.total * 100) : 0;
}

async function load() {
  loading.value = true;
  try {
    const [m, d, b, r] = await Promise.all([
      listModels(), parityDimensions(), parityBaselines(), parityRuns(),
    ]);
    models.value = m;
    dimensions.value = d;
    baselines.value = b;
    runs.value = r;
    if (!selectedDims.value.length) selectedDims.value = d.map((x) => x.id);
    if (!baselineForm.model_id && m.length) baselineForm.model_id = m[0].id;
    if (!runForm.model_id && m.length) runForm.model_id = m[0].id;
    if (!runForm.baseline_id && readyBaselines.value.length) {
      runForm.baseline_id = readyBaselines.value[0].id;
    }
  } finally {
    loading.value = false;
  }
}

async function refreshLists() {
  [baselines.value, runs.value] = await Promise.all([parityBaselines(), parityRuns()]);
  if (!runForm.baseline_id && readyBaselines.value.length) {
    runForm.baseline_id = readyBaselines.value[0].id;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitBaseline(id, token) {
  const deadline = Date.now() + 30 * 60 * 1000;
  while (token === pollToken && Date.now() < deadline) {
    const b = await getBaseline(id);
    Object.assign(baselineProgress, { ...(b.progress || {}), status: b.status, error: b.error || "" });
    if (["ready", "failed", "canceled"].includes(b.status)) {
      await refreshLists();
      if (b.status === "ready") ElMessage.success("基线建立完成");
      return b;
    }
    await sleep(1500);
  }
  if (token === pollToken) {
    Object.assign(baselineProgress, { status: "failed", error: "前端等待超过 30 分钟，请刷新列表查看后台任务状态" });
  }
  return null;
}

async function waitRun(id, token) {
  const deadline = Date.now() + 30 * 60 * 1000;
  while (token === pollToken && Date.now() < deadline) {
    const r = await getParityRun(id);
    Object.assign(runProgress, { ...(r.progress || {}), status: r.status, error: r.error || "" });
    if (["finished", "failed", "canceled"].includes(r.status)) {
      await refreshLists();
      if (r.status === "finished") {
        ElMessage.success("候选测试与对比报告已完成");
        await openReport(id);
      }
      return r;
    }
    await sleep(1500);
  }
  if (token === pollToken) {
    Object.assign(runProgress, { status: "failed", error: "前端等待超过 30 分钟，请刷新列表查看后台任务状态" });
  }
  return null;
}

async function startBaseline() {
  if (!baselineForm.model_id) return ElMessage.warning("请先选择官方基线模型");
  if (!selectedDims.value.length) return ElMessage.warning("至少选择一个测试维度");
  try {
    await ElMessageBox.confirm(
      `将按所选维度发送约 ${selectedCaseCount.value} 次请求。建立期间请勿关闭本机服务。`,
      "建立一致性基线",
      { confirmButtonText: "开始建立", cancelButtonText: "取消", type: "info" },
    );
  } catch { return; }
  const token = ++pollToken;
  Object.assign(baselineProgress, {
    id: null, visible: true, status: "running", done: 0, total: selectedCaseCount.value,
    current: "正在创建任务…", last_status: null, last_error: "", error: "",
  });
  try {
    const res = await createBaseline({
      name: baselineForm.name,
      model_id: baselineForm.model_id,
      dimensions: [...selectedDims.value],
    });
    baselineProgress.id = res.baseline_id;
    ElMessage.info("基线任务已开始，正在发送测试请求");
    await waitBaseline(res.baseline_id, token);
  } catch (e) {
    Object.assign(baselineProgress, { status: "failed", error: e.message });
  }
}

async function startRun() {
  if (!runForm.baseline_id) return ElMessage.warning("请选择一个已就绪的基线");
  if (!runForm.model_id) return ElMessage.warning("请选择候选模型");
  const token = ++pollToken;
  Object.assign(runProgress, {
    id: null, visible: true, status: "running", done: 0, total: 0,
    current: "正在创建任务…", last_status: null, last_error: "", error: "",
  });
  try {
    const res = await createParityRun({
      baseline_id: runForm.baseline_id,
      name: runForm.name,
      model_id: runForm.model_id,
    });
    runProgress.id = res.run_id;
    ElMessage.info("候选测试已开始，完成后将自动打开报告");
    await waitRun(res.run_id, token);
  } catch (e) {
    Object.assign(runProgress, { status: "failed", error: e.message });
  }
}

async function stopBaseline() {
  if (!baselineProgress.id) return;
  try {
    await ElMessageBox.confirm("中止基线任务？已经完成的用例会保留，但该基线不会标记为就绪。", "中止确认", { type: "warning" });
  } catch { return; }
  try {
    const res = await cancelBaseline(baselineProgress.id);
    ElMessage[res.ok ? "warning" : "info"](res.ok ? "已请求中止基线任务" : "任务已结束，无需中止");
  } catch { /* API 客户端已经展示了错误 */ }
}

async function stopRun() {
  if (!runProgress.id) return;
  try {
    await ElMessageBox.confirm("中止候选测试？已经完成的用例会保留，但不会生成对比报告。", "中止确认", { type: "warning" });
  } catch { return; }
  try {
    const res = await cancelParityRun(runProgress.id);
    ElMessage[res.ok ? "warning" : "info"](res.ok ? "已请求中止候选测试" : "任务已结束，无需中止");
  } catch { /* API 客户端已经展示了错误 */ }
}

async function openBaselineDetail(id) {
  reportDrawer.visible = false;
  baselineDrawer.visible = true;
  baselineDrawer.loading = true;
  try {
    baselineDrawer.data = await getBaseline(id);
  } finally {
    baselineDrawer.loading = false;
  }
}

async function openReport(id) {
  baselineDrawer.visible = false;
  reportDrawer.visible = true;
  reportDrawer.loading = true;
  try {
    reportDrawer.run = await getParityRun(id);
  } finally {
    reportDrawer.loading = false;
  }
}

async function removeBaseline(row) {
  try {
    await ElMessageBox.confirm(`删除基线「${row.name}」？已有对比报告不受影响。`, "删除确认", { type: "warning" });
  } catch { return; }
  await deleteBaseline(row.id);
  ElMessage.success("基线已删除");
  await refreshLists();
}

async function exportMarkdown() {
  if (!reportDrawer.run) return;
  const res = await parityMarkdown(reportDrawer.run.id);
  const blob = new Blob([res.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `parity_report_${reportDrawer.run.id}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

onMounted(load);
onBeforeUnmount(() => { pollToken += 1; });
</script>

<template>
  <div v-loading="loading" class="page">
    <div class="panel step-panel">
      <el-steps :active="runForm.baseline_id ? (runs.length ? 2 : 1) : 0" finish-status="success" simple>
        <el-step title="建立基线" description="选择官方模型并采集一次固定用例" />
        <el-step title="候选测试" description="选择既有基线，只跑被测模型" />
        <el-step title="对比报告" description="查看结论、维度差异与原始响应" />
      </el-steps>
    </div>

    <div class="two-col">
      <section class="panel section">
        <div class="section-head">
          <div>
            <b>① 建立官方基线</b>
            <span>基线可重复使用，不必每次同时跑官方模型</span>
          </div>
        </div>
        <el-form label-position="top" size="small">
          <div class="form-grid">
            <el-form-item label="官方模型">
              <el-select v-model="baselineForm.model_id" filterable placeholder="选择官方模型">
                <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="基线名称">
              <el-input v-model="baselineForm.name" placeholder="可选，例如 GLM-5.3 官方基线" />
            </el-form-item>
          </div>
        </el-form>
        <div class="dimension-grid">
          <label v-for="d in dimensions" :key="d.id" class="dimension-card" :class="{ active: selectedDims.includes(d.id) }">
            <el-checkbox v-model="selectedDims" :value="d.id" />
            <div class="dim-body">
              <div class="dim-title"><b>{{ d.id }}</b> {{ d.name }}</div>
              <p>{{ dimDescriptions[d.id] || "用于核对模型行为是否与官方保持一致。" }}</p>
              <div class="dim-meta">
                <span>{{ d.cases }} 条用例</span>
                <span>权重 {{ d.weight }}%</span>
                <span v-if="d.id === 'D1'">含参数闸门</span>
              </div>
            </div>
          </label>
        </div>
        <div class="action-row">
          <span class="muted">预计请求：{{ selectedCaseCount }} 次</span>
          <span class="spacer" />
          <el-button
            v-if="baselineProgress.status === 'running'"
            size="small" type="warning" plain :disabled="!baselineProgress.id"
            @click="stopBaseline"
          >
            中止基线
          </el-button>
          <el-button type="primary" size="small" :disabled="baselineProgress.status === 'running'" @click="startBaseline">
            建立基线
          </el-button>
        </div>
        <div v-if="baselineProgress.visible" class="progress-box">
          <div class="progress-title">
            <b>建立基线</b>
            <StatusTag :value="baselineProgress.status" />
            <span>{{ baselineProgress.done }}/{{ baselineProgress.total }}</span>
          </div>
          <el-progress :percentage="progressPct(baselineProgress)" :stroke-width="8" />
          <div v-if="baselineProgress.current" class="progress-detail">当前用例：{{ baselineProgress.current }}</div>
          <div v-if="baselineProgress.last_error" class="error-text">上一条错误：{{ baselineProgress.last_error }}</div>
          <div v-if="baselineProgress.error" class="error-text">失败原因：{{ baselineProgress.error }}</div>
          <div class="progress-detail">可随时中止；已完成的用例会保留在数据库里。</div>
        </div>
      </section>

      <section class="panel section">
        <div class="section-head">
          <div>
            <b>② 测试候选模型</b>
            <span>复用已有基线，只运行被测模型</span>
          </div>
        </div>
        <el-form label-position="top" size="small">
          <div class="form-grid">
            <el-form-item label="已有基线">
              <el-select v-model="runForm.baseline_id" placeholder="选择就绪基线">
                <el-option
                  v-for="b in readyBaselines" :key="b.id"
                  :label="`${b.name} · ${b.items} 条`" :value="b.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="候选模型">
              <el-select v-model="runForm.model_id" filterable placeholder="选择被测模型">
                <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="任务名称">
            <el-input v-model="runForm.name" placeholder="可选，例如 GodawnAi-GLM-5.3 对比" />
          </el-form-item>
        </el-form>
        <div v-if="!readyBaselines.length" class="inline-alert">
          还没有可用基线。先完成左侧建立基线，候选测试才能开始。
        </div>
        <div class="action-row">
          <span class="muted">候选只跑所选基线覆盖的维度与用例。</span>
          <span class="spacer" />
          <el-button
            v-if="runProgress.status === 'running'"
            size="small" type="warning" plain :disabled="!runProgress.id"
            @click="stopRun"
          >
            中止候选
          </el-button>
          <el-button type="primary" size="small" :disabled="runProgress.status === 'running' || !readyBaselines.length" @click="startRun">
            开始候选测试
          </el-button>
        </div>
        <div v-if="runProgress.visible" class="progress-box">
          <div class="progress-title">
            <b>候选测试</b>
            <StatusTag :value="runProgress.status" />
            <span>{{ runProgress.done }}/{{ runProgress.total }}</span>
          </div>
          <el-progress :percentage="progressPct(runProgress)" :stroke-width="8" />
          <div v-if="runProgress.current" class="progress-detail">当前用例：{{ runProgress.current }}</div>
          <div v-if="runProgress.last_error" class="error-text">上一条错误：{{ runProgress.last_error }}</div>
          <div v-if="runProgress.error" class="error-text">失败原因：{{ runProgress.error }}</div>
          <div class="progress-detail">可随时中止；已完成的用例会保留在数据库里。</div>
        </div>
      </section>
    </div>

    <div class="two-col tables">
      <section class="panel section">
        <div class="section-head">
          <div><b>已有基线</b><span>{{ baselines.length }} 条</span></div>
          <el-button link type="primary" size="small" @click="refreshLists">刷新</el-button>
        </div>
        <el-table :data="baselines" size="small" stripe height="330">
          <el-table-column prop="name" label="基线" min-width="150" show-overflow-tooltip />
          <el-table-column prop="model_label" label="官方模型" min-width="130" show-overflow-tooltip />
          <el-table-column label="状态" width="86">
            <template #default="{ row }"><StatusTag :value="row.status" /></template>
          </el-table-column>
          <el-table-column prop="items" label="用例" width="66" />
          <el-table-column label="建立时间" width="146">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="126" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openBaselineDetail(row.id)">查看</el-button>
              <el-button link type="danger" size="small" @click="removeBaseline(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty><el-empty description="暂无基线，请先完成第 ① 步" /></template>
        </el-table>
      </section>

      <section class="panel section">
        <div class="section-head">
          <div><b>③ 对比任务</b><span>{{ runs.length }} 条</span></div>
          <el-button link type="primary" size="small" @click="refreshLists">刷新</el-button>
        </div>
        <el-table :data="runs" size="small" stripe height="330">
          <el-table-column prop="name" label="任务" min-width="160" show-overflow-tooltip />
          <el-table-column prop="model_label" label="候选模型" min-width="130" show-overflow-tooltip />
          <el-table-column label="状态" width="86">
            <template #default="{ row }"><StatusTag :value="row.status === 'finished' ? 'success' : row.status" /></template>
          </el-table-column>
          <el-table-column label="结论" width="110">
            <template #default="{ row }">
              <span :class="['verdict', String(row.verdict || '').includes('不等价') ? 'bad' : String(row.verdict || '').includes('可疑') ? 'warn' : 'good']">
                {{ row.verdict || "—" }}
              </span>
              <span v-if="row.score != null" class="muted"> {{ row.score }}</span>
            </template>
          </el-table-column>
          <el-table-column label="完成时间" width="146">
            <template #default="{ row }">{{ formatTime(row.finished_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="72" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openReport(row.id)">查看</el-button>
            </template>
          </el-table-column>
          <template #empty><el-empty description="暂无候选测试任务" /></template>
        </el-table>
      </section>
    </div>

    <el-drawer v-model="baselineDrawer.visible" title="基线用例明细" size="820px">
      <div v-loading="baselineDrawer.loading">
        <template v-if="baselineDrawer.data">
          <div class="drawer-head">
            <div>
              <b>{{ baselineDrawer.data.name }}</b>
              <span>{{ baselineDrawer.data.model_label }} · {{ baselineDrawer.data.case_set }} · {{ baselineDrawer.data.items }} 条</span>
            </div>
            <StatusTag :value="baselineDrawer.data.status" />
          </div>
          <el-table :data="baselineDrawer.data.items_detail || []" size="small" stripe max-height="650">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="case-expand">
                  <div>
                    <div class="sub-title">实际请求体</div>
                    <JsonBlock :value="row.request" :rows="14" />
                  </div>
                  <div>
                    <div class="sub-title">
                      实际响应（原始{{ row.status_code >= 400 ? " · 报错原文" : "" }}{{ row.raw_response_truncated ? " · 已截断" : "" }}）
                    </div>
                    <JsonBlock :value="formatRaw(row.raw_response, row.error)" :rows="18" />
                    <div v-if="row.param_notes?.length" class="param-note">
                      参数说明：{{ row.param_notes.join("；") }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="case_id" label="用例 ID" min-width="142" show-overflow-tooltip />
            <el-table-column prop="name" label="用例" min-width="190" show-overflow-tooltip />
            <el-table-column prop="status_code" label="HTTP" width="64" />
            <el-table-column prop="behavior_class" label="行为" width="112" show-overflow-tooltip />
            <el-table-column label="Token 输入/输出" width="112">
              <template #default="{ row }">{{ row.prompt_tokens ?? 0 }} / {{ row.completion_tokens ?? 0 }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="78">
              <template #default="{ row }">{{ row.latency == null ? "—" : Number(row.latency).toFixed(2) + "s" }}</template>
            </el-table-column>
          </el-table>
        </template>
      </div>
    </el-drawer>

    <el-drawer v-model="reportDrawer.visible" title="一致性对比报告" size="920px">
      <div v-loading="reportDrawer.loading">
        <template v-if="reportDrawer.run && report.verdict">
          <div class="report-hero" :class="report.verdict">
            <div>
              <span class="report-label">综合结论</span>
              <b>{{ report.verdict }}</b>
              <small>{{ reportDrawer.run.name }} · {{ reportDrawer.run.model_label }}</small>
            </div>
            <div class="report-score">
              <span>等价度</span>
              <b>{{ report.score ?? "未启用" }}</b>
            </div>
            <el-button size="small" @click="exportMarkdown">导出 Markdown</el-button>
          </div>

          <el-alert
            v-if="report.gate && !report.gate.ok"
            type="error" show-icon :closable="false"
            title="基线闸门未通过：本次结论不可比"
            class="report-alert"
          >
            <div v-for="r in report.gate.reasons" :key="r">· {{ r }}</div>
            <div v-for="s in report.gate.suggestions" :key="s" class="suggestion">建议：{{ s }}</div>
          </el-alert>
          <el-alert
            v-if="report.red_flags?.length"
            type="warning" show-icon :closable="false"
            title="一票否决项"
            class="report-alert"
          >
            <div v-for="f in report.red_flags" :key="f">· {{ f }}</div>
          </el-alert>

          <div class="stats report-stats">
            <StatCard label="用例总数" :value="report.cases?.length || 0" unit="条" />
            <StatCard label="通过" :value="(report.cases || []).filter((c) => c.verdict === 'pass').length" tone="success" />
            <StatCard label="不通过" :value="(report.cases || []).filter((c) => c.verdict === 'fail').length" tone="danger" />
            <StatCard label="跳过" :value="(report.cases || []).filter((c) => c.verdict === 'skip').length" tone="warning" />
            <StatCard label="不可判断" :value="(report.cases || []).filter((c) => c.verdict === 'inconclusive').length" />
            <StatCard label="基线覆盖率" :value="report.gate?.coverage ?? '—'" unit="%" />
          </div>

          <div class="sub-title section-gap">维度结论</div>
          <el-table :data="report.dimensions || []" size="small" stripe>
            <el-table-column label="维度" width="166">
              <template #default="{ row }"><b>{{ row.dimension }}</b> {{ row.name }}</template>
            </el-table-column>
            <el-table-column label="结论" width="88">
              <template #default="{ row }"><StatusTag :value="row.verdict" /></template>
            </el-table-column>
            <el-table-column label="关键指标" min-width="340">
              <template #default="{ row }">
                <span v-for="(v, k, i) in row.metrics" :key="k" class="metric-chip">
                  {{ k }}={{ v }}<em v-if="i < Object.keys(row.metrics || {}).length - 1">；</em>
                </span>
              </template>
            </el-table-column>
          </el-table>

          <div class="sub-title section-gap">逐条用例（展开查看实际请求与双方原始响应）</div>
          <el-table :data="report.cases || []" size="small" stripe max-height="560">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="case-expand">
                  <div>
                    <div class="sub-title">实际请求体</div>
                    <JsonBlock :value="row.request" :rows="14" />
                  </div>
                  <div>
                    <div class="sub-title">
                      官方基线 · HTTP {{ row.baseline?.status_code ?? "—" }} ·
                      {{ row.baseline?.prompt_tokens ?? 0 }}/{{ row.baseline?.completion_tokens ?? 0 }} token
                      {{ row.baseline?.raw_response_truncated ? " · 响应已截断" : "" }}
                    </div>
                    <JsonBlock :value="formatRaw(row.baseline?.raw_response, row.baseline?.output_text)" :rows="18" />
                    <div v-if="row.baseline?.param_notes?.length" class="param-note">
                      参数说明：{{ row.baseline.param_notes.join("；") }}
                    </div>
                    <div class="sub-title response-gap">
                      候选模型 · HTTP {{ row.candidate?.status_code ?? "—" }} ·
                      {{ row.candidate?.prompt_tokens ?? 0 }}/{{ row.candidate?.completion_tokens ?? 0 }} token
                      {{ row.candidate?.raw_response_truncated ? " · 响应已截断" : "" }}
                    </div>
                    <JsonBlock :value="formatRaw(row.candidate?.raw_response, row.candidate?.error || row.candidate?.output_text)" :rows="18" />
                    <div v-if="row.candidate?.param_notes?.length" class="param-note">
                      参数说明：{{ row.candidate.param_notes.join("；") }}
                    </div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="78">
              <template #default="{ row }"><StatusTag :value="row.verdict" /></template>
            </el-table-column>
            <el-table-column prop="dimension" label="维度" width="62" />
            <el-table-column prop="name" label="用例" min-width="180" show-overflow-tooltip />
            <el-table-column prop="reason" label="判定说明" min-width="260" show-overflow-tooltip />
          </el-table>
        </template>
        <el-empty v-else-if="!reportDrawer.loading" description="报告尚未生成或任务仍在运行" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { display: grid; gap: 12px; }
.panel { background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.step-panel { padding: 10px 12px; }
.two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
.section { padding: 12px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; }
.section-head > div { display: grid; gap: 2px; }
.section-head b { font-size: var(--fs-base); }
.section-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.form-grid :deep(.el-select) { width: 100%; }
.dimension-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.dimension-card {
  display: flex; gap: 8px; padding: 9px 10px; border: 1px solid var(--c-border);
  border-radius: var(--r-md); cursor: pointer; background: var(--c-surface-2);
}
.dimension-card.active { border-color: var(--c-primary); background: #f2f6fc; }
.dim-body { min-width: 0; }
.dim-title { color: var(--c-text-1); font-size: var(--fs-sm); }
.dimension-card p { margin: 3px 0 5px; color: var(--c-text-2); font-size: var(--fs-xs); line-height: 1.45; }
.dim-meta { display: flex; flex-wrap: wrap; gap: 8px; color: var(--c-text-3); font-size: 11px; }
.action-row { display: flex; align-items: center; margin-top: 10px; }
.spacer { flex: 1; }
.muted { color: var(--c-text-3); font-size: var(--fs-xs); }
.progress-box { margin-top: 10px; padding: 9px 10px; border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface-2); }
.progress-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: var(--fs-xs); }
.progress-detail, .error-text { margin-top: 5px; color: var(--c-text-3); font-size: var(--fs-xs); }
.error-text { color: var(--c-danger); }
.inline-alert { padding: 8px 10px; color: var(--c-warning); background: var(--c-warning-bg); border-radius: var(--r-md); font-size: var(--fs-xs); }
.tables { align-items: stretch; }
.verdict.good { color: var(--c-success); font-weight: 600; }
.verdict.warn { color: var(--c-warning); font-weight: 600; }
.verdict.bad { color: var(--c-danger); font-weight: 600; }
.drawer-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 10px; }
.drawer-head > div { display: grid; gap: 3px; }
.drawer-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.case-expand { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 8px 12px 12px; }
.sub-title { margin-bottom: 5px; color: var(--c-text-2); font-size: var(--fs-xs); font-weight: 600; }
.response-gap { margin-top: 12px; }
.param-note { margin-top: 4px; color: var(--c-warning); font-size: var(--fs-xs); }
.report-hero {
  display: flex; align-items: center; gap: 18px; padding: 14px 16px; margin-bottom: 10px;
  border: 1px solid var(--c-border); border-left: 5px solid var(--c-primary); border-radius: var(--r-md);
  background: var(--c-surface-2);
}
.report-hero.等价 { border-left-color: var(--c-success); }
.report-hero.可疑 { border-left-color: var(--c-warning); }
.report-hero.不等价, .report-hero.不可比 { border-left-color: var(--c-danger); }
.report-hero > div:first-child { display: grid; gap: 2px; flex: 1; }
.report-hero b { font-size: 22px; }
.report-hero small, .report-label, .report-score span { color: var(--c-text-3); font-size: var(--fs-xs); }
.report-score { display: grid; text-align: right; }
.report-score b { font-size: 24px; color: var(--c-primary); }
.report-alert { margin-bottom: 8px; }
.suggestion { margin-top: 3px; color: var(--c-text-2); }
.report-stats { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; margin: 10px 0; }
.section-gap { margin-top: 14px; }
.metric-chip { white-space: normal; }
.metric-chip em { font-style: normal; color: var(--c-text-3); }
@media (max-width: 1150px) {
  .two-col { grid-template-columns: 1fr; }
}
</style>
