/* llmeter 压测平台 · 原生 JS 单页（无构建） */
const $ = (sel) => document.querySelector(sel);
const api = async (path, opts = {}) => {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
};

let currentTaskId = null;
let eventSource = null;
let modelsCache = [];
let promptsCache = [];
let connCache = [];
let editingConnectionId = null;
let editingModelId = null;

/* ── 页签 ───────────────────────────────── */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".panel").forEach((p) =>
      p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === "results") loadResults();
    if (btn.dataset.tab === "parity") {
      // 打开页签时重新拉一次模型/维度/基线，避免列表为空或数据过期
      loadModels().then(() => { loadParityBaselines(); loadParityRuns(); });
      if (!parityDims.length) loadParityDimensions();
    }
  };
});

/* ── 健康检查 ──────────────────────────── */
api("/api/health").then((h) => {
  $("#health").textContent = h.warning ? `⚠ ${h.warning}` : "本机服务正常";
}).catch(() => { $("#health").textContent = "服务不可用"; });

/* ── 连接 ─────────────────────────────── */
$("#conn-mode").onchange = () => {
  const env = $("#conn-mode").value === "env";
  $("#wrap-env").hidden = !env;
  $("#wrap-key").hidden = env;
};

async function loadConnections() {
  const rows = await api("/api/connections");
  connCache = rows;
  const tbody = $("#conn-table tbody");
  tbody.innerHTML = `<tr><th>名称</th><th>Base URL</th><th>密钥</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.name}</td>
      <td>${r.base_url}</td>
      <td><span class="tag ${r.key_mode}">${r.key_mode === "env" ? "环境变量" : "本机加密"}</span> ${r.key_ref || ""}</td>
      <td>
        <button onclick="editConnection(${r.id})">编辑</button>
        <button onclick="testConnection(${r.id})">测试</button>
        <button onclick="deleteConnection(${r.id})">删除</button>
      </td>
    </tr>`).join("");
  const sel = $("#model-conn");
  sel.innerHTML = rows.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
}

