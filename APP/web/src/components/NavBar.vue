<template>
  <nav
    class="navbar"
    @touchstart="onTouchStart"
    @touchmove="onTouchMove"
    @touchend="onTouchEnd"
  >
    <div class="container">
      <!-- 品牌 / Logo -->
      <a href="/" class="brand" aria-label="SprinzlMap home">
        <img class="brand-mark" src="/sprinzlmap-mark.svg" alt="" />
        <span>Sprinzl<span class="brand-highlight">Map</span></span>
      </a>

      <!-- 大屏时显示链接，小屏隐藏 -->
      <ul
        class="nav-links"
        :class="{ open: menuOpen }"
      >
        <li>
          <a href="/" :class="{ active: isActive('/') }"> Home </a>
        </li>
        <li>
          <a href="/sprinzl-align.html" :class="{ active: startsWith('/sprinzl-align') }">
            Numbering
          </a>
        </li>
        <li>
          <a
            href="/design-bench.html"
            :class="{ active: startsWith('/design-bench') }"
          >
            Compare
          </a>
        </li>

        <!-- 下列两个是独立页面入口，直接整页跳转 -->
        <li>
          <a href="/reference.html" :class="{ active: isActive('/reference.html') }">
            API
          </a>
        </li>
        <li>
          <a href="/about.html" :class="{ active: isActive('/about.html') }"> About </a>
        </li>
      </ul>

      <div class="nav-actions">
        <button
          class="theme-toggle"
          type="button"
          :aria-label="themeLabel"
          :title="themeLabel"
          :class="{ 'is-cycling': isCycling }"
          @click="handleCycle"
        >
          <span class="theme-icon-track" aria-hidden="true">
            <svg
              class="theme-svg"
              :class="{ active: effectiveTheme === 'light' }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="5" />
              <line x1="12" y1="1" x2="12" y2="3" />
              <line x1="12" y1="21" x2="12" y2="23" />
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
              <line x1="1" y1="12" x2="3" y2="12" />
              <line x1="21" y1="12" x2="23" y2="12" />
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
            </svg>
            <svg
              class="theme-svg"
              :class="{ active: effectiveTheme === 'dark' }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
            <svg
              class="theme-svg"
              :class="{ active: false }"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
          </span>
          <span class="theme-text">{{ themeShort }}</span>
        </button>

        <!-- 汉堡按钮：只在小屏显示 -->
        <button
          class="hamburger"
          type="button"
          @click="menuOpen = !menuOpen"
          aria-label="Toggle navigation"
          :aria-expanded="menuOpen"
        >
          <span :class="{ active: menuOpen }"></span>
          <span :class="{ active: menuOpen }"></span>
          <span :class="{ active: menuOpen }"></span>
        </button>
      </div>
    </div>
  </nav>

  <div
    v-if="menuOpen"
    class="menu-overlay"
    @click="menuOpen = false"
  />
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useTheme } from "@/composables/useTheme";

const menuOpen = ref(false);

// Swipe gesture state
const touchStartX = ref(0);
const touchStartY = ref(0);
const touchStartTime = ref(0);
const isSwiping = ref(false);

const SWIPE_THRESHOLD = 50;
const SWIPE_MAX_TIME = 300;

const onTouchStart = (e: TouchEvent) => {
  touchStartX.value = e.touches[0].clientX;
  touchStartY.value = e.touches[0].clientY;
  touchStartTime.value = Date.now();
  isSwiping.value = false;
};

const onTouchMove = (e: TouchEvent) => {
  const dx = Math.abs(e.touches[0].clientX - touchStartX.value);
  const dy = Math.abs(e.touches[0].clientY - touchStartY.value);
  if (dx > dy && dx > 10) {
    isSwiping.value = true;
  }
};

const onTouchEnd = (e: TouchEvent) => {
  if (!isSwiping.value) return;
  const dx = e.changedTouches[0].clientX - touchStartX.value;
  const dt = Date.now() - touchStartTime.value;
  if (dt > SWIPE_MAX_TIME) return;
  if (dx < -SWIPE_THRESHOLD && menuOpen.value) {
    menuOpen.value = false;
  } else if (dx > SWIPE_THRESHOLD && !menuOpen.value) {
    menuOpen.value = true;
  }
};
const { theme, effectiveTheme, cycleTheme } = useTheme();

const themeShort = computed(() => {
  if (theme.value === "system") return "Auto";
  return theme.value === "dark" ? "Dark" : "Light";
});
const themeLabel = computed(() => {
  if (theme.value === "system") return "Theme: Auto (system)";
  return `Theme: ${themeShort.value}`;
});

const isCycling = ref(false);
const handleCycle = () => {
  isCycling.value = true;
  cycleTheme();
  setTimeout(() => { isCycling.value = false; }, 350);
};

// 当前路径（不含 query/hash）
const pathname = typeof window !== "undefined" ? window.location.pathname : "/";

