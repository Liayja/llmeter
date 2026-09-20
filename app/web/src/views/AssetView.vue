<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  listConnections, listModels, createConnection, updateConnection, deleteConnection,
  testConnection, createModel, updateModel, deleteModel, probeModel,
  listPrompts, listPromptFiles, createPrompt, updatePrompt, deletePrompt,
  listScenarios, createScenario, updateScenario, deleteScenario, bulkCreateScenarios,
} from "../api/client";

const tab = ref("conn");
const loading = ref(true);
const connections = ref([]);
const models = ref([]);
const prompts = ref([]);
const promptFiles = ref([]);
const scenarios = ref([]);

/* ── 连接抽屉 ───────────────────────────── */
const connDrawer = reactive({
  visible: false, editing: null, saving: false,
  form: { name: "", base_url: "", endpoint: "", key_mode: "env", env_var: "", api_key: "", note: "" },
});

function openConn(row) {
  connDrawer.editing = row || null;
  connDrawer.form = row
    ? { name: row.name, base_url: row.base_url, endpoint: row.endpoint || "",
        key_mode: row.key_mode, env_var: row.key_mode === "env" ? row.key_ref : "",
        api_key: "", note: row.note || "" }
    : { name: "", base_url: "", endpoint: "", key_mode: "env", env_var: "", api_key: "", note: "" };
  connDrawer.visible = true;
}

const keyPlaceholder = computed(() =>
  connDrawer.editing && connDrawer.editing.key_mode === "local"
    ? `留空 = 保持原密钥（${connDrawer.editing.key_ref || "****"}）`
    : "sk-...");

async function saveConn() {
  const f = connDrawer.form;
  if (!f.name) return ElMessage.warning("请填写连接名称");
  if (!f.base_url && !/^https?:\/\//i.test(f.endpoint)) {
    return ElMessage.warning("请填写 Base URL（完整地址，例如 https://api.deepseek.com）");
  }
  connDrawer.saving = true;
  try {
    if (connDrawer.editing) await updateConnection(connDrawer.editing.id, f);
    else await createConnection(f);
    ElMessage.success(connDrawer.editing ? "连接已更新" : "连接已创建");
    connDrawer.visible = false;
    await load();
  } finally { connDrawer.saving = false; }
}

async function removeConn(row) {
  try {
    await ElMessageBox.confirm(`删除连接「${row.name}」？绑定它的模型会失去连接。`, "删除确认", { type: "warning" });
  } catch { return; }
  await deleteConnection(row.id);
  ElMessage.success("连接已删除");
  await load();
}

async function testConn(row) {
  const r = await testConnection(row.id);
  if (r.ok) ElMessage.success(`连接正常（${r.latency}s）`);
  else ElMessage.error(`失败：${(r.message || "").slice(0, 160)}`);
}

/* ── 模型抽屉 ───────────────────────────── */
const modelDrawer = reactive({
  visible: false, editing: null, saving: false,
  form: { label: "", model: "", connection_id: null, extra_params_text: "", note: "" },
});

function openModel(row) {
  modelDrawer.editing = row || null;
  let extra = "{}";
  if (row?.extra_params) {
    try { extra = JSON.stringify(JSON.parse(row.extra_params), null, 2); } catch (e) { extra = row.extra_params; }
  }
  modelDrawer.form = row
    ? { label: row.label, model: row.model, connection_id: row.connection_id,
        extra_params_text: extra === "{}" ? "" : extra, note: row.note || "" }
    : { label: "", model: "", connection_id: null, extra_params_text: "", note: "" };
  modelDrawer.visible = true;
}

async function saveModel() {
  const f = modelDrawer.form;
  if (!f.label || !f.model) return ElMessage.warning("请填写显示名与模型 ID");
  if (!f.connection_id) return ElMessage.warning("请选择所属连接");
  let extra = null;
  if (f.extra_params_text.trim()) {
    try { extra = JSON.parse(f.extra_params_text); }
    catch { return ElMessage.error("extra_params 不是合法 JSON"); }
  }
  modelDrawer.saving = true;
  try {
    const body = { label: f.label, model: f.model, connection_id: f.connection_id, extra_params: extra, note: f.note };
    if (modelDrawer.editing) await updateModel(modelDrawer.editing.id, body);
    else await createModel(body);
    ElMessage.success(modelDrawer.editing ? "模型已更新" : "模型已创建");
    modelDrawer.visible = false;
    await load();
  } finally { modelDrawer.saving = false; }
}