$("#btn-save-conn").onclick = async () => {
  const payload = {
    name: $("#conn-name").value.trim(),
    base_url: $("#conn-url").value.trim(),
    endpoint: $("#conn-endpoint").value.trim(),
    key_mode: $("#conn-mode").value,
    env_var: $("#conn-env").value.trim(),
    api_key: $("#conn-key").value.trim(),
  };
  // 容错：Base URL 空但接口路径填了完整地址 → 自动纠正为 Base URL
  if (!payload.base_url && /^https?:\/\//i.test(payload.endpoint)) {
    payload.base_url = payload.endpoint;
    payload.endpoint = "";
    $("#conn-url").value = payload.base_url;
    $("#conn-endpoint").value = "";
  }
  if (!payload.base_url) return alert("请填写 Base URL（完整地址，例如 https://api.deepseek.com）");
  try {
    if (editingConnectionId) {
      await api(`/api/connections/${editingConnectionId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/connections", { method: "POST", body: JSON.stringify(payload) });
    }
    const wasEditing = !!editingConnectionId;
    resetConnectionForm();
    await loadConnections();
    await loadModels();
    alert(wasEditing ? "连接已更新" : "连接已保存");
  } catch (e) { alert(`保存失败: ${e.message}`); }
};

function resetConnectionForm() {
  editingConnectionId = null;
  $("#conn-name").value = $("#conn-url").value = $("#conn-endpoint").value = "";
  $("#conn-env").value = $("#conn-key").value = "";
  $("#conn-key").placeholder = "sk-...";
  $("#btn-save-conn").textContent = "保存连接";
  $("#btn-cancel-conn").hidden = true;
}

window.editConnection = (id) => {
  const c = connCache.find((x) => x.id === id);
  if (!c) return;
  editingConnectionId = id;
  $("#conn-name").value = c.name || "";
  $("#conn-url").value = c.base_url || "";
  $("#conn-endpoint").value = c.endpoint || "";
  $("#conn-mode").value = c.key_mode || "env";
  if ((c.key_mode || "env") === "env") {
    $("#conn-env").value = c.key_ref || "";
    $("#conn-key").value = "";
    $("#conn-key").placeholder = "当前为环境变量模式，无需填写";
  } else {
    $("#conn-env").value = "";
    $("#conn-key").value = "";
    $("#conn-key").placeholder = `留空 = 保持原密钥（${c.key_ref || "****"}）`;
  }
  if (typeof $("#conn-mode").onchange === "function") $("#conn-mode").onchange();
  $("#btn-save-conn").textContent = "保存修改";
  $("#btn-cancel-conn").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
};

$("#btn-cancel-conn").onclick = resetConnectionForm;

window.deleteConnection = async (id) => {
  if (!confirm("删除该连接？绑定它的模型会失去连接。")) return;
  await api(`/api/connections/${id}`, { method: "DELETE" });
  await loadConnections();
  await loadModels();
};

window.testConnection = async (id) => {
  const r = await api(`/api/connections/${id}/test`, { method: "POST" });
  alert(r.ok ? `连接正常，耗时 ${r.latency}s` : `失败: ${r.message}`);
};

/* ── 模型 ─────────────────────────────── */
async function loadModels() {
  const rows = await api("/api/models");
  modelsCache = rows;
  const tbody = $("#model-table tbody");
    tbody.innerHTML = `<tr><th>显示名</th><th>模型</th><th>连接</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.label}</td><td>${r.model}</td><td>${r.connection_id ?? "-"}</td>
      <td><button onclick="editModel(${r.id})">编辑</button>
          <button onclick="window.openProbe(${r.id})">调试</button>
          <button onclick="deleteModel(${r.id})">删除</button></td>
    </tr>`).join("");
  $("#probe-model").innerHTML = rows.map((r) => `<option value="${r.id}">${r.label} (${r.model})</option>`).join("");
  const sel = $("#run-model");
  sel.innerHTML = rows.map((r) => `<option value="${r.id}">${r.label} (${r.model})</option>`).join("");
  const pSel = $("#pb-model");
  const rSel = $("#pr-model");
  const options = rows.length
    ? rows.map((r) => `<option value="${r.id}">${r.label} (${r.model})</option>`).join("")
    : `<option value="">⚠️ 暂无模型，请先到「资产库」添加</option>`;
  if (pSel) pSel.innerHTML = options;
  if (rSel) rSel.innerHTML = options;
  const hint = rows.length
    ? `共 ${rows.length} 个模型，可下拉选择；没有想要的模型请到「资产库」新增`
    : "还没有配置模型：请到「资产库」→ 模型 里先添加（需要先有连接）";
  if ($("#pb-model-hint")) $("#pb-model-hint").textContent = hint;
  if ($("#pr-model-hint")) $("#pr-model-hint").textContent = hint;
  $("#run-models").innerHTML = rows.length
    ? rows.map((r) => `<label><input type="checkbox" class="run-model-ck" value="${r.id}" /> ${r.label} (${r.model})</label>`).join("")
    : "<span class='muted'>请先在左侧添加模型</span>";
}

/* ── 提示词库 / 场景模板 ─────────────── */
async function loadPrompts() {
  const rows = await api("/api/prompts");
  promptsCache = rows;
  $("#prompt-table tbody").innerHTML = `<tr><th>名称</th><th>标签</th><th>长度</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.name}</td><td>${r.tags || ""}</td><td>${(r.content || "").length}</td>
      <td><button onclick="deletePrompt(${r.id})">删除</button></td>
    </tr>`).join("");
  $("#sc-prompt-source").innerHTML = rows.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
  renderScPromptInfo();
}

function renderScPromptInfo() {
  const id = Number($("#sc-prompt-source").value);
  const p = promptsCache.find((x) => x.id === id);
  $("#sc-prompt-info").textContent = p
    ? `已选提示词：${p.name} · ${(p.chars || 0).toLocaleString()} 字符 ≈ ${(p.tokens_est || 0).toLocaleString()} token`
    : "尚未选择提示词";
}

$("#sc-prompt-source").onchange = renderScPromptInfo;

async function loadPromptFiles() {
  const files = await api("/api/prompts/files");
  $("#prompt-files").innerHTML = files.length
    ? files.map((f) => `<label title="${f.file}">
        <input type="checkbox" class="prompt-file-ck" value="${f.file}" />
        ${f.name} <span class="muted">~${f.tokens_est} tok</span></label>`).join("")
    : "<span class='muted'>prompts 目录下没有 .txt 文件</span>";
}

$("#btn-bulk-scenarios").onclick = async () => {
  const files = [...document.querySelectorAll(".prompt-file-ck:checked")].map((x) => x.value);
  if (!files.length) return alert("请先勾选要导入的提示词文件");
  try {
    const res = await api("/api/scenarios/bulk", {
      method: "POST",
      body: JSON.stringify({ files, group: $("#sc-group").value.trim() }),
    });
    await loadScenarios();
    alert(`已生成 ${res.created.length} 个场景`);
  } catch (e) { alert(`生成失败: ${e.message}`); }
};

$("#btn-save-prompt").onclick = async () => {
  try {
    await api("/api/prompts", { method: "POST", body: JSON.stringify({
      name: $("#prompt-name").value.trim(),
      tags: $("#prompt-tags").value.trim(),
      content: $("#prompt-content").value,
    })});
    $("#prompt-name").value = $("#prompt-tags").value = $("#prompt-content").value = "";
    await loadPrompts();
  } catch (e) { alert(`保存失败: ${e.message}`); }
};

window.deletePrompt = async (id) => {
  if (!confirm("删除该提示词？")) return;
  await api(`/api/prompts/${id}`, { method: "DELETE" });
  await loadPrompts();
};

async function loadScenarios() {
  const rows = await api("/api/scenarios");
  $("#scenario-table tbody").innerHTML =
    `<tr><th>场景</th><th>提示词</th><th>规模(≈token)</th><th>覆盖参数</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.name}</td>
      <td>${r.prompt_source || "-"}</td>
      <td>${(r.prompt_tokens_est || 0).toLocaleString()}${r.prompt_chars ? ` <span class="muted">(${r.prompt_chars.toLocaleString()} 字符)</span>` : ""}</td>
      <td>${r.overrides}</td>
      <td><button onclick="deleteScenario(${r.id})">删除</button></td>
    </tr>`).join("");
  $("#run-scenarios").innerHTML = rows.length
    ? rows.map((r) => `<label><input type="checkbox" class="run-scenario-ck" value="${r.id}" /> ${r.name}</label>`).join("")
    : "<span class='muted'>请先在资产库添加场景模板</span>";
}

$("#btn-save-scenario").onclick = async () => {
  let overrides = {};
  const raw = $("#sc-overrides").value.trim();
  if (raw) { try { overrides = JSON.parse(raw); } catch (e) { return alert("覆盖参数不是合法 JSON"); } }
  const promptId = Number($("#sc-prompt-source").value);
  const prompts = await api("/api/prompts");
  const p = prompts.find((x) => x.id === promptId);
  // 防呆：场景名里的规模（如 输入-20k）与实际提示词规模明显不符时先确认
  const name = $("#sc-name").value.trim();
  const hint = /(\d+)\s*k/i.exec(name);
  if (hint && p && p.tokens_est) {
    const expected = Number(hint[1]) * 1000;
    const ratio = p.tokens_est / expected;
    if (ratio < 0.6 || ratio > 1.8) {
      const ok = confirm(
        `⚠️ 场景名写的是 ${expected.toLocaleString()} token 量级（${name}），\n` +
        `但所选提示词「${p.name}」实际约 ${p.tokens_est.toLocaleString()} token。\n\n` +
        `名字和内容对不上时，压测结果会误导（例如"输入-20k"实际发的是 10k 内容）。\n` +
        `确定要这样保存吗？`);
      if (!ok) return;
    }
  }
  try {
    await api("/api/scenarios", { method: "POST", body: JSON.stringify({
      name, prompt: p ? p.content : "", overrides,
    })});
    $("#sc-name").value = $("#sc-overrides").value = "";
    await loadScenarios();
  } catch (e) { alert(`保存失败: ${e.message}`); }
};

window.deleteScenario = async (id) => {
  if (!confirm("删除该场景？")) return;
  await api(`/api/scenarios/${id}`, { method: "DELETE" });
  await loadScenarios();
};

/* 运行模式切换 */
document.querySelectorAll("input[name=run-mode]").forEach((el) => {
  el.onchange = () => {
    const scenarios = document.querySelector("input[name=run-mode]:checked").value === "scenarios";
    $("#scenario-block").hidden = !scenarios;
    $("#chat-block").hidden = scenarios;
  };
});

function syncRunMode() {
  const scenarios = document.querySelector("input[name=run-mode]:checked").value === "scenarios";
  $("#scenario-block").hidden = !scenarios;
  $("#chat-block").hidden = scenarios;
}

$("#btn-save-model").onclick = async () => {
  let extra = {};
  const raw = $("#model-extra").value.trim();
  if (raw) {
    try { extra = JSON.parse(raw); } catch (e) { return alert("extra_params 不是合法 JSON"); }
  }
  const payload = {
    label: $("#model-label").value.trim(),
    model: $("#model-id").value.trim(),
    connection_id: Number($("#model-conn").value) || null,
    extra_params: extra,
  };
  try {
    if (editingModelId) {
      await api(`/api/models/${editingModelId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/models", { method: "POST", body: JSON.stringify(payload) });
    }
    const wasEditing = !!editingModelId;
    resetModelForm();
    await loadModels();
    alert(wasEditing ? "模型已更新" : "模型已保存");
  } catch (e) { alert(`保存失败: ${e.message}`); }
};

