<!-- src/App.vue -->
<template>
  <div id="app">
    <NavBar />
    <!-- 为了让内容不被固定导航栏遮住，加一个占位 -->
    <div style="height: 56px"></div>

    <main class="view">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import NavBar from "./components/NavBar.vue";
</script>

<style>
/* —— 全局基础，保证 100% 宽度 —— */
html,
body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overscroll-behavior: none;
}
#app {
  width: 100%;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* 主内容区：铺满、scroll、水平居中子组件 */
.view {
  flex: 1;
  display: flex;
  justify-content: center; /* 子组件水平居中 */
  align-items: flex-start;
  overflow-y: auto;
  box-sizing: border-box;
  padding: 1rem;
}

/* 不再在这里限制 max-width！ */
.view > * {
  width: 100%;
}

/* Global mobile styles — breakpoints ordered largest-first */
@media (max-width: 1200px) {
  .view {
    padding: 0.875rem;
  }
}

@media (max-width: 1024px) {
  .view {
    padding: 0.75rem;
  }
}

@media (max-width: 480px) {
  .view {
    padding: 0.5rem;
  }

  /* Increase touch targets for interactive elements */
  .nav-links a, .btn, [role="button"], button[type="submit"], .el-button {
    min-height: 44px;
    min-width: 44px;
  }

  /* Improve form controls for mobile */
  input, select, textarea {
    font-size: 16px; /* Prevents zoom on iOS */
    padding: 12px;
  }
}
</style>
