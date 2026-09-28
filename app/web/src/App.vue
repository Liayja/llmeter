<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { health } from "./api/client";

const route = useRoute();
const collapsed = ref(false);
const healthText = ref("连接中…");
const healthOk = ref(true);
const title = computed(() => route.meta?.title || "llmeter");

onMounted(async () => {
  try {
    const h = await health();
    healthText.value = h.warning ? `⚠ ${h.warning}` : "本机服务正常";
    healthOk.value = true;
  } catch (e) {
    healthText.value = "服务不可用";
    healthOk.value = false;
  }
});
</script>

<template>
  <el-container class="shell">
    <!-- 左侧导航 -->
    <el-aside :width="collapsed ? '56px' : '176px'" class="side">
      <div class="brand" @click="collapsed = !collapsed">
        <span class="logo">lm</span>
        <span v-show="!collapsed" class="name">llmeter</span>
      </div>
      <el-menu :default-active="route.path" router :collapse="collapsed" class="menu">
        <el-menu-item index="/loadtest"><span>压测</span></el-menu-item>
        <el-menu-item index="/parity"><span>一致性基准</span></el-menu-item>
        <el-menu-item index="/multimodal"><span>多模态测试</span></el-menu-item>
        <el-menu-item index="/route-probe"><span>路由检测</span></el-menu-item>
        <el-menu-item index="/assets"><span>资产库</span></el-menu-item>
        <el-menu-item index="/results"><span>结果</span></el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部工具栏 -->
      <el-header class="topbar">
        <div class="page-title">{{ title }}</div>
        <div class="spacer" />
        <el-tag :type="healthOk ? 'success' : 'danger'" size="small" effect="light">{{ healthText }}</el-tag>
      </el-header>

      <!-- 主工作区 -->
      <el-main class="main">
        <!-- 压测任务可能在后台持续运行：保活页面状态和 SSE 连接 -->
        <router-view v-slot="{ Component }">
          <keep-alive include="LoadTestView">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell { height: 100%; }
.side {
  background: var(--c-surface);
  border-right: 1px solid var(--c-border);
  transition: width .2s;
  display: flex; flex-direction: column;
}
.brand {
  display: flex; align-items: center; gap: var(--sp-2);
  height: 44px; padding: 0 var(--sp-3); cursor: pointer;
  border-bottom: 1px solid var(--c-border);
}
.logo {
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border-radius: var(--r-sm);
  background: var(--c-primary); color: #fff; font-size: var(--fs-xs); font-weight: 700;
}
.name { font-weight: 700; color: var(--c-primary); }
.menu { border-right: none; flex: 1; }
.topbar {
  display: flex; align-items: center; gap: var(--sp-3);
  height: 44px !important; padding: 0 var(--sp-4);
  background: var(--c-surface); border-bottom: 1px solid var(--c-border);
}
.page-title { font-size: var(--fs-lg); font-weight: 600; color: var(--c-primary); }
.spacer { flex: 1; }
.main { padding: var(--sp-4); overflow: auto; }
</style>