function resetModelForm() {
  editingModelId = null;
  $("#model-label").value = $("#model-id").value = $("#model-extra").value = "";
  $("#btn-save-model").textContent = "保存模型";
  $("#btn-cancel-model").hidden = true;
}

window.editModel = (id) => {
  const m = modelsCache.find((x) => x.id === id);
  if (!m) return;
  editingModelId = id;
  $("#model-label").value = m.label || "";
  $("#model-id").value = m.model || "";
  $("#model-conn").value = m.connection_id ?? "";
  let extra = m.extra_params || "{}";
  try { extra = JSON.stringify(JSON.parse(extra || "{}"), null, 2); } catch (e) { /* keep */ }
  $("#model-extra").value = extra === "{}" ? "" : extra;
  $("#btn-save-model").textContent = "保存修改";
  $("#btn-cancel-model").hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
};

$("#btn-cancel-model").onclick = resetModelForm;

window.deleteModel = async (id) => {
  if (!confirm("删除该模型？")) return;
  await api(`/api/models/${id}`, { method: "DELETE" });
  await loadModels();
};

/* ── 连通性测试 / 结构调试 ─────────────── */
function prefillProbeExtra() {
  const id = Number($("#probe-model").value);
  const m = modelsCache.find((x) => x.id === id);
  let extra = m && m.extra_params ? m.extra_params : "{}";
  try { extra = JSON.stringify(JSON.parse(extra || "{}"), null, 2); } catch (e) { /* keep raw */ }
  $("#probe-extra").value = extra === "{}" ? "" : extra;
}

window.openProbe = (modelId, opts = {}) => {
  if (!modelsCache.length) return alert("请先在资产库添加模型");
  $("#probe-modal").hidden = false;
  if (modelId) $("#probe-model").value = String(modelId);
  if (opts.prompt !== undefined) $("#probe-prompt").value = opts.prompt;
  if (opts.maxTokens !== undefined) $("#probe-max-tokens").value = opts.maxTokens;
  if (opts.stream !== undefined) $("#probe-stream").checked = !!opts.stream;
  prefillProbeExtra();
};

$("#probe-model").onchange = prefillProbeExtra;

function closeProbe() {
  $("#probe-modal").hidden = true;      // 独立弹窗：关闭与压测任务无关
}
$("#probe-close").onclick = closeProbe;
$("#probe-close-bottom").onclick = closeProbe;
// 点遮罩空白处关闭
$("#probe-modal").onclick = (e) => { if (e.target.id === "probe-modal") closeProbe(); };
// Esc 关闭
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#probe-modal").hidden) closeProbe();
});

$("#btn-probe").onclick = () => {
  const mode = document.querySelector("input[name=run-mode]:checked").value;
  let modelId = null;
  if (mode === "chat") {
    modelId = Number($("#run-model").value) || null;
  } else {
    const first = document.querySelector(".run-model-ck:checked");
    modelId = first ? Number(first.value) : null;
  }
  if (!modelId) return alert("请先选择模型");
  window.openProbe(modelId, {
    prompt: $("#run-prompt").value,
    maxTokens: Number($("#run-max-tokens").value),
    stream: $("#run-stream").checked,
  });
};

