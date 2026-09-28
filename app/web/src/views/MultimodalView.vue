<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  cancelMultimodalRun, createLocalMedia, createMultimodalRun, createRemoteMedia,
  deleteMedia, getMultimodalRun, listModels, multimodalMedia,
  multimodalRuns,
} from "../api/client";
import JsonBlock from "../components/JsonBlock.vue";
import StatusTag from "../components/StatusTag.vue";

const loading = ref(true);
const models = ref([]);
const mediaList = ref([]);
const runs = ref([]);
const selectedRun = ref(null);
const drawerVisible = ref(false);
const progress = reactive({
  id: null, visible: false, status: "idle", done: 0, total: 1, current: "", error: "",
});
const INLINE_WARN_BYTES = 6 * 1024 * 1024;
const INLINE_MAX_BYTES = 12 * 1024 * 1024;

const mediaForm = reactive({
  source: "local",
  name: "",
  kind: "image",
  mime_type: "",
  data_url: "",
  url: "",
});
const runForm = reactive({
  name: "",
  model_id: null,
  media_id: null,
  input_mode: "auto",
  prompt: "请描述这个媒体的内容。",
  expected_text: "",
  stream: false,
  max_tokens: 512,
  temperature: null,
  timeout: 90,
  extra_params_text: "{}",
  allow_large_inline: false,
});

let pollToken = 0;

const selectedMedia = computed(() => mediaList.value.find((x) => x.id === runForm.media_id) || null);
const selectedInlineBytes = computed(() => selectedMedia.value?.inline_request_bytes || 0);
const selectedWillInline = computed(() =>
  runForm.input_mode === "inline"
  || (runForm.input_mode === "auto" && selectedMedia.value?.source_type === "local"));
const selectedSizeWarning = computed(() =>
  selectedWillInline.value && selectedInlineBytes.value > INLINE_WARN_BYTES);
const inputModeOptions = computed(() => {
  if (selectedMedia.value?.source_type === "local") {
    return [
      { label: "自动（本地转 Base64）", value: "auto" },
      { label: "内联 Base64", value: "inline" },
    ];
  }
  return [
    { label: "自动（远端 URL 直传）", value: "auto" },
    { label: "公网 URL", value: "url" },
    { label: "下载后内联", value: "inline" },
  ];
});

function formatTime(value) {
  return value ? String(value).slice(0, 19).replace("T", " ") : "—";
}

