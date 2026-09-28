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
    accepted: ["已接受", "success"],
    partial: ["部分通过", "warning"],
    rejected: ["已拒绝", "danger"],
    error: ["请求错误", "danger"],
    SINGLE_ROUTE: ["单一指纹", "success"],
    MULTI_ROUTE_LIKELY: ["疑似多路由", "warning"],
    MULTI_ROUTE_SUSPECTED: ["多路由待确认", "warning"],
    MULTI_INSTANCE_LIKELY: ["疑似多实例", "warning"],
    EDGE_VARIANCE_LIKELY: ["入口波动", "warning"],
    CLIENT_OR_GATEWAY_UNSTABLE: ["客户端/网关不稳定", "danger"],
    INCONCLUSIVE: ["无法判断", "info"],
    ...props.labels,
  };
  return table[v] || [v || "—", "info"];
});
</script>

<template>
  <el-tag size="small" effect="light" :type="mapped[1]">{{ mapped[0] }}</el-tag>
</template>
