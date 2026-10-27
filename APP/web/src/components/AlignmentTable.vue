<template>
  <div ref="wrapperRef" class="alignment-wrappers">
    <table v-for="(chunk, index) in chunks" :key="index" class="alignment-table">
      <tbody>
        <tr v-for="row in visibleRows" :key="row.type">
          <th class="label-cell" scope="row">{{ row.label }}</th>
          <td
            v-for="item in chunk"
            :key="`${item.id}_${row.type}`"
            :class="getCellClass(item, row.type)"
            @click="handleCellClick(row.type, item.id)"
          >
            {{ getCellValue(item, row.type) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

interface AlignmentItem {
  id: string;
  base: string;
  sup_base: string;
  structure?: string;
}

type RowType = "id" | "origin" | "target" | "structure";

interface RowDescriptor {
  type: RowType;
  label: string;
}

const props = defineProps<{
  alignmentData: AlignmentItem[];
  chunkSize?: number;
  editable?: boolean;
  showOrigin?: boolean;
  colorMode?: "compare" | "targetOnly";
  idLabel?: string;
  originLabel?: string;
  targetLabel?: string;
  showStructure?: boolean;
  structureLabel?: string;
}>();

const emit = defineEmits<{
  (e: "target-click", id: string): void;
}>();

const wrapperRef = ref<HTMLDivElement | null>(null);
const wrapperWidth = ref(0);
const labelWidth = ref(120);
const cellFont = ref("13px Consolas, monospace");
const measureContext = ref<CanvasRenderingContext2D | null>(null);

const FALLBACK_CHUNK = 20;
const MIN_COLS = 4;
const WRAP_GUTTER = 24;
const MIN_CELL_WIDTH = 22;
const CELL_PADDING = 14;

const baseSize = computed(() => {
  if (!props.chunkSize) return 0;
  return Math.max(MIN_COLS, props.chunkSize);
});

const visibleRows = computed<RowDescriptor[]>(() => {
  const rows: RowDescriptor[] = [{ type: "id", label: props.idLabel || "ID" }];
  if (props.showOrigin !== false) {
    rows.push({ type: "origin", label: props.originLabel || "Template baseline" });
  }
  rows.push({ type: "target", label: props.targetLabel || "Your target" });
  if (props.showStructure) {
    rows.push({ type: "structure", label: props.structureLabel || "Structure" });
  }
  return rows;
});

const chunks = computed(() => {
  const result: AlignmentItem[][] = [];
  const desired = baseSize.value;
  if (desired > 0) {
    for (let i = 0; i < props.alignmentData.length; i += desired) {
      result.push(props.alignmentData.slice(i, i + desired));
    }
    return result;
  }

  const available = Math.max(
    0,
    wrapperWidth.value - labelWidth.value - WRAP_GUTTER
  );
  const safeWidth = available > 0 ? available : FALLBACK_CHUNK * MIN_CELL_WIDTH;
  let current: AlignmentItem[] = [];
  let currentWidth = 0;

  props.alignmentData.forEach((item) => {
    const colWidth = estimateCellWidth(item.id);
    const nextWidth = currentWidth + colWidth;
    if (current.length >= MIN_COLS && nextWidth > safeWidth) {
      result.push(current);
      current = [];
      currentWidth = 0;
    }
    if (!current.length && colWidth > safeWidth) {
      result.push([item]);
      return;
    }
    current.push(item);
    currentWidth += colWidth;
  });
  if (current.length) result.push(current);
  return result;
});

/**
 * Determine cell type based on base and sup_base values
 */
const getType = (
  item: AlignmentItem
): "match" | "mismatch" | "insertion" | "deletion" => {
  return item.base === "-" && item.sup_base !== "-"
    ? "insertion"
    : item.base !== "-" && item.sup_base === "-"
    ? "deletion"
    : item.base === item.sup_base
    ? "match"
    : "mismatch";
};

function getCellValue(item: AlignmentItem, rowType: RowType) {
  if (rowType === "id") return item.id;
  if (rowType === "origin") return item.base;
  if (rowType === "structure") return item.structure || "";
  return item.sup_base;
}

function getCellClass(item: AlignmentItem, rowType: RowType) {
  const classes: string[] = [];
  if (rowType === "origin") {
    classes.push(getType(item));
  }
  if (rowType === "target") {
    if (props.colorMode === "targetOnly") {
      classes.push(getTargetBaseClass(item.sup_base));
    } else {
      classes.push(getType(item));
    }
  }
  if (rowType === "structure") {
    classes.push(getStructureClass(item.structure));
  }
  if (rowType === "target" && props.editable) {
    classes.push("cell-editable");
  }
  return classes;
}

function getStructureClass(raw: string | undefined) {
  const token = String(raw || "").trim();
  if (token === ">") return "struct-open";
  if (token === "<") return "struct-close";
  if (token === ".") return "struct-loop";
  return "struct-empty";
}

function getTargetBaseClass(base: string) {
  const normalized = String(base || "-").trim().toUpperCase();
  if (normalized === "A") return "target-a";
  if (normalized === "C") return "target-c";
  if (normalized === "G") return "target-g";
  if (normalized === "U" || normalized === "T") return "target-u";
  return "target-gap";
}

function handleTargetClick(id: string) {
  if (!props.editable) return;
  emit("target-click", id);
}

function handleCellClick(rowType: RowType, id: string) {
  if (rowType !== "target") return;
  handleTargetClick(id);
}

let resizeObserver: ResizeObserver | null = null;

onMounted(() => {
  const measureCellWidths = () => {
    if (!wrapperRef.value) return;
    const labelCells = Array.from(
      wrapperRef.value.querySelectorAll("th.label-cell")
    ) as HTMLElement[];
    if (labelCells.length) {
      const maxLabel = Math.max(
        labelWidth.value,
        ...labelCells.map((cell) => cell.offsetWidth || 0)
      );
      if (maxLabel) labelWidth.value = maxLabel;
    }
    const sampleCell = wrapperRef.value.querySelector(
      "td"
    ) as HTMLElement | null;
    if (sampleCell) {
      const style = getComputedStyle(sampleCell);
      if (style.font) cellFont.value = style.font;
    }
  };

  const updateMetrics = () => {
    wrapperWidth.value = wrapperRef.value?.clientWidth || 0;
    if (!wrapperRef.value) return;
    const style = getComputedStyle(wrapperRef.value);
    const labelRaw = style.getPropertyValue("--label-width").trim();
    const parsedLabel = Number.parseFloat(labelRaw);
    if (!Number.isNaN(parsedLabel) && parsedLabel > 0) {
      labelWidth.value = parsedLabel;
    }
    nextTick(() => measureCellWidths());
  };
  updateMetrics();
  const observer = new ResizeObserver(updateMetrics);
  if (wrapperRef.value) observer.observe(wrapperRef.value);
  resizeObserver = observer;
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
});

watch(
  () => [props.alignmentData.length, wrapperWidth.value],
  async () => {
    await nextTick();
    if (!wrapperRef.value) return;
    const labelCells = Array.from(
      wrapperRef.value.querySelectorAll("th.label-cell")
    ) as HTMLElement[];
    if (labelCells.length) {
      const maxLabel = Math.max(
        labelWidth.value,
        ...labelCells.map((cell) => cell.offsetWidth || 0)
      );
      if (maxLabel) labelWidth.value = maxLabel;
    }
  }
);

function ensureMeasureContext() {
  if (!measureContext.value) {
    const canvas = document.createElement("canvas");
    measureContext.value = canvas.getContext("2d");
  }
  if (measureContext.value) {
    measureContext.value.font = cellFont.value;
  }
}

function estimateCellWidth(text: string) {
  ensureMeasureContext();
  const ctx = measureContext.value;
  if (!ctx) return MIN_CELL_WIDTH;
  const width = ctx.measureText(String(text)).width;
  return Math.max(MIN_CELL_WIDTH, Math.ceil(width + CELL_PADDING));
}
</script>

<style scoped>
/* —— 布局 —— */
.alignment-wrappers {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  width: 100%;
  --label-width: 120px;
}

/* —— 小表 —— */
.alignment-table {
  border-collapse: collapse;
  font-family: "Consolas", monospace;
  table-layout: auto;
  backdrop-filter: blur(4px);
  background: var(--surface-3);
  border-radius: 6px;
  overflow: hidden;
  max-width: 100%;
}

/* —— 单元格 —— */
.alignment-table th,
.alignment-table td {
  border: 1px solid var(--border-soft);
  padding: 3px 6px;
  text-align: center;
  white-space: nowrap;
  font-size: 13px;
  color: var(--text-strong);
  box-sizing: border-box;
  min-width: 22px;
}

/* 行首标签 */
.label-cell {
  background: var(--surface-2);
  font-weight: 600;
  width: var(--label-width);
  min-width: var(--label-width);
}

/* —— 配色 —— */
.alignment-table td.match {
  background: rgba(76, 175, 80, 0.55);   /* 绿 */
}
.alignment-table td.mismatch {
  background: rgba(244, 67, 54, 0.55);   /* 红 */
}
.alignment-table td.insertion {
  background: rgba(33, 150, 243, 0.55);  /* 蓝 */
}
.alignment-table td.deletion {
  background: rgba(255, 167, 38, 0.55);  /* 橙 */
}
.alignment-table td.target-a {
  background: rgba(244, 67, 54, 0.55);   /* A 红 */
}
.alignment-table td.target-c {
  background: rgba(76, 175, 80, 0.55);   /* C 绿 */
}
.alignment-table td.target-g {
  background: rgba(255, 167, 38, 0.55);  /* G 橙 */
}
.alignment-table td.target-u {
  background: rgba(33, 150, 243, 0.55);  /* U 蓝 */
}
.alignment-table td.target-gap {
  background: rgba(121, 134, 203, 0.55); /* - 蓝灰 */
}
.alignment-table td.struct-open,
.alignment-table td.struct-close {
  background: rgba(15, 23, 42, 0.14);
  color: var(--text-strong);
  font-weight: 700;
}
.alignment-table td.struct-loop {
  background: rgba(148, 163, 184, 0.16);
  color: var(--text-muted);
}
.alignment-table td.struct-empty {
  background: var(--surface-2);
  color: var(--text-soft);
}
.alignment-table td.cell-editable {
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb), 0.4);
}
.alignment-table td.cell-editable:hover {
  box-shadow: inset 0 0 0 2px rgba(var(--accent-rgb), 0.7);
}
</style>
