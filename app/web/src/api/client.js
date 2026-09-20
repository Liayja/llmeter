// 统一的 API 客户端：与后端 /api/* 对接，集中处理错误提示
import { ElMessage } from "element-plus";

async function request(path, { method = "GET", body, silent = false } = {}) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (e) { /* 保持默认信息 */ }
    if (!silent) ElMessage.error(detail);
    throw new Error(detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.text();
}

export const api = {
  get: (p, o) => request(p, o),
  post: (p, body, o) => request(p, { ...o, method: "POST", body }),
  put: (p, body, o) => request(p, { ...o, method: "PUT", body }),
  del: (p, o) => request(p, { ...o, method: "DELETE" }),
};

export const health = () => api.get("/api/health");
export const listConnections = () => api.get("/api/connections");
export const listModels = () => api.get("/api/models");
export const listPrompts = () => api.get("/api/prompts");
export const listPromptFiles = () => api.get("/api/prompts/files");
export const listScenarios = () => api.get("/api/scenarios");
export const listTasks = () => api.get("/api/tasks");
export const listResults = () => api.get("/api/results");
export const parityDimensions = () => api.get("/api/parity/dimensions");
export const parityBaselines = () => api.get("/api/parity/baselines");
export const parityRuns = () => api.get("/api/parity/runs");

// 连接
export const createConnection = (body) => api.post("/api/connections", body);
export const updateConnection = (id, body) => api.put(`/api/connections/${id}`, body);
export const deleteConnection = (id) => api.del(`/api/connections/${id}`);
export const testConnection = (id) => api.post(`/api/connections/${id}/test`);

// 模型
export const createModel = (body) => api.post("/api/models", body);
export const updateModel = (id, body) => api.put(`/api/models/${id}`, body);
export const deleteModel = (id) => api.del(`/api/models/${id}`);
export const probeModel = (id, body) => api.post(`/api/models/${id}/probe`, body);

// 提示词
export const createPrompt = (body) => api.post("/api/prompts", body);
export const updatePrompt = (id, body) => api.put(`/api/prompts/${id}`, body);
export const deletePrompt = (id) => api.del(`/api/prompts/${id}`);

// 场景
export const createScenario = (body) => api.post("/api/scenarios", body);
export const updateScenario = (id, body) => api.put(`/api/scenarios/${id}`, body);
export const deleteScenario = (id) => api.del(`/api/scenarios/${id}`);
export const bulkCreateScenarios = (body) => api.post("/api/scenarios/bulk", body);

// 压测任务
export const createTask = (body) => api.post("/api/tasks", body);
export const startTask = (id) => api.post(`/api/tasks/${id}/start`);
export const cancelTask = (id) => api.post(`/api/tasks/${id}/cancel`);
export const getTask = (id) => api.get(`/api/tasks/${id}`);

// 一致性基准
export const createBaseline = (body) => api.post("/api/parity/baselines", body);
export const getBaseline = (id) => api.get(`/api/parity/baselines/${id}`);
export const deleteBaseline = (id) => api.del(`/api/parity/baselines/${id}`);
export const cancelBaseline = (id) => api.post(`/api/parity/baselines/${id}/cancel`);
export const createParityRun = (body) => api.post("/api/parity/runs", body);
export const getParityRun = (id) => api.get(`/api/parity/runs/${id}`);
export const cancelParityRun = (id) => api.post(`/api/parity/runs/${id}/cancel`);
export const parityMarkdown = (id) => api.get(`/api/parity/runs/${id}/markdown`);

// 结果
export const getResult = (id) => api.get(`/api/results/${id}`);
export const resultDownloadUrl = (id, kind = "json") =>
  `/api/results/${id}/download?kind=${encodeURIComponent(kind)}`;