async function removeModel(row) {
  try { await ElMessageBox.confirm(`删除模型「${row.label}」？`, "删除确认", { type: "warning" }); }
  catch { return; }
  await deleteModel(row.id);
  ElMessage.success("模型已删除");
  await load();
}

/* ── 提示词库 ───────────────────────────── */
const promptDrawer = reactive({
  visible: false, editing: null, saving: false,
  form: { name: "", tags: "", content: "" },
});

function openPrompt(row) {
  promptDrawer.editing = row || null;
  promptDrawer.form = row
    ? { name: row.name, tags: row.tags || "", content: row.content || "" }
    : { name: "", tags: "", content: "" };
  promptDrawer.visible = true;
}

async function savePrompt() {
  const f = promptDrawer.form;
  if (!f.name.trim()) return ElMessage.warning("请填写提示词名称");
  if (!f.content.trim()) return ElMessage.warning("请填写提示词内容");
  promptDrawer.saving = true;
  try {
    const body = { name: f.name.trim(), tags: f.tags.trim(), content: f.content };
    if (promptDrawer.editing) await updatePrompt(promptDrawer.editing.id, body);
    else await createPrompt(body);
    ElMessage.success(promptDrawer.editing ? "提示词已更新" : "提示词已创建");
    promptDrawer.visible = false;
    await load();
  } finally { promptDrawer.saving = false; }
}

async function removePrompt(row) {
  try { await ElMessageBox.confirm(`删除提示词「${row.name}」？`, "删除确认", { type: "warning" }); }
  catch { return; }
  await deletePrompt(row.id);
  ElMessage.success("提示词已删除");
  await load();
}

/* ── 场景模板 / 批量生成 ────────────────── */
const scenarioDrawer = reactive({
  visible: false, editing: null, saving: false,
  form: { name: "", source_kind: "text", prompt_id: null, prompt_file: "", prompt_text: "", overrides_text: "", sort: 0 },
});
const bulkForm = reactive({ group: "输入", files: [] });

function openScenario(row) {
  scenarioDrawer.editing = row || null;
  if (row) {
    let overrides = "{}";
    try { overrides = JSON.stringify(JSON.parse(row.overrides || "{}"), null, 2); }
    catch { overrides = row.overrides || "{}"; }
    scenarioDrawer.form = {
      name: row.name,
      source_kind: row.prompt_file ? "file" : "text",
      prompt_id: null,
      prompt_file: row.prompt_file || "",
      prompt_text: row.prompt || "",
      overrides_text: overrides === "{}" ? "" : overrides,
      sort: row.sort || 0,
    };
  } else {
    const firstPrompt = prompts.value[0];
    scenarioDrawer.form = {
      name: "", source_kind: promptFiles.value.length ? "file" : "text",
      prompt_id: firstPrompt?.id || null, prompt_file: promptFiles.value[0]?.file || "",
      prompt_text: firstPrompt?.content || "", overrides_text: "", sort: 0,
    };
  }
  scenarioDrawer.visible = true;
}

function pickScenarioPrompt() {
  const form = scenarioDrawer.form;
  if (form.source_kind !== "text" || !form.prompt_id) return;
  const prompt = prompts.value.find((x) => x.id === form.prompt_id);
  if (prompt) form.prompt_text = prompt.content || "";
}