function formatBytes(value) {
  const n = Number(value || 0);
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function previewUrl(row) {
  return row.preview_url || "";
}

async function load() {
  loading.value = true;
  try {
    const [m, media, r] = await Promise.all([
      listModels(), multimodalMedia(), multimodalRuns(),
    ]);
    models.value = m;
    mediaList.value = media;
    runs.value = r;
    if (!runForm.model_id && m.length) runForm.model_id = m[0].id;
    if (!runForm.media_id && media.length) runForm.media_id = media[0].id;
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  [mediaList.value, runs.value] = await Promise.all([multimodalMedia(), multimodalRuns()]);
}

function onFileChange(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  mediaForm.name = mediaForm.name || file.name;
  mediaForm.mime_type = file.type || "";
  mediaForm.kind = file.type?.startsWith("video/") ? "video" : "image";
  const reader = new FileReader();
  reader.onload = () => { mediaForm.data_url = String(reader.result || ""); };
  reader.onerror = () => ElMessage.error("读取本地文件失败");
  reader.readAsDataURL(file);
}

async function addMedia() {
  try {
    const row = mediaForm.source === "local"
      ? await createLocalMedia({
          name: mediaForm.name,
          kind: mediaForm.kind,
          data_url: mediaForm.data_url,
        })
      : await createRemoteMedia({
          name: mediaForm.name,
          kind: mediaForm.kind,
          url: mediaForm.url,
        });
    ElMessage.success("媒体已加入资产");
    Object.assign(mediaForm, {
      source: "local", name: "", kind: "image", mime_type: "", data_url: "", url: "",
    });
    await refresh();
    if (!runForm.media_id && row.id) runForm.media_id = row.id;
  } catch (e) {
    ElMessage.error(e.message);
  }
}

async function removeMedia(row) {
  try {
    await ElMessageBox.confirm(`删除媒体「${row.name}」？`, "删除确认", { type: "warning" });
  } catch { return; }
  await deleteMedia(row.id);
  ElMessage.success("媒体已删除");
  await refresh();
  if (runForm.media_id === row.id) runForm.media_id = mediaList.value[0]?.id || null;
}

async function startRun() {
  if (!runForm.model_id) return ElMessage.warning("请先选择模型");
  if (!runForm.media_id) return ElMessage.warning("请先选择媒体");
  if (selectedWillInline.value && selectedInlineBytes.value > INLINE_MAX_BYTES
      && !runForm.allow_large_inline) {
    return ElMessage.warning("预计 inline 请求体超过 12 MB，请改用公网 URL，或勾选“强制发送”");
  }
  let extraParams = {};
  try {
    extraParams = runForm.extra_params_text.trim()
      ? JSON.parse(runForm.extra_params_text) : {};
    if (!extraParams || Array.isArray(extraParams) || typeof extraParams !== "object") {
      throw new Error("必须是 JSON 对象");
    }
  } catch (e) {
    return ElMessage.warning(`extra_params 不是合法 JSON 对象：${e.message}`);
  }
  const token = ++pollToken;
  Object.assign(progress, {
    id: null, visible: true, status: "running", done: 0, total: 1,
    current: "正在创建任务", error: "",
  });
  try {
    const res = await createMultimodalRun({
      name: runForm.name,
      model_id: runForm.model_id,
      media_id: runForm.media_id,
      input_mode: runForm.input_mode,
      prompt: runForm.prompt,
      expected_keywords: runForm.expected_text
        .split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
      stream: runForm.stream,
      max_tokens: runForm.max_tokens,
      temperature: runForm.temperature,
      timeout: runForm.timeout,
      extra_params: extraParams,
      allow_large_inline: runForm.allow_large_inline,
    });
    progress.id = res.run_id;
    ElMessage.info("多模态探测已开始");
    const run = await waitRun(res.run_id, token);
    if (run?.status === "finished") await openRun(res.run_id);
  } catch (e) {
    Object.assign(progress, { status: "failed", error: e.message });
  }
}

async function waitRun(id, token) {
  const deadline = Date.now() + 10 * 60 * 1000;
  while (token === pollToken && Date.now() < deadline) {
    const run = await getMultimodalRun(id);
    Object.assign(progress, {
      ...(run.progress || {}), id, status: run.status, error: run.error || "",
    });
    if (["finished", "failed", "canceled"].includes(run.status)) {
      await refresh();
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

async function stopRun() {
  if (!progress.id) return;
  try {
    await cancelMultimodalRun(progress.id);
    ElMessage.warning("已请求中止");
  } catch (e) {
    ElMessage.error(e.message);
  }
}

async function openRun(id) {
  selectedRun.value = await getMultimodalRun(id);
  drawerVisible.value = true;
}

onMounted(load);
onBeforeUnmount(() => { pollToken += 1; });
</script>

<template>
  <div v-loading="loading" class="mm-page">
    <div class="panel intro">
      <div>
        <b>多模态可行性测试</b>
        <span>独立于压测：验证图片或视频请求是否被接受、回答是否真正依赖媒体内容。</span>
      </div>
      <el-tag size="small" type="info" effect="plain">图片：image_url · 视频：video_url MVP</el-tag>
    </div>

    <div class="two-col">
      <section class="panel section">
        <div class="section-head">
          <div><b>媒体资产</b><span>{{ mediaList.length }} 个文件 / URL</span></div>
          <span class="muted">本地文件会存入 app/data/mm_media</span>
        </div>

        <el-radio-group v-model="mediaForm.source" size="small">
          <el-radio-button value="local">本地文件</el-radio-button>
          <el-radio-button value="url">公网 URL</el-radio-button>
        </el-radio-group>
        <div class="form-grid media-form">
          <el-form-item label="类型">
            <el-select v-model="mediaForm.kind">
              <el-option label="图片" value="image" />
              <el-option label="视频" value="video" />
            </el-select>
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="mediaForm.name" placeholder="可选" />
          </el-form-item>
        </div>
        <el-form-item v-if="mediaForm.source === 'local'" label="本地文件">
          <input class="file-input" type="file" accept="image/*,video/*" @change="onFileChange" />
          <span v-if="mediaForm.data_url" class="muted">已读取 {{ mediaForm.name }}</span>
        </el-form-item>
        <el-form-item v-else label="公网 URL">
          <el-input v-model="mediaForm.url" placeholder="https://example.com/demo.png" />
        </el-form-item>
        <el-button type="primary" size="small" @click="addMedia">加入媒体资产</el-button>

        <div class="media-list">
          <article v-for="row in mediaList" :key="row.id" class="media-row">
            <div class="media-preview">
              <img v-if="row.kind === 'image'" :src="previewUrl(row)" alt="" />
              <video v-else :src="previewUrl(row)" controls preload="metadata" />
            </div>
            <div class="media-info">
              <b>{{ row.name }}</b>
              <span>{{ row.kind }} · {{ row.source_type }} · {{ row.mime_type || "unknown" }}</span>
              <span v-if="row.size_bytes">原始 {{ formatBytes(row.size_bytes) }}</span>
              <span v-if="row.inline_size_bytes" :class="{ 'size-warn': row.inline_request_bytes > INLINE_WARN_BYTES }">
                Base64 约 {{ formatBytes(row.inline_size_bytes) }} · 请求体约 {{ formatBytes(row.inline_request_bytes) }}
              </span>
              <span v-else>URL 直传，不下载媒体</span>
            </div>
            <el-button link type="danger" size="small" @click="removeMedia(row)">删除</el-button>
          </article>
          <el-empty v-if="!mediaList.length" description="还没有媒体资产" :image-size="64" />
        </div>
      </section>

      <section class="panel section">
        <div class="section-head">
          <div><b>发起探测</b><span>选择资产库里的模型，不需要重复填 URL 和密钥</span></div>
        </div>
        <el-form label-position="top" size="small">
          <div class="form-grid">
            <el-form-item label="模型">
              <el-select v-model="runForm.model_id" filterable placeholder="选择模型">
                <el-option v-for="m in models" :key="m.id" :label="`${m.label} (${m.model})`" :value="m.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="媒体">
              <el-select v-model="runForm.media_id" placeholder="选择媒体">
                <el-option v-for="m in mediaList" :key="m.id" :label="`${m.name} · ${m.kind}`" :value="m.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="输入方式">
              <el-select v-model="runForm.input_mode">
                <el-option v-for="x in inputModeOptions" :key="x.value" :label="x.label" :value="x.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="任务名">
              <el-input v-model="runForm.name" placeholder="可选" />
            </el-form-item>
          </div>
          <el-form-item label="Prompt">
            <el-input v-model="runForm.prompt" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="期望关键词（可选，用逗号或换行分隔）">
            <el-input v-model="runForm.expected_text" placeholder="例如：红色, 3 个, Q7K2M9" />
          </el-form-item>
          <el-alert
            v-if="selectedSizeWarning"
            type="warning" :closable="false" show-icon
            :title="`当前媒体 inline 后预计请求体约 ${formatBytes(selectedInlineBytes)}，可能超过部分供应商限制`"
          />
          <el-checkbox v-if="selectedWillInline" v-model="runForm.allow_large_inline">
            预计超过 12 MB 时仍强制发送（可能触发 HTTP 500 / 413）
          </el-checkbox>
          <div v-if="selectedWillInline && !selectedInlineBytes" class="muted">
            远端文件体积未知，发送前下载完成后再做体积检查。
          </div>
          <div class="form-grid">
            <el-form-item label="max_tokens">
              <el-input-number v-model="runForm.max_tokens" :min="1" :max="65536" />
            </el-form-item>
            <el-form-item label="temperature">
              <el-input-number v-model="runForm.temperature" :min="0" :max="2" :step="0.1" />
            </el-form-item>
          </div>
          <div class="form-grid">
            <el-form-item label="超时（秒）">
              <el-input-number v-model="runForm.timeout" :min="1" :max="600" />
            </el-form-item>
            <el-form-item label="自定义 extra_params（JSON）">
              <el-input v-model="runForm.extra_params_text" type="textarea" :rows="2" placeholder='{"thinking":{"type":"disabled"}}' />
            </el-form-item>
          </div>
          <el-checkbox v-model="runForm.stream">流式响应（记录 TTFT）</el-checkbox>
        </el-form>
        <div class="action-row">
          <span class="muted">仅发起一次请求，不做并发压测、不做自动能力矩阵；视频失败时保留原始响应。</span>
          <span class="spacer" />
          <el-button v-if="progress.status === 'running'" size="small" type="warning" plain @click="stopRun">
            中止
          </el-button>
          <el-button type="primary" size="small" :disabled="progress.status === 'running'" @click="startRun">
            开始探测
          </el-button>
        </div>
        <div v-if="progress.visible" class="progress-box">
          <div class="progress-title">
            <b>探测状态</b><StatusTag :value="progress.status" />
            <span>{{ progress.done }}/{{ progress.total }}</span>
          </div>
          <el-progress :percentage="progress.total ? progress.done / progress.total * 100 : 0" :stroke-width="8" />
          <div v-if="progress.current" class="progress-detail">当前：{{ progress.current }}</div>
          <div v-if="progress.error" class="error-text">{{ progress.error }}</div>
        </div>
      </section>
    </div>

    <section class="panel section">
      <div class="section-head">
        <div><b>历史探测</b><span>{{ runs.length }} 条</span></div>
        <el-button link type="primary" size="small" @click="refresh">刷新</el-button>
      </div>
      <el-table :data="runs" size="small" stripe max-height="320">
        <el-table-column prop="name" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column prop="model_label" label="模型" min-width="120" show-overflow-tooltip />
        <el-table-column label="媒体" width="80">
          <template #default="{ row }">{{ row.media_kind === "video" ? "视频" : "图片" }}</template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }"><StatusTag :value="row.status === 'finished' ? row.verdict : row.status" /></template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.finished_at || row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ row }"><el-button link type="primary" size="small" @click="openRun(row.id)">查看</el-button></template>
        </el-table-column>
        <template #empty><el-empty description="还没有多模态探测记录" :image-size="64" /></template>
      </el-table>
    </section>

    <el-drawer v-model="drawerVisible" title="多模态探测详情" size="860px">
      <template v-if="selectedRun">
        <div class="drawer-head">
          <div>
            <b>{{ selectedRun.name }}</b>
            <span>{{ selectedRun.model_label }} · {{ selectedRun.media_kind }} · {{ selectedRun.input_mode }}</span>
          </div>
          <StatusTag :value="selectedRun.verdict || selectedRun.status" />
        </div>
        <el-alert
          v-if="selectedRun.item?.reason"
          :type="selectedRun.verdict === 'pass' ? 'success' : selectedRun.verdict === 'rejected' ? 'error' : 'warning'"
          :closable="false" show-icon
        >
          {{ selectedRun.item.reason }}
        </el-alert>
        <div v-if="selectedRun.item" class="metrics">
          <span>HTTP <b>{{ selectedRun.item.status_code ?? "—" }}</b></span>
          <span>延迟 <b>{{ selectedRun.item.latency ?? "—" }}s</b></span>
          <span v-if="selectedRun.item.ttft != null">TTFT <b>{{ selectedRun.item.ttft }}s</b></span>
          <span>输入/输出 <b>{{ selectedRun.item.prompt_tokens ?? 0 }} / {{ selectedRun.item.completion_tokens ?? 0 }}</b></span>
          <span>请求体 <b>{{ formatBytes(selectedRun.item.request_size_bytes) }}</b></span>
        </div>
        <div class="case-expand">
          <div>
            <div class="sub-title">实际请求体（Base64 已折叠）</div>
            <JsonBlock :value="selectedRun.item?.request || {}" :rows="18" />
          </div>
          <div>
            <div class="sub-title">
              原始响应
              {{ selectedRun.item?.raw_response_truncated ? "（已截断）" : "" }}
            </div>
            <JsonBlock
              :value="selectedRun.item?.raw_response || selectedRun.item?.error || '（无响应）'"
              :rows="20"
            />
          </div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.mm-page { display: grid; gap: 12px; }
.panel { background: var(--c-surface); border: 1px solid var(--c-border); border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.intro { display: flex; align-items: center; justify-content: space-between; padding: 12px; }
.intro > div { display: grid; gap: 2px; }
.intro span { color: var(--c-text-3); font-size: var(--fs-xs); }
.two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
.section { padding: 12px; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; }
.section-head > div { display: grid; gap: 2px; }
.section-head b { font-size: var(--fs-base); }
.section-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 10px; }
.form-grid :deep(.el-select) { width: 100%; }
.media-form { margin-top: 10px; }
.file-input { display: block; margin-top: 4px; }
.media-list { display: grid; gap: 8px; margin-top: 12px; max-height: 520px; overflow: auto; }
.media-row { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 7px; border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface-2); }
.media-preview { width: 72px; height: 56px; overflow: hidden; border-radius: var(--r-sm); background: #111820; }
.media-preview img, .media-preview video { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-info { display: grid; gap: 2px; min-width: 0; }
.media-info b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs-sm); }
.media-info span { color: var(--c-text-3); font-size: 11px; }
.media-info .size-warn { color: var(--c-warning); font-weight: 600; }
.case-chips { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; margin-bottom: 6px; }
.action-row { display: flex; align-items: center; margin-top: 10px; }
.spacer { flex: 1; }
.progress-box { margin-top: 10px; padding: 9px 10px; border: 1px solid var(--c-border); border-radius: var(--r-md); background: var(--c-surface-2); }
.progress-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: var(--fs-xs); }
.progress-detail, .muted { color: var(--c-text-3); font-size: var(--fs-xs); }
.error-text { color: var(--c-danger); font-size: var(--fs-xs); margin-top: 4px; }
.drawer-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 10px; }
.drawer-head > div { display: grid; gap: 3px; }
.drawer-head span { color: var(--c-text-3); font-size: var(--fs-xs); }
.metrics { display: flex; gap: 18px; flex-wrap: wrap; margin: 10px 0; font-size: var(--fs-xs); }
.metrics span { color: var(--c-text-3); }
.metrics b { color: var(--c-text-1); }
.case-expand { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 10px; }
.sub-title { margin-bottom: 5px; color: var(--c-text-2); font-size: var(--fs-xs); font-weight: 600; }
@media (max-width: 1150px) { .two-col { grid-template-columns: 1fr; } }
</style>
