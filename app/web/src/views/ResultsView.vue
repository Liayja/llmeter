<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import {
  getResult, listResults, resultDownloadUrl,
} from "../api/client";
import JsonBlock from "../components/JsonBlock.vue";
import StatCard from "../components/StatCard.vue";
import StatusTag from "../components/StatusTag.vue";

const loading = ref(true);
const detailLoading = ref(false);
const results = ref([]);
const selectedId = ref(null);
const detail = ref(null);
const keyword = ref("");
const statusFilter = ref("all");
const rowDrawer = reactive({ visible: false, row: null });

const filtered = computed(() => results.value.filter((r) => {
  const statusOk = statusFilter.value === "all" || r.status === statusFilter.value;
  const text = `${r.id} ${r.name} ${r.status}`.toLowerCase();
  return statusOk && (!keyword.value || text.includes(keyword.value.toLowerCase()));
}));

const rows = computed(() => detail.value?.rows || []);
const summary = computed(() => detail.value?.summary || {});
const hasComparison = computed(() => rows.value.length > 0);
const modelCount = computed(() => new Set(rows.value.map((r) => r.model_label || r._model || "—")).size);
const scenarioCount = computed(() => new Set(rows.value.map((r) => r.name)).size);
const selectedTask = computed(() => detail.value?.task || results.value.find((r) => r.id === selectedId.value));

function fmt(value, digits = 2, fallback = "—") {
  return value == null || Number.isNaN(Number(value)) ? fallback : Number(value).toFixed(digits);
}

function time(value) {
  return value ? String(value).slice(0, 19).replace("T", " ") : "—";
}

function successTone(v) {
  if (v == null) return "default";
  if (v >= 99) return "success";
  if (v >= 90) return "warning";
  return "danger";
}

async function load() {
  loading.value = true;
  try {
    results.value = await listResults();
    if (!selectedId.value && results.value.length) await selectResult(results.value[0].id);
  } finally {
    loading.value = false;
  }
}

async function selectResult(id) {
  selectedId.value = id;
  detailLoading.value = true;
  try {
    detail.value = await getResult(id);
  } finally {
    detailLoading.value = false;
  }
}

function openRow(row) {
  rowDrawer.row = row;
  rowDrawer.visible = true;
}

onMounted(load);
</script>