async function saveScenario() {
  const f = scenarioDrawer.form;
  if (!f.name.trim()) return ElMessage.warning("请填写场景名称");
  let overrides = {};
  if (f.overrides_text.trim()) {
    try { overrides = JSON.parse(f.overrides_text); }
    catch { return ElMessage.error("覆盖参数不是合法 JSON"); }
  }
  const sourcePrompt = f.source_kind === "text"
    ? prompts.value.find((x) => x.id === f.prompt_id)
    : null;

  // 防呆：场景名里的规模（如 输入-20k）与实际提示词规模差异过大时先确认。
  const hint = /(\d+)\s*k/i.exec(f.name);
  if (hint && f.source_kind === "text" && sourcePrompt?.tokens_est) {
    const expected = Number(hint[1]) * 1000;
    const ratio = sourcePrompt.tokens_est / expected;
    if (ratio < 0.6 || ratio > 1.8) {
      try {
        await ElMessageBox.confirm(
          `场景名约为 ${expected.toLocaleString()} token，但所选提示词约 ${sourcePrompt.tokens_est.toLocaleString()} token。` +
          "名称与内容不符会使压测结果失真，确定继续保存？",
          "规模校验",
          { type: "warning" },
        );
      } catch { return; }
    }
  }

  scenarioDrawer.saving = true;
  try {
    const body = {
      name: f.name.trim(),
      prompt: f.source_kind === "text" ? f.prompt_text : "",
      prompt_file: f.source_kind === "file" ? f.prompt_file : "",
      overrides,
      sort: Number(f.sort) || 0,
    };
    if (scenarioDrawer.editing) await updateScenario(scenarioDrawer.editing.id, body);
    else await createScenario(body);
    ElMessage.success(scenarioDrawer.editing ? "场景已更新" : "场景已创建");
    scenarioDrawer.visible = false;
    await load();
  } finally { scenarioDrawer.saving = false; }
}

async function removeScenario(row) {
  try { await ElMessageBox.confirm(`删除场景「${row.name}」？`, "删除确认", { type: "warning" }); }
  catch { return; }
  await deleteScenario(row.id);
  ElMessage.success("场景已删除");
  await load();
}

async function createBulkScenarios() {
  if (!bulkForm.files.length) return ElMessage.warning("请先勾选要导入的提示词文件");
  const res = await bulkCreateScenarios({ files: bulkForm.files, group: bulkForm.group.trim() });
  ElMessage.success(`已生成 ${res.created.length} 个场景`);
  bulkForm.files = [];
  await load();
}

/* ── 调试抽屉（请求体 + 原始响应）───────── */
const probe = reactive({
  visible: false, running: false, model: null, form: { prompt: "ping", max_tokens: 32, stream: false },
  request: null, response: null,
});

function openProbe(row) {
  probe.model = row;
  probe.request = null;
  probe.response = null;
  probe.visible = true;
}

async function runProbe() {
  probe.running = true;
  try {
    const d = await probeModel(probe.model.id, { ...probe.form, extra_params: null });
    probe.request = d.request;
    probe.response = d.response;
    (d.hints || []).slice(0, 3).forEach((h) => ElMessage({ message: h, type: "info", duration: 5000 }));
  } finally { probe.running = false; }
}

