// vite.config.ts
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
import path from "path";
import { resolve } from "path";

const backendTarget = process.env.VITE_BACKEND_TARGET || "http://127.0.0.1:5679";

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      dts: "src/auto-imports.d.ts",
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      dts: "src/components.d.ts",
      resolvers: [ElementPlusResolver({ importStyle: "css" })],
    }),
  ],
  build: {
    rollupOptions: {
      // 多页面入口
      input: {
        main: resolve(__dirname, "index.html"),
        about: resolve(__dirname, "about.html"),
        reference: resolve(__dirname, "reference.html"),
        sprinzl: resolve(__dirname, "sprinzl-align.html"),
        forge: resolve(__dirname, "design-bench.html"),
        gold_curation: resolve(__dirname, "gold-curation.html"),
      },
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return;
          }
          if (
            /node_modules[\\/]vue[\\/]/.test(id) ||
            /node_modules[\\/]@vue[\\/]/.test(id) ||
            /node_modules[\\/]vue-router[\\/]/.test(id)
          ) {
            return "vendor-vue";
          }
          if (
            /node_modules[\\/]element-plus[\\/]/.test(id) ||
            /node_modules[\\/]@element-plus[\\/]/.test(id)
          ) {
            return "vendor-element";
          }
          if (/node_modules[\\/]d3(?:-|[\\/])/.test(id)) {
            return "vendor-d3";
          }
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    // 支持导入这些后缀的模块
    extensions: [".mjs", ".js", ".ts", ".jsx", ".tsx", ".json"],
  },
  server: {
    // 本地前端启动端口
    port: 5189,
    watch: {
      ignored: [
        "**/.runtime/**",
        "**/benchmark/results/**",
        "**/playwright-report/**",
        "**/test-results/**",
      ],
    },
    // 本地开发时的代理配置：统一经由 /api 前缀转发到 Flask 服务
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
