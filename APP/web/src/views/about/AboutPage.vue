<template>
  <div class="about-page">
    <NavBar />
    <div class="about-offset"></div>
    <About />
  </div>
</template>

<script setup lang="ts">
import NavBar from "@/components/NavBar.vue";
import About from "./About.vue";
</script>

<style scoped>
.about-page {
  min-height: 100vh;
}

.about-offset {
  height: 56px;
}
</style>
