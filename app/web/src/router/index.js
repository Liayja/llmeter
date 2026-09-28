import { createRouter, createWebHashHistory } from "vue-router";

// 用 hash 模式：由 FastAPI 静态托管时不需要额外的服务端路由配置
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/loadtest" },
    { path: "/loadtest", name: "loadtest", component: () => import("../views/LoadTestView.vue"), meta: { title: "压测" } },
    { path: "/parity", name: "parity", component: () => import("../views/ParityView.vue"), meta: { title: "一致性基准" } },
    { path: "/multimodal", name: "multimodal", component: () => import("../views/MultimodalView.vue"), meta: { title: "多模态测试" } },
    { path: "/route-probe", name: "route-probe", component: () => import("../views/RouteProbeView.vue"), meta: { title: "路由检测" } },
    { path: "/assets", name: "assets", component: () => import("../views/AssetView.vue"), meta: { title: "资产库" } },
    { path: "/results", name: "results", component: () => import("../views/ResultsView.vue"), meta: { title: "结果" } },
  ],
});

export default router;
