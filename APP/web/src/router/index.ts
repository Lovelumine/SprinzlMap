// src/router/index.ts
import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import Home from "../views/home/Home.vue";
import PageNotFound from "../404.vue"; // 引入404组件
import "../utils/prevent-bounce";

const routes: Array<RouteRecordRaw> = [
  { path: "/", name: "Home", component: Home },
  { path: "/:pathMatch(.*)*", name: "NotFound", component: PageNotFound }, // 404路由
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

export default router;
