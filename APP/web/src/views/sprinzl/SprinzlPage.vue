<template>
  <div class="tool-page">
    <NavBar />
    <main class="tool-main">
      <SprinzlAlignFeature />
    </main>
  </div>
</template>

<script setup lang="ts">
import NavBar from "@/components/NavBar.vue";
import { SprinzlAlignFeature } from "@/features/sprinzl-align";
</script>

<style scoped>
.tool-page {
  min-height: 100vh;
}

.tool-main {
  padding-top: 56px;
}
</style>
