<script setup lang="ts" generic="T extends string">
interface FilterChipOption<T extends string> {
  value: T;
  label: string;
  count: number;
  title?: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: T;
    options: FilterChipOption<T>[];
    ariaLabel?: string;
  }>(),
  { ariaLabel: "筛选" }
);

const emit = defineEmits<{
  "update:modelValue": [value: T];
  change: [value: T];
}>();

function pick(value: T) {
  if (value === props.modelValue) return;
  emit("update:modelValue", value);
  emit("change", value);
}
</script>

<template>
  <div class="filter-chips" role="group" :aria-label="ariaLabel">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      class="filter-chip"
      :class="{ active: option.value === modelValue }"
      :title="option.title"
      :aria-pressed="option.value === modelValue ? 'true' : 'false'"
      @click="pick(option.value)"
    >
      <span>{{ option.label }}</span>
      <span class="filter-chip-count">{{ option.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.filter-chips {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: stretch;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--surface-1);
  overflow: hidden;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
}
.filter-chip + .filter-chip {
  border-left: 1px solid var(--border-soft);
}
.filter-chip:hover {
  border-color: transparent;
  background: var(--accent-soft);
  color: var(--text-strong);
}
.filter-chip.active {
  background: var(--accent-soft-strong);
  color: var(--text-strong);
  font-weight: 700;
}
.filter-chip-count {
  font-variant-numeric: tabular-nums;
}
</style>