$("#probe-send").onclick = async () => {
  const id = Number($("#probe-model").value);
  let extra = null;
  const raw = $("#probe-extra").value.trim();
  if (raw) {
    try { extra = JSON.parse(raw); } catch (e) { return alert("extra_params 不是合法 JSON"); }
  }
  $("#probe-hints").innerHTML = "<li>请求中…</li>";
  $("#probe-summary").innerHTML = "";
  $("#probe-request").innerHTML = "";
  $("#probe-response").innerHTML = "";
  try {
    const d = await api(`/api/models/${id}/probe`, {
      method: "POST",
      body: JSON.stringify({
        prompt: $("#probe-prompt").value,
        max_tokens: Number($("#probe-max-tokens").value),
        stream: $("#probe-stream").checked,
        extra_params: extra,
      }),
    });
    $("#probe-hints").innerHTML = (d.hints || []).map((h) => `<li>${h}</li>`).join("")
      || "<li>无提示</li>";
    renderProbe(d);
  } catch (e) {
    $("#probe-hints").innerHTML = `<li>失败：${e.message}</li>`;
  }
};

/* ── 调试结果渲染（结构化展示，不堆 JSON 文本）───────────── */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function kvTable(obj) {
  const rows = Object.entries(obj || {})
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`)
    .join("");
  return rows ? `<table class="kv">${rows}</table>` : "";
}

function rawJson(summary, obj) {
  return `<details class="raw"><summary>${esc(summary)}</summary>
    <pre class="code small">${esc(JSON.stringify(obj, null, 2))}</pre></details>`;
}

function chunkItem(i, ch) {
  if (ch === "[DONE]") {
    return `<div class="chunk done"><span class="idx">#${i}</span>流结束 [DONE]</div>`;
  }
  const choice = (ch.choices || [])[0] || {};
  const delta = choice.delta || {};
  const content = delta.content ?? delta.text ?? ((choice.message || {}).content ?? "");
  const reasoning = delta.reasoning_content || "";
  let label = "元数据";
  if (content) label = `内容：${esc(String(content).slice(0, 80))}`;
  else if (reasoning) label = `推理：${esc(String(reasoning).slice(0, 80))}`;
  else if (ch.usage) label = `usage：prompt ${ch.usage.prompt_tokens} / completion ${ch.usage.completion_tokens}`;
  return `<div class="chunk"><span class="idx">#${i}</span>${label}${rawJson("原始 JSON", ch)}</div>`;
}

function renderProbe(d) {
  const req = d.request || {};
  const r = d.response || {};

  // 请求体（压测实际发送的形态）
  $("#probe-request").innerHTML =
    `<div class="sect-title">POST ${esc(req.url)}</div>` +
    kvTable(req.headers) +
    `<div class="sect-title">请求体（本地估算 prompt ≈ ${req.prompt_tokens_est ?? "?"} token）</div>` +
    `<pre class="code small">${esc(JSON.stringify(req.body, null, 2))}</pre>`;

  // 顶部指标卡
  const ok = r.status_code === 200;
  const cards = [
    `<div class="pcard ${ok ? "ok" : "bad"}"><span>HTTP 状态</span><b>${r.status_code ?? "—"}</b></div>`,
    `<div class="pcard"><span>总耗时</span><b>${r.elapsed != null ? r.elapsed + "s" : "—"}</b></div>`,
  ];
  if (r.stream) {
    cards.push(`<div class="pcard"><span>TTFT（客户端测量）</span><b>${r.ttft != null ? r.ttft + "s" : "—"}</b></div>`);
    cards.push(`<div class="pcard"><span>SSE 分片数</span><b>${r.chunk_count ?? "—"}</b></div>`);
  }
  if (r.usage) {
    cards.push(`<div class="pcard"><span>输入 / 输出 token</span><b>${r.usage.prompt_tokens ?? "—"} / ${r.usage.completion_tokens ?? "—"}</b></div>`);
    const cached = (r.usage.prompt_tokens_details || {}).cached_tokens;
    if (cached !== undefined) {
      cards.push(`<div class="pcard"><span>缓存命中 token</span><b>${cached}</b></div>`);
    }
  }
  $("#probe-summary").innerHTML = cards.join("");

  // 响应区
  let html = "";
  if (r.error) {
    html += `<div class="content-box" style="border-color:#f5c2c7;background:#fdecea;color:#8a1f28">`
          + `请求失败：${esc(r.error)}</div>`;
  }
  if (r.headers && Object.keys(r.headers).length) {
    html += `<div class="sect-title">响应头（关键项）</div>${kvTable(r.headers)}`;
  }
  if (r.usage) {
    html += `<div class="sect-title">usage（接口字段）</div>${kvTable(r.usage)}`;
  }

  // 内容：流式取拼接结果，非流式取 message.content
  const msg = (((r.body || {}).choices || [])[0] || {}).message || {};
  const reasoningText = r.reasoning_text || msg.reasoning_content || "";
  const answerText = r.text || msg.content || "";
  if (reasoningText) {
    html += `<div class="sect-title">推理内容 reasoning_content</div>
      <div class="content-box reasoning">${esc(reasoningText)}</div>`;
  }
  if (answerText) {
    html += `<div class="sect-title">回答内容</div><div class="content-box">${esc(answerText)}</div>`;
  }

  // 流式分片逐条展示
  if (r.stream && Array.isArray(r.chunks) && r.chunks.length) {
    html += `<div class="sect-title">SSE 分片（点开看原始 JSON）</div>`;
    html += r.chunks.map((ch, i) => chunkItem(i + 1, ch)).join("");
  }

  // 原文折叠：保留 100% 可追溯性，但默认不占屏
  html += rawJson("查看 HTTP 原文（未加工）", {
    status_code: r.status_code, headers: r.headers, raw: r.raw,
    raw_truncated: r.raw_truncated || false, error: r.error,
  });
  if (!r.stream && r.body) {
    html += rawJson("查看解析后的完整响应 JSON", r.body);
  }
  $("#probe-response").innerHTML = html;
}

