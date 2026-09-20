import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物由 FastAPI 托管在 /；开发时把 /api 代理到本地后端
export default defineConfig({
  plugins: [vue()],
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5273,
    proxy: { "/api": { target: "http://127.0.0.1:8781", changeOrigin: true } },
  },
});