<template>
  <div v-loading="loading" class="page">
    <aside class="panel result-list">
      <div class="list-head">
        <b>历史结果</b>
        <el-button link type="primary" size="small" @click="load">刷新</el-button>
      </div>
      <div class="filters">
        <el-input v-model="keyword" size="small" clearable placeholder="搜索任务名 / ID" />
        <el-select v-model="statusFilter" size="small">
          <el-option label="全部状态" value="all" />
          <el-option label="成功" value="success" />
          <el-option label="已中止" value="canceled" />
          <el-option label="失败" value="failed" />
        </el-select>
      </div>
      <div class="result-items">
        <button
          v-for="r in filtered" :key="r.id"
          class="result-item" :class="{ active: selectedId === r.id }"
          @click="selectResult(r.id)"
        >
          <div class="result-item-top">
            <span class="task-name">{{ r.name }}</span>
            <StatusTag :value="r.status" />
          </div>
          <div class="result-item-meta">
            <span>#{{ r.id }}</span>
            <span>{{ r.success }}/{{ r.total }}</span>
            <span>{{ time(r.created_at) }}</span>
          </div>
        </button>
        <el-empty v-if="!filtered.length" :image-size="70" description="没有匹配的结果" />
      </div>
    </aside>

    <main v-loading="detailLoading" class="panel report">
      <template v-if="detail && selectedTask">
        <div class="report-head">
          <div>
            <div class="report-title">
              <b>{{ selectedTask.name }}</b>
              <StatusTag :value="selectedTask.status" />
            </div>
            <div class="report-meta">
              任务 #{{ selectedTask.id }} · {{ time(selectedTask.created_at) }} ·
              成功 {{ selectedTask.success }}/{{ selectedTask.total }}
            </div>
          </div>
          <div class="downloads">
            <a :href="resultDownloadUrl(selectedTask.id, 'json')" download>
              <el-button size="small">下载 JSON</el-button>
            </a>
            <a :href="resultDownloadUrl(selectedTask.id, 'xlsx')" download>
              <el-button size="small" type="primary" plain>下载 Excel</el-button>
            </a>
          </div>
        </div>

        <template v-if="hasComparison">
          <div class="compare-note">
            <b>对比范围：</b>
            {{ modelCount }} 个模型 × {{ scenarioCount }} 个场景，共 {{ rows.length }} 组结果。
            数值单位为秒、req/s、token 或百分比，表格中已明确标注。
          </div>
          <div class="stats">
            <StatCard label="平均成功率" :value="fmt(rows.reduce((n, r) => n + (r.success_rate || 0), 0) / rows.length, 1)" unit="%" tone="success" />
            <StatCard label="平均 QPS" :value="fmt(rows.reduce((n, r) => n + (r.qps || 0), 0) / rows.length, 2)" unit="req/s" tone="primary" />
            <StatCard label="平均 TTFT" :value="fmt(rows.reduce((n, r) => n + (r.ttft_avg || 0), 0) / rows.length, 3)" unit="s" />
            <StatCard label="平均 P50" :value="fmt(rows.reduce((n, r) => n + (r.latency_p50 || 0), 0) / rows.length, 3)" unit="s" />
            <StatCard label="平均缓存命中" :value="fmt(rows.reduce((n, r) => n + (r.cache_hit_rate || 0), 0) / rows.length, 1)" unit="%" tone="warning" />
          </div>
          <el-table :data="rows" size="small" stripe height="calc(100vh - 285px)">
            <el-table-column v-if="modelCount > 1" prop="model_label" label="模型" min-width="132" fixed show-overflow-tooltip />
            <el-table-column prop="name" label="场景" min-width="132" fixed show-overflow-tooltip />
            <el-table-column label="成功率" width="86" sortable :sort-method="(a, b) => (a.success_rate || 0) - (b.success_rate || 0)">
              <template #default="{ row }">
                <span :class="`tone-${successTone(row.success_rate)}`">{{ fmt(row.success_rate, 1) }}%</span>
              </template>
            </el-table-column>
            <el-table-column label="QPS" width="86" sortable :sort-method="(a, b) => (a.qps || 0) - (b.qps || 0)">
              <template #default="{ row }">{{ fmt(row.qps) }} <small>req/s</small></template>
            </el-table-column>
            <el-table-column label="TTFT 均值" width="102" sortable :sort-method="(a, b) => (a.ttft_avg || 0) - (b.ttft_avg || 0)">
              <template #default="{ row }">{{ fmt(row.ttft_avg, 3) }} <small>s</small></template>
            </el-table-column>
            <el-table-column label="延迟 P50" width="102" sortable :sort-method="(a, b) => (a.latency_p50 || 0) - (b.latency_p50 || 0)">
              <template #default="{ row }">{{ fmt(row.latency_p50, 3) }} <small>s</small></template>
            </el-table-column>
            <el-table-column label="延迟 P90" width="102">
              <template #default="{ row }">{{ fmt(row.latency_p90, 3) }} <small>s</small></template>
            </el-table-column>
            <el-table-column label="延迟 P99" width="102">
              <template #default="{ row }">{{ fmt(row.latency_p99, 3) }} <small>s</small></template>
            </el-table-column>
            <el-table-column label="缓存命中" width="96">
              <template #default="{ row }">{{ fmt(row.cache_hit_rate, 1) }} <small>%</small></template>
            </el-table-column>
            <el-table-column label="目标输入 token" width="116">
              <template #default="{ row }">{{ fmt(row.target_input_tokens, 0, "—") }}</template>
            </el-table-column>
            <el-table-column label="实际输入 token" width="116">
              <template #default="{ row }">{{ fmt(row.avg_input_tokens, 0, "—") }}</template>
            </el-table-column>
            <el-table-column label="输入偏差" width="92">
              <template #default="{ row }">
                <span :class="Math.abs(row.input_token_deviation_pct || 0) > 10 ? 'tone-warning' : ''">
                  {{ row.input_token_deviation_pct == null ? "—" : `${row.input_token_deviation_pct > 0 ? "+" : ""}${fmt(row.input_token_deviation_pct, 1)}%` }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="输出 token/请求" width="122">
              <template #default="{ row }">{{ fmt(row.avg_output_tokens, 0, "—") }}</template>
            </el-table-column>
            <el-table-column label="重试" width="64">
              <template #default="{ row }">{{ row.retried ?? 0 }}</template>
            </el-table-column>
            <el-table-column label="操作" width="76" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openRow(row)">原始数据</el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>

        <template v-else>
          <div class="single-summary">
            <StatCard label="成功率" :value="fmt(summary.success_rate, 1)" unit="%" :tone="successTone(summary.success_rate)" />
            <StatCard label="QPS" :value="fmt(summary.qps, 2)" unit="req/s" tone="primary" />
            <StatCard label="TTFT 均值" :value="fmt(summary.ttft_avg, 3)" unit="s" />
            <StatCard label="TTFT P50" :value="fmt(summary.ttft_p50, 3)" unit="s" />
            <StatCard label="延迟 P50" :value="fmt(summary.latency_p50, 3)" unit="s" />
            <StatCard label="延迟 P90" :value="fmt(summary.latency_p90, 3)" unit="s" />
            <StatCard label="延迟 P99" :value="fmt(summary.latency_p99, 3)" unit="s" />
            <StatCard label="缓存命中率" :value="fmt(summary.cache_hit_rate, 1)" unit="%" tone="warning" />
            <StatCard label="输入 token/请求" :value="fmt(summary.avg_input_tokens, 0)" />
            <StatCard label="输出 token/请求" :value="fmt(summary.avg_output_tokens, 0)" />
            <StatCard label="重试次数" :value="summary.retried ?? 0" unit="次" />
            <StatCard label="并发峰值" :value="summary.concurrency_peak ?? summary.concurrency ?? '—'" />
          </div>
          <div class="raw-summary">
            <div class="sub-title">完整指标（原始 JSON）</div>
            <JsonBlock :value="summary" :rows="24" />
          </div>
        </template>
      </template>
      <el-empty v-else-if="!detailLoading" description="从左侧选择一个结果查看报告" />
    </main>

    <el-drawer v-model="rowDrawer.visible" title="单组结果原始数据" size="720px">
      <JsonBlock :value="rowDrawer.row" :rows="28" />
    </el-drawer>
  </div>
</template>

<style scoped>
.page {
  display: grid; grid-template-columns: 286px minmax(0, 1fr); gap: 12px;
  height: calc(100vh - 76px); min-height: 520px;
}
.panel { background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.result-list { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.list-head, .report-head { display: flex; align-items: center; justify-content: space-between; }
.list-head { padding: 10px 12px; border-bottom: 1px solid var(--c-border); }
.filters { display: grid; grid-template-columns: 1fr 112px; gap: 6px; padding: 8px; border-bottom: 1px solid var(--c-border); }
.result-items { min-height: 0; overflow: auto; padding: 6px; }
.result-item {
  display: block; width: 100%; padding: 8px 9px; margin-bottom: 5px; text-align: left;
  border: 1px solid transparent; border-radius: var(--r-md); background: transparent; cursor: pointer;
}
.result-item:hover { background: var(--c-surface-2); }
.result-item.active { border-color: #b8c9e5; background: #f2f6fc; }
.result-item-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.task-name { min-width: 0; overflow: hidden; color: var(--c-text-1); font-size: var(--fs-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.result-item-meta { display: flex; justify-content: space-between; gap: 6px; margin-top: 6px; color: var(--c-text-3); font-size: 11px; }
.report { min-width: 0; overflow: hidden; padding: 12px; }
.report-head { min-height: 46px; margin-bottom: 10px; }
.report-title { display: flex; align-items: center; gap: 8px; }
.report-title b { font-size: var(--fs-lg); }
.report-meta { margin-top: 3px; color: var(--c-text-3); font-size: var(--fs-xs); }
.downloads { display: flex; gap: 6px; }
.downloads a { text-decoration: none; }
.compare-note { padding: 8px 10px; margin-bottom: 10px; color: var(--c-text-2); background: var(--c-surface-2); border-left: 3px solid var(--c-primary); font-size: var(--fs-xs); }
.stats, .single-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: 8px; margin-bottom: 10px; }
.tone-success { color: var(--c-success); font-weight: 600; }
.tone-warning { color: var(--c-warning); font-weight: 600; }
.tone-danger { color: var(--c-danger); font-weight: 600; }
small { color: var(--c-text-3); }
.raw-summary { max-height: calc(100vh - 285px); overflow: auto; }
.sub-title { margin-bottom: 5px; color: var(--c-text-2); font-size: var(--fs-xs); font-weight: 600; }
@media (max-width: 1000px) {
  .page { grid-template-columns: 1fr; height: auto; }
  .result-list { height: 300px; }
  .report { min-height: 600px; }
}
</style>