// 精确匹配首页（/ 或 /index.html）
const isActive = (path: string) => {
  if (path === "/") {
    return pathname === "/" || pathname === "/index.html";
  }
  return pathname === path;
};

// 以某个前缀开始（给工具类页面用）
const startsWith = (prefix: string) => pathname.startsWith(prefix);
</script>

<style scoped>
.navbar {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  background: var(--nav-bg);
  color: var(--nav-text);
  z-index: 20000;
  box-shadow: var(--nav-shadow);
  backdrop-filter: blur(12px) saturate(180%);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
  border-bottom: 1px solid var(--border-soft);
}
.container {
  max-width: 1240px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.5rem;
  height: 56px;
}
/* 品牌 */
.brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-size: 1.4rem;
  font-weight: bold;
  text-decoration: none;
  color: var(--nav-text);
}
.brand-mark {
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
}
.brand-highlight {
  color: var(--nav-accent);
}

/* 导航链接 */
.nav-links {
  display: flex;
  list-style: none;
  margin: 0;
  padding: 0;
}
.nav-links li + li {
  margin-left: 1.5rem;
}
.nav-links a {
  text-decoration: none;
  color: var(--nav-text);
  font-weight: 500;
  position: relative;
}
.nav-links a::after {
  content: "";
  position: absolute;
  left: 0;
  bottom: -4px;
  width: 0;
  height: 2px;
  background: var(--nav-accent);
  transition: width 0.2s ease;
}
.nav-links a:hover::after,
.nav-links a.active::after {
  width: 100%;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.dataset-select label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--nav-text);
}

.dataset-select select {
  background: rgba(var(--nav-text-rgb), 0.18);
  border: 1px solid rgba(var(--nav-text-rgb), 0.35);
  color: var(--nav-text);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
}

.dataset-select select:focus {
  outline: none;
  border-color: rgba(var(--nav-text-rgb), 0.6);
  background: rgba(var(--nav-text-rgb), 0.26);
}

.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 12px;
  border: 1px solid var(--toggle-border);
  background: var(--toggle-bg);
  color: var(--accent-strong);
  font-size: 0.85rem;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
}
.theme-toggle:hover {
  border-color: var(--toggle-border-hover);
  background: var(--toggle-bg-hover);
  box-shadow: 0 3px 10px rgba(var(--accent-rgb), 0.12);
  transform: translateY(-1px);
}
.theme-toggle.is-cycling {
  transform: scale(0.96);
}
.theme-icon-track {
  position: relative;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.theme-svg {
  position: absolute;
  inset: 0;
  width: 18px;
  height: 18px;
  opacity: 0;
  transform: rotate(-60deg) scale(0.5);
  transition: opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1), transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.theme-svg.active {
  opacity: 1;
  transform: rotate(0deg) scale(1);
}
.theme-text {
  letter-spacing: 0.02em;
}

/* 汉堡菜单按钮（移动端） */
.hamburger {
  display: none;
  flex-direction: column;
  justify-content: space-between;
  width: 24px;
  height: 18px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.hamburger span {
  display: block;
  width: 24px;
  height: 2px;
  background: var(--nav-text);
  transition: transform 0.3s ease, opacity 0.3s ease;
}
/* 打开状态下的动画 */
.hamburger span:nth-child(1).active {
  transform: translateY(8px) rotate(45deg);
}
.hamburger span:nth-child(2).active {
  opacity: 0;
}
.hamburger span:nth-child(3).active {
  transform: translateY(-8px) rotate(-45deg);
}

/* 小屏(Media < 768px) 适配 */
@media (max-width: 767px) {
  .nav-links {
    position: absolute;
    top: 56px;
    right: 0;
    z-index: 2;
    background: var(--page-bg);
    flex-direction: column;
    width: min(280px, 100vw);
    max-height: calc(100dvh - 56px);
    overflow-y: auto;
    transform: translateX(100%);
    transition: transform 0.3s ease;
    box-shadow: -4px 0 20px rgba(0, 0, 0, 0.15);
    border-left: 1px solid var(--border-soft);
    border-bottom: 1px solid var(--border-soft);
    pointer-events: none;
  }

  .nav-links.open {
    transform: translateX(0);
    pointer-events: auto;
  }

  .nav-links li + li {
    margin: 0;
  }

  .nav-links a {
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--border-soft);
    min-height: 44px;
    display: flex;
    align-items: center;
    color: var(--text-strong);
  }

  .hamburger {
    display: flex;
    padding: 8px;
    min-height: 44px;
    min-width: 44px;
    align-items: center;
    justify-content: center;
    gap: 5px;
  }

  .theme-toggle {
    min-height: 40px;
    min-width: 40px;
    width: 40px;
    height: 40px;
    padding: 0;
    border-radius: 12px;
    gap: 0;
    justify-content: center;
  }

  .theme-text {
    display: none;
  }

  .container {
    padding: 0 1rem;
  }
}

.menu-overlay {
  position: fixed;
  inset: 56px 0 0;
  background: rgba(15, 23, 42, 0.28);
  z-index: 19990;
}
</style>