function pretty(obj) {
  if (obj == null) return "";
  const text = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

const connName = (id) => connections.value.find((c) => c.id === id)?.name || "-";

async function load() {
  loading.value = true;
  try {
    [
      connections.value,
      models.value,
      prompts.value,
      promptFiles.value,
      scenarios.value,
    ] = await Promise.all([
      listConnections(), listModels(), listPrompts(), listPromptFiles(), listScenarios(),
    ]);
  } finally { loading.value = false; }
}

onMounted(load);
</script>

<template>
  <el-card shadow="never" class="panel">
    <el-tabs v-model="tab">
      <el-tab-pane label="连接" name="conn">
        <div class="toolbar">
          <span class="muted">共 {{ connections.length }} 条连接</span>
          <div class="spacer" />
          <el-button size="small" @click="load">刷新</el-button>
          <el-button size="small" type="primary" @click="openConn(null)">新增连接</el-button>
        </div>
        <el-table :data="connections" v-loading="loading" size="small" stripe height="420">
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="base_url" label="Base URL" min-width="240" show-overflow-tooltip />
          <el-table-column label="接口路径" min-width="150" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.endpoint || "（默认 /v1/chat/completions）" }}</span></template>
          </el-table-column>
          <el-table-column label="密钥" min-width="170">
            <template #default="{ row }">
              <el-tag size="small" :type="row.key_mode === 'env' ? 'success' : 'warning'" effect="light">
                {{ row.key_mode === "env" ? "环境变量" : "本机加密" }}
              </el-tag>
              <span class="mono muted"> {{ row.key_ref }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openConn(row)">编辑</el-button>
              <el-button link size="small" @click="testConn(row)">测试</el-button>
              <el-button link type="danger" size="small" @click="removeConn(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有连接，点右上角「新增连接」添加（需要 Base URL 与密钥）" />
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="模型" name="model">
        <div class="toolbar">
          <span class="muted">共 {{ models.length }} 个模型</span>
          <div class="spacer" />
          <el-button size="small" @click="load">刷新</el-button>
          <el-button size="small" type="primary" @click="openModel(null)">新增模型</el-button>
        </div>
        <el-table :data="models" v-loading="loading" size="small" stripe height="420">
          <el-table-column prop="label" label="显示名" min-width="160" />
          <el-table-column prop="model" label="模型 ID" min-width="160" />
          <el-table-column label="所属连接" min-width="140">
            <template #default="{ row }">{{ connName(row.connection_id) }}</template>
          </el-table-column>
          <el-table-column prop="extra_params" label="extra_params" min-width="200" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.extra_params }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="180" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openModel(row)">编辑</el-button>
              <el-button link size="small" @click="openProbe(row)">调试</el-button>
              <el-button link type="danger" size="small" @click="removeModel(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有模型，点右上角「新增模型」添加（需先有连接）" />
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="提示词" name="prompt">
        <div class="toolbar">
          <span class="muted">共 {{ prompts.length }} 条提示词</span>
          <div class="spacer" />
          <el-button size="small" @click="load">刷新</el-button>
          <el-button size="small" type="primary" @click="openPrompt(null)">新增提示词</el-button>
        </div>
        <el-table :data="prompts" v-loading="loading" size="small" stripe height="420">
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column prop="tags" label="标签" min-width="130" />
          <el-table-column label="字符数" width="100">
            <template #default="{ row }">{{ (row.chars || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column label="预估 token" width="110">
            <template #default="{ row }">{{ (row.tokens_est || 0).toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="content" label="内容预览" min-width="260" show-overflow-tooltip />
          <el-table-column label="操作" width="122" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openPrompt(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="removePrompt(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有提示词，点右上角「新增提示词」添加" />
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="场景模板" name="scenario">
        <div class="bulk-box">
          <span class="muted">从 prompts 目录批量生成输入梯度：</span>
          <el-input v-model="bulkForm.group" size="small" placeholder="场景名前缀，例如 输入" style="width: 150px" />
          <el-select v-model="bulkForm.files" multiple collapse-tags size="small" placeholder="选择 .txt 文件" style="min-width: 320px">
            <el-option
              v-for="f in promptFiles" :key="f.file"
              :label="`${f.name} · ≈${f.tokens_est.toLocaleString()} token`"
              :value="f.file"
            />
          </el-select>
          <el-button size="small" :disabled="!bulkForm.files.length" @click="createBulkScenarios">批量生成</el-button>
        </div>
        <div class="toolbar">
          <span class="muted">共 {{ scenarios.length }} 个场景</span>
          <div class="spacer" />
          <el-button size="small" @click="load">刷新</el-button>
          <el-button size="small" type="primary" @click="openScenario(null)">新增场景</el-button>
        </div>
        <el-table :data="scenarios" v-loading="loading" size="small" stripe height="360">
          <el-table-column prop="name" label="场景" min-width="150" />
          <el-table-column prop="prompt_source" label="提示词来源" min-width="190" show-overflow-tooltip />
          <el-table-column label="规模" width="130">
            <template #default="{ row }">
              ≈{{ (row.prompt_tokens_est || 0).toLocaleString() }} token
              <span v-if="row.prompt_chars" class="muted"> / {{ row.prompt_chars.toLocaleString() }} 字符</span>
            </template>
          </el-table-column>
          <el-table-column prop="overrides" label="覆盖参数" min-width="190" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.overrides }}</span></template>
          </el-table-column>
          <el-table-column label="操作" width="122" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openScenario(row)">编辑</el-button>
              <el-button link type="danger" size="small" @click="removeScenario(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="还没有场景模板；可先添加提示词，再批量生成输入梯度" />
          </template>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-card>

  <el-drawer v-model="connDrawer.visible" :title="connDrawer.editing ? '编辑连接' : '新增连接'" size="520px">
    <el-form label-width="110px" label-position="left" size="small">
      <el-form-item label="名称" required>
        <el-input v-model="connDrawer.form.name" placeholder="例如 智谱官方" />
      </el-form-item>
      <el-form-item label="Base URL" required>
        <el-input v-model="connDrawer.form.base_url" placeholder="https://open.bigmodel.cn/api/paas" />
      </el-form-item>
      <el-form-item label="接口路径">
        <el-input v-model="connDrawer.form.endpoint" placeholder="只填路径，如 /chat/completions；留空用 /v1/chat/completions" />
      </el-form-item>
      <el-form-item label="密钥方式">
        <el-radio-group v-model="connDrawer.form.key_mode">
          <el-radio-button value="env">环境变量</el-radio-button>
          <el-radio-button value="local">本机加密</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="connDrawer.form.key_mode === 'env'" label="环境变量名">
        <el-input v-model="connDrawer.form.env_var" placeholder="LLM_API_KEY" />
      </el-form-item>
      <el-form-item v-else label="API Key">
        <el-input v-model="connDrawer.form.api_key" type="password" show-password :placeholder="keyPlaceholder" />
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="connDrawer.form.note" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button size="small" @click="connDrawer.visible = false">取消</el-button>
      <el-button size="small" type="primary" :loading="connDrawer.saving" @click="saveConn">保存</el-button>
    </template>
  </el-drawer>

  <el-drawer v-model="modelDrawer.visible" :title="modelDrawer.editing ? '编辑模型' : '新增模型'" size="520px">
    <el-form label-width="110px" label-position="left" size="small">
      <el-form-item label="显示名" required>
        <el-input v-model="modelDrawer.form.label" placeholder="例如 智谱官方GLM-5.3" />
      </el-form-item>
      <el-form-item label="模型 ID" required>
        <el-input v-model="modelDrawer.form.model" placeholder="例如 glm-5.3" />
      </el-form-item>
      <el-form-item label="所属连接" required>
        <el-select v-model="modelDrawer.form.connection_id" placeholder="选择连接" style="width: 100%">
          <el-option v-for="c in connections" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="extra_params">
        <el-input v-model="modelDrawer.form.extra_params_text" type="textarea" :rows="5"
                  placeholder='{"thinking":{"type":"disabled"}}' />
        <div class="muted">会覆盖压测页的同名参数；数值要写数字，不要写成字符串</div>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="modelDrawer.form.note" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button size="small" @click="modelDrawer.visible = false">取消</el-button>
      <el-button size="small" type="primary" :loading="modelDrawer.saving" @click="saveModel">保存</el-button>
    </template>
  </el-drawer>

  <el-drawer v-model="promptDrawer.visible" :title="promptDrawer.editing ? '编辑提示词' : '新增提示词'" size="620px">
    <el-form label-position="top" size="small">
      <div class="form-row">
        <el-form-item label="名称" required><el-input v-model="promptDrawer.form.name" placeholder="例如 输入-3k" /></el-form-item>
        <el-form-item label="标签"><el-input v-model="promptDrawer.form.tags" placeholder="输入/业务/翻译" /></el-form-item>
      </div>
      <el-form-item label="提示词内容" required>
        <el-input v-model="promptDrawer.form.content" type="textarea" :rows="16" placeholder="粘贴提示词内容" />
      </el-form-item>
      <div class="muted">提示词用于生成场景，内容长度会直接影响输入 token 规模。</div>
    </el-form>
    <template #footer>
      <el-button size="small" @click="promptDrawer.visible = false">取消</el-button>
      <el-button size="small" type="primary" :loading="promptDrawer.saving" @click="savePrompt">保存</el-button>
    </template>
  </el-drawer>

  <el-drawer v-model="scenarioDrawer.visible" :title="scenarioDrawer.editing ? '编辑场景' : '新增场景'" size="680px">
    <el-form label-position="top" size="small">
      <div class="form-row">
        <el-form-item label="场景名" required><el-input v-model="scenarioDrawer.form.name" placeholder="例如 输入-3k" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="scenarioDrawer.form.sort" :min="0" :max="9999" controls-position="right" /></el-form-item>
      </div>
      <el-form-item label="提示词来源">
        <el-radio-group v-model="scenarioDrawer.form.source_kind">
          <el-radio-button value="text">提示词库 / 文本</el-radio-button>
          <el-radio-button value="file">prompts 文件</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <template v-if="scenarioDrawer.form.source_kind === 'text'">
        <el-form-item label="从提示词库选择">
          <el-select v-model="scenarioDrawer.form.prompt_id" clearable placeholder="可选，选择后自动填入内容" @change="pickScenarioPrompt">
            <el-option v-for="p in prompts" :key="p.id" :label="`${p.name} · ≈${(p.tokens_est || 0).toLocaleString()} token`" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="实际发送内容">
          <el-input v-model="scenarioDrawer.form.prompt_text" type="textarea" :rows="10" placeholder="可直接填写文本；选择提示词库后此处会自动填入" />
        </el-form-item>
      </template>
      <el-form-item v-else label="prompts 文件">
        <el-select v-model="scenarioDrawer.form.prompt_file" filterable placeholder="选择项目 prompts 目录下的 txt 文件">
          <el-option v-for="f in promptFiles" :key="f.file" :label="`${f.name} · ≈${f.tokens_est.toLocaleString()} token`" :value="f.file" />
        </el-select>
      </el-form-item>
      <el-form-item label="覆盖参数 overrides">
        <el-input v-model="scenarioDrawer.form.overrides_text" type="textarea" :rows="3" placeholder='{"max_tokens":512,"timeout":90}' />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button size="small" @click="scenarioDrawer.visible = false">取消</el-button>
      <el-button size="small" type="primary" :loading="scenarioDrawer.saving" @click="saveScenario">保存</el-button>
    </template>
  </el-drawer>

  <el-drawer v-model="probe.visible" :title="`调试 · ${probe.model?.label || ''}`" size="720px">
    <el-form label-width="90px" label-position="left" size="small">
      <el-form-item label="Prompt"><el-input v-model="probe.form.prompt" type="textarea" :rows="2" /></el-form-item>
      <el-form-item label="max_tokens"><el-input-number v-model="probe.form.max_tokens" :min="1" :max="65536" /></el-form-item>
      <el-form-item label="流式"><el-switch v-model="probe.form.stream" /></el-form-item>
      <el-form-item>
        <el-button size="small" type="primary" :loading="probe.running" @click="runProbe">发送测试请求</el-button>
      </el-form-item>
    </el-form>
    <div v-if="probe.request">
      <div class="sect">实际请求体</div>
      <pre class="code">{{ pretty(probe.request.body) }}</pre>
      <div class="sect">实际响应（原始）</div>
      <div class="muted">HTTP {{ probe.response?.status_code ?? "—" }} · {{ probe.response?.elapsed ?? "—" }}s</div>
      <pre class="code">{{ pretty(probe.response?.raw || probe.response) }}</pre>
    </div>
  </el-drawer>
</template>

<style scoped>
.panel { border-radius: var(--r-lg); box-shadow: var(--sh-card); }
.toolbar { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-2); }
.spacer { flex: 1; }
.muted { color: var(--c-text-3); font-size: var(--fs-xs); }
.sect { font-size: var(--fs-xs); color: var(--c-text-2); margin: var(--sp-3) 0 var(--sp-1); }
.bulk-box {
  display: flex; align-items: center; flex-wrap: wrap; gap: var(--sp-2);
  padding: 8px 10px; margin-bottom: 8px; border: 1px solid var(--c-border);
  border-radius: var(--r-md); background: var(--c-surface-2);
}
.form-row { display: grid; grid-template-columns: minmax(0, 1fr) 150px; gap: 10px; }
.form-row :deep(.el-input-number) { width: 100%; }
:deep(.el-drawer .el-select) { width: 100%; }
</style>