/* ── 运行压测 ─────────────────────────── */
$("#btn-start").onclick = async () => {
  const mode = document.querySelector("input[name=run-mode]:checked").value;
  const base = {
    name: $("#run-name").value.trim(),
    prompt: $("#run-prompt").value,
    concurrency: Number($("#run-concurrency").value),
    requests: Number($("#run-requests").value),
    max_tokens: Number($("#run-max-tokens").value),
    timeout: Number($("#run-timeout").value),
    warmup: Number($("#run-warmup").value),
    retries: Number($("#run-retries").value),
    stream: $("#run-stream").checked,
    track_cache: $("#run-cache").checked,
    unique_prefix: $("#run-unique").checked,
  };
  let body;
  if (mode === "scenarios") {
    const modelIds = [...document.querySelectorAll(".run-model-ck:checked")].map((x) => Number(x.value));
    const scenarioIds = [...document.querySelectorAll(".run-scenario-ck:checked")].map((x) => Number(x.value));
    if (!modelIds.length || !scenarioIds.length) return alert("请至少选择 1 个模型和 1 个场景");
    body = { ...base, mode: "scenarios", model_ids: modelIds, scenario_ids: scenarioIds };
  } else {
    const modelId = Number($("#run-model").value);
    if (!modelId) return alert("请先在资产库添加连接和模型");
    body = { ...base, mode: "chat", model_id: modelId };
  }
  try {
    const { task_id } = await api("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    await api(`/api/tasks/${task_id}/start`, { method: "POST" });
    attachTask(task_id);
  } catch (e) { alert(`启动失败: ${e.message}`); }
};

function attachTask(taskId) {
  currentTaskId = taskId;
  $("#btn-cancel").disabled = false;
  $("#btn-start").disabled = true;
  $("#run-bar").style.width = "0%";
  $("#run-log").textContent = "";
  setStatus("running", "运行中…");
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/tasks/${taskId}/events`);
  eventSource.addEventListener("status", (e) => {
    const d = JSON.parse(e.data);
    setStatus(d.status, `状态：${d.status}`);
    if (["success", "failed", "canceled"].includes(d.status)) finishRun(d);
  });
  eventSource.addEventListener("progress", (e) => {
    const p = JSON.parse(e.data);
    const pct = p.total ? (p.completed / p.total * 100) : 0;
    $("#run-bar").style.width = `${pct.toFixed(1)}%`;
    const fmt = (v, unit, digits = 2) => (v == null ? "—" : `${Number(v).toFixed(digits)}${unit}`);
    const cards = [];
    if (p.scene) cards.push(`<div>当前场景<b>${p.scene}</b>${p.scene_index ?? ""}/${p.scenes_total ?? ""}</div>`);
    cards.push(`<div>完成<b>${p.completed}/${p.total}</b>${pct.toFixed(0)}%</div>`);
    if (p.mode === "open") {
      // 开环：在途 = 积压，峰值为最大积压，最能说明服务器扛不扛得住
      cards.push(`<div>在途/峰值积压<b>${p.active}/${p.peak}</b>目标 ${p.qps_target ?? "-"} QPS</div>`);
    } else {
      const target = p.concurrency_target ?? 0;
      const full = p.peak >= target;
      cards.push(`<div>并发<b>${p.active}/${target}</b>${full ? "已打满" : `峰值仅 ${p.peak}（未打满）`}</div>`);
    }
    cards.push(`<div>实时速率(近20s)<b>${fmt(p.rate, " req/s")}</b>累计 ${fmt(p.rate_overall, " req/s")}</div>`);
    cards.push(`<div>平均延迟<b>${fmt(p.avg_latency, "s", 3)}</b></div>`);
    cards.push(`<div>TTFT 均值<b>${fmt(p.ttft_avg, "s", 3)}</b></div>`);
    cards.push(`<div>已用时 / 预计剩余<b>${fmt(p.elapsed, "s", 1)} / ${p.eta == null ? "计算中" : fmt(p.eta, "s", 0)}</b></div>`);
    cards.push(`<div>失败<b>${p.failed}</b></div>`);
    $("#run-metrics").innerHTML = cards.join("");
    const stateTxt = p.mode === "open"
      ? `在途 ${p.active}(峰 ${p.peak})`
      : `并发 ${p.active}/${p.concurrency_target ?? "-"}`;
    $("#run-log").textContent +=
      `⏳ ${p.completed}/${p.total} | ${stateTxt} | ${p.rate ?? 0} req/s | ` +
      `TTFT ${p.ttft_avg == null ? "—" : p.ttft_avg.toFixed(2) + "s"} | ` +
      `ETA ${p.eta == null ? "计算中" : p.eta.toFixed(0) + "s"} | 失败 ${p.failed}\n`;
    $("#run-log").scrollTop = $("#run-log").scrollHeight;
  });
  eventSource.addEventListener("error", (e) => {
    if (e.data) $("#run-log").textContent += `❌ ${JSON.parse(e.data).message}\n`;
  });
}

function finishRun(d) {
  $("#btn-cancel").disabled = true;
  $("#btn-start").disabled = false;
  if (eventSource) { eventSource.close(); eventSource = null; }
  if (d.status === "success") setStatus("success", `完成：成功 ${d.success}/${d.total}`);
  else if (d.status === "canceled") setStatus("canceled", "已中止（已跑结果已保存）");
  else setStatus("failed", `失败：${d.error || "见日志"}`);
}

function setStatus(kind, text) {
  const el = $("#run-status");
  el.className = `status ${kind}`;
  el.textContent = text;
}

$("#btn-cancel").onclick = async () => {
  if (!currentTaskId) return;
  await api(`/api/tasks/${currentTaskId}/cancel`, { method: "POST" });
};

/* ── 结果 ─────────────────────────────── */
async function loadResults() {
  const rows = await api("/api/results");
  const tbody = $("#result-table tbody");
  tbody.innerHTML = `<tr><th>ID</th><th>任务</th><th>状态</th><th>成功/总数</th><th>时间</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.id}</td><td>${r.name}</td><td>${r.status}</td>
      <td>${r.success}/${r.total}</td><td>${(r.created_at || "").slice(0, 19).replace("T", " ")}</td>
      <td>
        <button onclick="showResult(${r.id})">查看</button>
        <button onclick="downloadResult(${r.id}, 'json')">JSON</button>
        <button onclick="downloadResult(${r.id}, 'xlsx')">Excel</button>
      </td>
    </tr>`).join("");
}

$("#btn-refresh-results").onclick = loadResults;

window.downloadResult = (id, kind) => {
  window.location.href = `/api/results/${id}/download?kind=${kind}`;
};

window.showResult = async (id) => {
  const d = await api(`/api/results/${id}`);
  if (d.rows && d.rows.length) {
    const head = `<tr><th>模型</th><th>场景</th><th>成功率</th><th>QPS</th><th>P50</th><th>TTFT</th><th>缓存</th><th>重试</th></tr>`;
    const body = d.rows.map((r) => `<tr>
      <td>${r.model_label || r._model || "-"}</td><td>${r.name}</td>
      <td>${(r.success_rate ?? 0).toFixed(1)}%</td><td>${(r.qps ?? 0).toFixed(2)}</td>
      <td>${(r.latency_p50 ?? 0).toFixed(3)}s</td>
      <td>${r.ttft_avg == null ? "—" : r.ttft_avg.toFixed(3) + "s"}</td>
      <td>${r.cache_hit_rate == null ? "—" : r.cache_hit_rate.toFixed(1) + "%"}</td>
      <td>${r.retried ?? 0}</td></tr>`).join("");
    $("#result-detail").innerHTML = `<h3>${d.task.name} · 对比结果</h3><table>${head}${body}</table>`;
    return;
  }
  const s = d.summary || {};
  $("#result-detail").innerHTML = `
    <h3>${d.task.name}</h3>
    <div class="metrics">
      <div>成功率<b>${(s.success_rate ?? 0).toFixed(1)}%</b></div>
      <div>QPS<b>${(s.qps ?? 0).toFixed(2)}</b></div>
      <div>延迟 P50<b>${(s.latency_p50 ?? 0).toFixed(3)}s</b></div>
      <div>TTFT 均值<b>${s.ttft_avg == null ? "—" : s.ttft_avg.toFixed(3) + "s"}</b></div>
      <div>缓存命中<b>${s.cache_hit_rate == null ? "—" : s.cache_hit_rate.toFixed(1) + "%"}</b></div>
      <div>重试<b>${s.retried ?? 0}</b></div>
    </div>
    <pre>${JSON.stringify(s, null, 2).slice(0, 4000)}</pre>`;
};

/* ── 初始化 ───────────────────────────── */

/* ── 一致性基准（独立模块）────────────────*/
let parityDims = [];

async function loadParityDimensions() {
  parityDims = await api("/api/parity/dimensions");
  $("#pb-dimensions").innerHTML = parityDims.map((d) => `
    <label title="权重 ${d.weight}%">
      <input type="checkbox" class="pb-dim" value="${d.id}" checked />
      ${d.id} ${d.name} <span class="muted">${d.cases} 条 · 权重 ${d.weight}%</span>
    </label>`).join("");
}

async function loadParityBaselines() {
  const rows = await api("/api/parity/baselines");
  $("#pb-table tbody").innerHTML = `<tr><th>基线</th><th>模型</th><th>状态</th><th>条目</th><th></th></tr>` +
    rows.map((b) => `<tr>
      <td>${b.name}</td><td>${b.model_label}</td>
      <td>${b.status === "ready" ? "✅ 就绪" : b.status === "running" ? "⏳ 建立中" : b.status}</td>
      <td>${b.items}</td>
      <td>
        <button onclick="showBaselineDetail(${b.id})">查看</button>
        <button onclick="deleteParityBaseline(${b.id})">删除</button>
      </td>
    </tr>`).join("");
  $("#pr-baseline").innerHTML = rows.filter((b) => b.status === "ready")
    .map((b) => `<option value="${b.id}">${b.name}（${b.items} 条）</option>`).join("");
}

$("#btn-create-baseline").onclick = async () => {
  const modelId = Number($("#pb-model").value);
  const dims = [...document.querySelectorAll(".pb-dim:checked")].map((x) => x.value);
  if (!modelId) return alert("请先在资产库添加模型");
  if (!dims.length) return alert("至少选择一个测试维度");
  const est = parityDims.filter((d) => dims.includes(d.id)).reduce((n, d) => n + d.cases, 0);
  if (!confirm(`将为官方模型建立基线，约 ${est} 次请求，确定开始？`)) return;
  try {
    const res = await api("/api/parity/baselines", { method: "POST", body: JSON.stringify({
      name: $("#pb-name").value.trim(), model_id: modelId, dimensions: dims }) });
    await loadParityBaselines();
    await waitBaseline(res.baseline_id);
  } catch (e) { alert(`建立失败: ${e.message}`); }
};

/* ── 进度反馈 ─────────────────────────── */
function renderProgress(el, p, label) {
  if (!el) return;
  const done = p.done || 0, total = p.total || 0;
  const pct = total ? Math.min(100, done / total * 100) : 0;
  const finished = p.status === "ready" || p.status === "finished";
  const failed = p.status === "failed";
  el.hidden = false;
  el.innerHTML =
    `<b>${label}</b>：${failed ? "❌ 失败" : finished ? "✅ 完成" : "⏳ 进行中（已开始请求）"}　`
    + `${done}/${total}${total ? `（${pct.toFixed(0)}%）` : ""}`
    + (p.model ? `　模型：${esc(p.model)}` : "")
    + (p.current && !finished ? `<br>当前用例：${esc(p.current)}` : "")
    + (p.last_status != null && !finished ? `<br>上一条返回：HTTP ${p.last_status}` : "")
    + (p.last_error ? `<br><span style="color:#c62828">上一条错误：${esc(String(p.last_error).slice(0, 160))}</span>` : "")
    + (p.error && failed ? `<br><span style="color:#c62828">失败原因：${esc(p.error)}</span>` : "")
    + (failed ? `<br>建议：检查该模型连接的 Base URL / 接口路径 / API Key，或先用「调试」验证连通性。` : "")
    + `<div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>`;
}

async function waitBaseline(baselineId) {
  const el = $("#pb-progress");
  renderProgress(el, { status: "running", total: 0, done: 0 }, "建立基线");
  for (let i = 0; i < 600; i++) {
    const b = await api(`/api/parity/baselines/${baselineId}`);
    renderProgress(el, { ...(b.progress || {}), status: b.status, error: b.error }, "建立基线");
    if (b.status === "ready" || b.status === "failed") {
      await loadParityBaselines();
      return b;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  return null;
}

async function waitRun(runId) {
  const el = $("#pr-progress");
  renderProgress(el, { status: "running", total: 0, done: 0 }, "候选测试");
  for (let i = 0; i < 600; i++) {
    const r = await api(`/api/parity/runs/${runId}`);
    renderProgress(el, { ...(r.progress || {}), status: r.status, error: r.error }, "候选测试");
    if (r.status === "finished" || r.status === "failed") {
      await loadParityRuns();
      if (r.status === "finished") await showParityReport(runId);
      return r;
    }
    await new Promise((res) => setTimeout(res, 1500));
  }
  return null;
}

let pbDetailId = null;   // 当前展开的基线明细（再次点击同一基线即关闭）

/* 原始响应美化：是 JSON 就缩进美化，否则原样展示（报错原文也照原样给出） */
function prettyRaw(text, fallback = "") {
  const t = (text || "").trim() || fallback || "";
  if (!t) {
    return "（该记录未保存原始响应：多为此记录由旧版本生成或旧版运行。请「重建基线」/重新跑候选后再查看）";
  }
  try {
    return JSON.stringify(JSON.parse(t), null, 2);
  } catch (e) {
    return t;
  }
}

function closeParityPanels() {
  $("#pb-detail").innerHTML = "";
  $("#pb-detail").hidden = true;
  $("#pr-report").innerHTML = "";
  $("#pr-report").hidden = true;
  pbDetailId = null;
}

window.showBaselineDetail = async (baselineId) => {
  // 再次点击同一个「查看」→ 折叠关闭
  if (pbDetailId === baselineId && !$("#pb-detail").hidden) {
    closeParityPanels();
    return;
  }
  const b = await api(`/api/parity/baselines/${baselineId}`);
  const rows = b.items_detail || [];
  const box = { REJECT: "REJECT（拒绝）", ACCEPT_AS_IS: "ACCEPT（接受）",
                ERROR_OTHER: "5xx", UNKNOWN: "无响应" };
  // 与候选报告互斥显示，避免两块内容同时铺开
  $("#pr-report").innerHTML = "";
  $("#pr-report").hidden = true;
  pbDetailId = baselineId;
  $("#pb-detail").hidden = false;
  $("#pb-detail").innerHTML =
    `<h3 style="display:flex;justify-content:space-between;align-items:center">
       基线明细：${esc(b.name)}（${esc(b.model_label)} · ${esc(b.case_set)} · ${rows.length} 条）
       <button class="ghost" onclick="closeParityPanels()">关闭</button>
     </h3>`
    + `<div class="muted">建立时间：${esc(b.created_at)}　维度：${esc(b.dimensions)}　`
    + `（点每条用例可展开查看实际请求与响应）</div>`
    + rows.map((r) => `
      <details class="case-detail">
        <summary>${r.dimension} | ${esc(r.name)} | HTTP ${r.status_code ?? "—"} | `
        + `${box[r.behavior_class] || esc(r.behavior_class || "—")} | `
        + `${r.prompt_tokens ?? 0}/${r.completion_tokens ?? 0} token | ${(r.latency ?? 0).toFixed(2)}s</summary>
        <div class="probe-cols">
          <div><h3>实际请求体</h3><pre class="code small">${esc(JSON.stringify(r.request || {}, null, 2))}</pre></div>
          <div>
            <h3>实际响应（原始${r.status_code >= 400 ? " · 报错原文" : ""}）</h3>
            <div class="muted">HTTP ${r.status_code ?? "—"} · ${esc(r.behavior_class || "")} ·
              ${r.prompt_tokens ?? 0}/${r.completion_tokens ?? 0} token · ${(r.latency ?? 0).toFixed(2)}s</div>
            <pre class="code small">${esc(prettyRaw(r.raw_response, r.error))}</pre>
          </div>
        </div>
      </details>`).join("");
  $("#pb-detail").scrollIntoView({ behavior: "smooth", block: "start" });
};

window.closeParityPanels = closeParityPanels;

["#btn-refresh-pb-models", "#btn-refresh-pr-models"].forEach((sel) => {
  const el = $(sel);
  if (el) el.onclick = () => loadModels();
});

window.deleteParityBaseline = async (id) => {
  if (!confirm("删除该基线？已产生的运行记录不受影响。")) return;
  await api(`/api/parity/baselines/${id}`, { method: "DELETE" });
  await loadParityBaselines();
};

async function loadParityRuns() {
  const rows = await api("/api/parity/runs");
  $("#pr-table tbody").innerHTML = `<tr><th>任务</th><th>候选</th><th>状态</th><th>结论</th><th></th></tr>` +
    rows.map((r) => `<tr>
      <td>${r.name}</td><td>${r.model_label}</td>
      <td>${r.status === "finished" ? "✅ 完成" : r.status === "running" ? "⏳ 运行中" : r.status}</td>
      <td>${r.verdict || "—"}${r.score != null ? ` (${r.score})` : ""}</td>
      <td><button onclick="showParityReport(${r.id})">查看</button></td>
    </tr>`).join("");
}

$("#btn-create-run").onclick = async () => {
  const baselineId = Number($("#pr-baseline").value);
  const modelId = Number($("#pr-model").value);
  if (!baselineId) return alert("请先建立并选择基线");
  if (!modelId) return alert("请选择候选模型");
  try {
    const res = await api("/api/parity/runs", { method: "POST", body: JSON.stringify({
      baseline_id: baselineId, model_id: modelId, name: $("#pr-name").value.trim() }) });
    await loadParityRuns();
    await waitRun(res.run_id);
  } catch (e) { alert(`启动失败: ${e.message}`); }
};

window.showParityReport = async (runId) => {
  const run = await api(`/api/parity/runs/${runId}`);
  const rep = run.report || {};
  if (!rep.verdict) return ($("#pr-report").innerHTML = "<div class='muted'>报告尚未生成（任务可能仍在运行）。</div>");
  // 与基线明细互斥显示
  $("#pb-detail").innerHTML = "";
  $("#pb-detail").hidden = true;
  pbDetailId = null;
  $("#pr-report").hidden = false;
  const icon = { "等价": "🟢", "可疑": "🟠", "不等价": "🔴", "不可比": "⚪" }[rep.verdict] || "";
  let html = `<h3>${icon} 结论：${rep.verdict}`
    + (rep.score != null ? ` <span class="muted">等价度 ${rep.score}</span>` : "") + `</h3>`;
  const gate = rep.gate || {};
  if (!gate.ok) {
    html += `<div class="content-box" style="border-color:#f5c2c7;background:#fdecea">`
      + `<b>不可比，原因：</b><br>` + (gate.reasons || []).map((r) => `· ${esc(r)}`).join("<br>")
      + `<br><b>建议：</b>` + (gate.suggestions || []).map((s) => `· ${esc(s)}`).join("<br>") + `</div>`;
  }
  if (rep.red_flags && rep.red_flags.length) {
    html += `<div class="content-box" style="border-color:#f5c2c7;background:#fdecea">`
      + `<b>🚩 一票否决项</b><br>` + rep.red_flags.map((f) => `· ${esc(f)}`).join("<br>") + `</div>`;
  }
  html += `<table class="kv"><tr><th>维度</th><th>结论</th><th>指标</th></tr>`
    + (rep.dimensions || []).map((d) => {
        const i2 = { green: "🟢", yellow: "🟠", red: "🔴" }[d.verdict] || "";
        const m = Object.entries(d.metrics || {}).map(([k, v]) => `${k}=${v}`).join("；");
        return `<tr><th>${d.dimension} ${esc(d.name)}</th><td>${i2}</td><td>${esc(m)}</td></tr>`;
      }).join("") + `</table>`;
  html += `<div class="muted">${(rep.cases || []).length} 条用例（点每条可展开查看实际请求与双方响应）</div>`;
  html += (rep.cases || []).map((c) => {
        const i3 = { pass: "✅", fail: "❌", skip: "⚠️" }[c.verdict] || "—";
        const b = c.baseline || {}, t = c.candidate || {};
        return `<details class="case-detail">
          <summary>${i3} ${c.dimension} | ${esc(c.name)} | ${esc(c.reason)}</summary>
          <div class="probe-cols">
            <div><h3>实际请求体</h3><pre class="code small">${esc(JSON.stringify(c.request || {}, null, 2))}</pre></div>
            <div><h3>响应对比</h3>
            </div>
            <div class="muted">官方：HTTP ${b.status_code ?? "—"} · ${esc(b.behavior_class || "")} ·
              ${b.prompt_tokens ?? 0}/${b.completion_tokens ?? 0} token</div>
            <pre class="code small">${esc(prettyRaw(b.raw_response, b.output_text))}</pre>
            <div class="muted" style="margin-top:8px">候选：HTTP ${t.status_code ?? "—"} · ${esc(t.behavior_class || "")} ·
              ${t.prompt_tokens ?? 0}/${t.completion_tokens ?? 0} token
              ${t.error ? ` · ${esc(String(t.error).slice(0, 120))}` : ""}</div>
            <pre class="code small">${esc(prettyRaw(t.raw_response, t.output_text || t.error))}</pre>
          </div>
        </details>`;
      }).join("");
  html += `<div class="sect-title">配置快照</div><pre class="code small">${esc(JSON.stringify(rep.config || {}, null, 2))}</pre>`;
  html += `<button class="ghost" onclick="exportParityMarkdown(${runId})">导出 Markdown 报告</button>`;
  html += ` <button class="ghost" onclick="closeParityPanels()">关闭</button>`;
  $("#pr-report").innerHTML = html;
  $("#pr-report").scrollIntoView({ behavior: "smooth", block: "start" });
};

window.exportParityMarkdown = async (runId) => {
  const res = await api(`/api/parity/runs/${runId}/markdown`);
  const blob = new Blob([res.markdown], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `parity_report_${runId}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
};

(async function init() {
  try {
    await loadConnections();
    await loadModels();
    await loadParityDimensions();
    await loadPrompts();
    await loadPromptFiles();
    await loadScenarios();
    await loadResults();
    syncRunMode();
  } catch (e) {
    console.error(e);
  }
})();
