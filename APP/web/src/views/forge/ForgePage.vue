<template>
  <div class="tool-page">
    <NavBar />
    <main class="tool-main">
      <DesignBenchFeature />
    </main>
  </div>
</template>

<script setup lang="ts">
import NavBar from "@/components/NavBar.vue";
import { DesignBenchFeature } from "@/features/design-bench";
</script>

<style scoped>
.tool-page {
  min-height: 100vh;
}

.tool-main {
  padding-top: 56px;
}
</style>
