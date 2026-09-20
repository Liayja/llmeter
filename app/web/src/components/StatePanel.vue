<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
  empty: { type: Boolean, default: false },
  emptyText: { type: String, default: "暂无数据" },
  emptyHint: { type: String, default: "" },
});
const emit = defineEmits(["retry"]);
</script>

<template>
  <div v-if="loading" class="state">
    <el-skeleton :rows="4" animated />
  </div>
  <el-alert v-else-if="error" type="error" show-icon :closable="false" class="state-alert">
    <template #title>加载失败</template>
    <div>{{ error }}</div>
    <el-button link type="primary" @click="emit('retry')">重新加载</el-button>
  </el-alert>
  <el-empty v-else-if="empty" :description="emptyText">
    <div v-if="emptyHint" class="muted">{{ emptyHint }}</div>
  </el-empty>
</template>

<style scoped>
.state { padding: var(--sp-4); }
.state-alert { margin: var(--sp-2) 0; }
.muted { color: var(--c-text-3); font-size: var(--fs-xs); }
</style>
