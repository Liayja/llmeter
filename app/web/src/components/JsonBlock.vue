<script setup>
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";

const props = defineProps({
  value: { type: [Object, Array, String, Number, Boolean], default: null },
  title: { type: String, default: "" },
  fallback: { type: String, default: "" },
  rows: { type: Number, default: 18 },
  defaultExpanded: { type: Boolean, default: true },
});

const expanded = ref(props.defaultExpanded);

const text = computed(() => {
  const raw = props.value;
  if (raw == null || raw === "") return props.fallback || "";
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (!trimmed) return props.fallback || "";
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return raw;
    }
  }
  try {
    return JSON.stringify(raw, null, 2);
  } catch {
    return String(raw);
  }
});

const lines = computed(() => text.value.split("\n"));
const clipped = computed(() => lines.value.length > props.rows);
const visibleText = computed(() => {
  if (expanded.value || !clipped.value) return text.value;
  return lines.value.slice(0, props.rows).join("\n") + "\n…";
});

async function copy() {
  try {
    await navigator.clipboard.writeText(text.value);
    ElMessage.success("已复制");
  } catch {
    ElMessage.warning("浏览器未允许复制，请手动选择内容");
  }
}
</script>

<template>
  <div class="json-block">
    <div v-if="title || clipped" class="json-bar">
      <span class="json-title">{{ title }}</span>
      <span v-if="clipped" class="json-count">{{ lines.length }} 行</span>
      <span class="spacer" />
      <el-button v-if="clipped" link size="small" @click="expanded = !expanded">
        {{ expanded ? "收起" : "展开全部" }}
      </el-button>
      <el-button link size="small" @click="copy">复制</el-button>
    </div>
    <pre class="json-code">{{ visibleText || "（无内容）" }}</pre>
  </div>
</template>

<style scoped>
.json-block { border: 1px solid #2b3442; border-radius: var(--r-md); overflow: hidden; background: #1e2430; }
.json-bar {
  min-height: 28px; display: flex; align-items: center; gap: var(--sp-2);
  padding: 0 var(--sp-2); color: #d7e0eb; background: #252e3b; border-bottom: 1px solid #313c4c;
  font-size: var(--fs-xs);
}
.json-title { font-weight: 600; }
.json-count { color: #8d9db2; }
.spacer { flex: 1; }
.json-code {
  margin: 0; padding: var(--sp-3); color: #c8d3e0; background: #1e2430;
  font: 12px/1.55 "Cascadia Mono", Consolas, monospace; white-space: pre-wrap;
  overflow: auto; max-height: 460px;
}
:deep(.el-button) { color: #aec8ee; }
</style>
