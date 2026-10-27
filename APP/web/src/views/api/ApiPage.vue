<template>
  <div class="api-page">
    <NavBar />
    <div class="api-offset"></div>
    <ApiReference />
  </div>
</template>

<script setup lang="ts">
import NavBar from "@/components/NavBar.vue";
import ApiReference from "./ApiReference.vue";
</script>

<style scoped>
.api-page {
  min-height: 100vh;
}

/* 固定导航栏占位，避免内容被遮住 */
.api-offset {
  height: 56px;
}
</style>
