<script setup lang="ts" generic="T extends string">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

interface AppSelectOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: T;
    options: AppSelectOption<T>[];
    placeholder?: string;
    disabled?: boolean;
    size?: "sm" | "md";
    ariaLabel?: string;
    panelMaxWidth?: string;
  }>(),
  {
    placeholder: "请选择",
    disabled: false,
    size: "md",
    ariaLabel: "",
    panelMaxWidth: "520px",
  }
);

const emit = defineEmits<{
  "update:modelValue": [value: T];
  change: [value: T];
}>();

const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const open = ref(false);
const activeIndex = ref(0);

const selectedIndex = computed(() => props.options.findIndex((option) => option.value === props.modelValue));

const currentLabel = computed(() => {
  const option = props.options[selectedIndex.value];
  return option ? option.label : props.placeholder;
});

watch(open, (expanded) => {
  if (!expanded) return;
  activeIndex.value = Math.max(selectedIndex.value, 0);
  void nextTick(scrollActiveIntoView);
});

function scrollActiveIntoView() {
  const node = panel.value?.querySelector<HTMLElement>('[data-active="true"]');
  node?.scrollIntoView({ block: "nearest" });
}

function toggle() {
  if (props.disabled) return;
  open.value = !open.value;
}

function close(refocus = true) {
  open.value = false;
  if (refocus) trigger.value?.focus();
}

function moveActive(step: number) {
  if (!props.options.length) return;
  const next = (activeIndex.value + step + props.options.length) % props.options.length;
  activeIndex.value = next;
  void nextTick(scrollActiveIntoView);
}

function commit(index: number) {
  const option = props.options[index];
  if (!option) return;
  emit("update:modelValue", option.value);
  emit("change", option.value);
  close();
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    if (open.value) {
      event.preventDefault();
      close();
    }
    return;
  }
  if (!open.value) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveActive(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    moveActive(-1);
  } else if (event.key === "Home") {
    event.preventDefault();
    activeIndex.value = 0;
    void nextTick(scrollActiveIntoView);
  } else if (event.key === "End") {
    event.preventDefault();
    activeIndex.value = Math.max(props.options.length - 1, 0);
    void nextTick(scrollActiveIntoView);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    commit(activeIndex.value);
  } else if (event.key === "Tab") {
    close(false);
  }
}

function onDocumentPointerDown(event: PointerEvent) {
  if (!open.value) return;
  const target = event.target as Node | null;
  if (target && root.value && root.value.contains(target)) return;
  open.value = false;
}

onMounted(() => {
  window.addEventListener("pointerdown", onDocumentPointerDown, true);
});

onBeforeUnmount(() => {
  window.removeEventListener("pointerdown", onDocumentPointerDown, true);
});
</script>

<template>
  <div
    ref="root"
    class="app-select"
    :class="[`is-${size}`, { 'is-open': open, 'is-disabled': disabled }]"
  >
    <button
      ref="trigger"
      type="button"
      class="app-select-trigger"
      :disabled="disabled"
      :aria-label="ariaLabel || undefined"
      aria-haspopup="listbox"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggle"
      @keydown="onTriggerKeydown"
    >
      <span class="app-select-value">{{ currentLabel }}</span>
      <span class="app-select-caret" aria-hidden="true" />
    </button>

    <div
      v-if="open"
      ref="panel"
      class="app-select-panel"
      role="listbox"
      :style="{ maxWidth: panelMaxWidth }"
    >
      <button
        v-for="(option, index) in options"
        :key="option.value"
        type="button"
        role="option"
        class="app-select-option"
        :class="{ 'is-selected': index === selectedIndex, 'is-active': index === activeIndex }"
        :data-active="index === activeIndex ? 'true' : undefined"
        :aria-selected="index === selectedIndex ? 'true' : 'false'"
        :title="option.title"
        @click="commit(index)"
        @mouseenter="activeIndex = index"
      >
        {{ option.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.app-select {
  position: relative;
  display: inline-flex;
  min-width: 0;
}
.app-select-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: max-content;
  max-width: 100%;
  border: 1px solid var(--field-border);
  border-radius: 5px;
  background: var(--field-bg);
  color: var(--field-text);
  text-align: left;
  cursor: pointer;
}
.app-select.is-md .app-select-trigger {
  min-height: 38px;
  padding: 7px 11px;
  font-size: 0.9rem;
}
.app-select.is-sm .app-select-trigger {
  min-height: 26px;
  padding: 3px 8px;
  font-size: 0.78rem;
  gap: 6px;
}
.app-select-trigger:hover:not(:disabled),
.app-select.is-open .app-select-trigger {
  border-color: var(--accent);
}
.app-select.is-disabled .app-select-trigger {
  color: var(--text-muted);
  cursor: not-allowed;
}
.app-select-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-select-caret {
  flex: none;
  width: 6px;
  height: 6px;
  border-right: 1.5px solid var(--text-muted);
  border-bottom: 1.5px solid var(--text-muted);
  transform: rotate(45deg) translateY(-1px);
  transition: transform 0.15s ease;
}
.app-select.is-open .app-select-caret {
  transform: rotate(-135deg) translateY(-1px);
}
.app-select-panel {
  position: absolute;
  z-index: 80;
  top: calc(100% + 5px);
  left: 0;
  display: grid;
  gap: 2px;
  min-width: 100%;
  width: max-content;
  max-height: 340px;
  padding: 4px;
  border: 1px solid var(--field-border);
  border-radius: 5px;
  background: var(--picker-bg);
  box-shadow: var(--shadow-strong);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.app-select-option {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--field-text);
  font-size: 0.85rem;
  font-weight: 400;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}
.app-select-option.is-selected {
  background: var(--accent-soft-strong);
  color: var(--text-strong);
  font-weight: 600;
}
.app-select-option.is-active:not(.is-selected) {
  background: var(--accent-soft);
}
.app-select-option:hover {
  border-color: transparent;
}
</style>
