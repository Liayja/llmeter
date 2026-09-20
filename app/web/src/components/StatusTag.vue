<script setup>
import { computed } from "vue";

const props = defineProps({
  value: { type: [String, Number, Boolean], default: "" },
  labels: { type: Object, default: () => ({}) },
});

const mapped = computed(() => {
  const v = String(props.value ?? "");
  const table = {
    queued: ["待运行", "info"],
    running: ["运行中", "primary"],
    success: ["成功", "success"],
    finished: ["完成", "success"],
    ready: ["就绪", "success"],
    failed: ["失败", "danger"],
    canceled: ["已中止", "warning"],
    pass: ["通过", "success"],
    fail: ["不通过", "danger"],
    skip: ["跳过", "info"],
    inconclusive: ["不可判断", "info"],
    green: ["通过", "success"],
    yellow: ["可疑", "warning"],
    red: ["不通过", "danger"],
    gray: ["不可判断", "info"],
    ...props.labels,
  };
  return table[v] || [v || "—", "info"];
});
</script>

<template>
  <el-tag size="small" effect="light" :type="mapped[1]">{{ mapped[0] }}</el-tag>
</template>
