import { computed, ref, watch } from "vue";

export type ThemeMode = "system" | "light" | "dark";

const STORAGE_KEY = "aylm-theme";
const theme = ref<ThemeMode>("system");
const prefersDark = ref(false);
let initialized = false;

const effectiveTheme = computed<"light" | "dark">(() =>
  theme.value === "system" ? (prefersDark.value ? "dark" : "light") : theme.value,
);

function safeReadStorage(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      return stored;
    }
  } catch {
    // Ignore storage errors.
  }
  return "system";
}

function safeWriteStorage(value: ThemeMode) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // Ignore storage errors.
  }
}

function applyTheme() {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme.value === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", theme.value);
  }
  root.style.colorScheme = effectiveTheme.value;
}

function initMediaListener() {
  if (typeof window === "undefined") return;
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  prefersDark.value = media.matches;
  const handler = (event: MediaQueryListEvent | MediaQueryList) => {
    prefersDark.value = event.matches;
  };
  if (typeof media.addEventListener === "function") {
    media.addEventListener("change", handler);
    return;
  }
  const legacy = media as MediaQueryList & {
    addListener?: (listener: (event: MediaQueryListEvent | MediaQueryList) => void) => void;
  };
  legacy.addListener?.(handler);
}

export function initTheme() {
  if (initialized) return;
  initialized = true;
  theme.value = safeReadStorage();
  initMediaListener();
  applyTheme();
  watch(theme, (value) => {
    safeWriteStorage(value);
    applyTheme();
  });
  watch(prefersDark, () => {
    applyTheme();
  });
}

export function useTheme() {
  initTheme();
  const isDark = computed(() => effectiveTheme.value === "dark");
  const cycleTheme = () => {
    theme.value =
      theme.value === "system"
        ? "light"
        : theme.value === "light"
          ? "dark"
          : "system";
  };
  return {
    theme,
    effectiveTheme,
    isDark,
    cycleTheme,
  };
}
