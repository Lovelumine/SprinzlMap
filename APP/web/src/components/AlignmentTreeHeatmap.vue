<template>
  <div class="tree-heatmap" :data-coordinate-order="safeIds.join(',')">
    <div class="heatmap-toolbar">
      <span v-if="title" class="heatmap-title">{{ title }}</span>
      <div class="heatmap-controls">
        <label class="control">
          Window
          <input
            type="range"
            :min="minWindow"
            :max="maxWindow"
            v-model.number="windowSize"
          />
          <span class="control-value">{{ windowSize }}</span>
        </label>
        <label class="control">
          Start
          <input
            type="range"
            min="0"
            :max="maxStart"
            v-model.number="windowStart"
          />
          <span class="control-value">{{ windowStart + 1 }}</span>
        </label>
        <label class="control">
          Region
          <select v-model="selectedRegion" class="control-select">
            <option
              v-for="option in regionOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="control control-toggle">
          <input
            type="checkbox"
            v-model="useClusterOrder"
            :disabled="!clusterOrderAvailable"
          />
          Clustered order
        </label>
        <div v-if="mode !== 'numeric' && showColorToggle" class="control control-colors">
          <span class="control-text">Heatmap colors</span>
          <div class="color-toggle">
            <button
              type="button"
              class="color-btn base"
              :class="{ active: colorSchemeModel === 'base' }"
              @click="colorSchemeModel = 'base'"
              :aria-pressed="colorSchemeModel === 'base'"
            >
              Base
            </button>
            <button
              type="button"
              class="color-btn diff"
              :class="{ active: colorSchemeModel === 'diff' }"
              @click="colorSchemeModel = 'diff'"
              :aria-pressed="colorSchemeModel === 'diff'"
            >
              Changes
            </button>
          </div>
        </div>
      </div>
      <div class="heatmap-legend">
        <span v-for="item in legendItems" :key="item.label" class="legend-item">
          <span
            class="legend-swatch"
            :style="{ background: item.color }"
          ></span>
          {{ item.label }}
        </span>
      </div>
    </div>

    <div class="combo-body">
      <div ref="wrapRef" class="combo-canvas">
        <svg ref="svgRef"></svg>
      </div>
      <teleport to="body">
        <div
          v-if="tooltip.visible"
          ref="tooltipRef"
          class="svg-tooltip"
          :style="{ left: `${tooltip.x}px`, top: `${tooltip.y}px` }"
        >
          {{ tooltip.text }}
        </div>
      </teleport>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import * as d3 from "d3";
import { useTheme } from "@/composables/useTheme";
import { apiFetch } from "@/shared/api";

interface HeatmapRow {
  name: string;
  values: Array<string | number | null>;
  label?: string;
  details?: VariantDetail[][];
  isReference?: boolean;
}
type VariantDetail = {
  variant: string;
  ref: string;
  alt: string;
  pos: string;
  trna: string;
};

const emit = defineEmits<{
  (e: "update:colorScheme", value: "base" | "diff"): void;
}>();

const props = defineProps<{
  ids: string[];
  rows: HeatmapRow[];
  treeNewick: string | null;
  treeError?: string | null;
  cellSize?: number;
  mode?: "base" | "numeric";
  colorScheme?: "base" | "diff";
  reference?: string[];
  rowReferences?: string[][];
  referenceMode?: "single" | "per-row" | "none";
  showColorToggle?: boolean;
  numericMax?: number;
  numericMin?: number;
  valueLabel?: string;
  title?: string;
}>();

const baseLegendItems = [
  { label: "-", color: "#F8FAFC" },
  { label: "A", color: "#EF4444" },
  { label: "G", color: "#22C55E" },
  { label: "C", color: "#3B82F6" },
  { label: "U", color: "#F59E0B" },
  { label: "N", color: "#94A3B8" },
];

const diffLegendItems = [
  { label: "Match", color: "#CBD5E1" },
  { label: "Changed", color: "#EF4444" },
  { label: "Gap", color: "#F8FAFC" },
];
const diffMatchColor = "#CBD5E1";
const diffChangeColor = "#EF4444";
const diffGapColor = "#F8FAFC";


const colorMap: Record<string, string> = Object.fromEntries(
  baseLegendItems.map((item) => [item.label, item.color])
);

function getCssVar(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(
    name
  );
  return value ? value.trim() : fallback;
}

const wrapRef = ref<HTMLDivElement | null>(null);
const svgRef = ref<SVGSVGElement | null>(null);
const tooltipRef = ref<HTMLDivElement | null>(null);
const wrapWidth = ref(0);
const cellSize = computed(() => props.cellSize ?? 16);
const mode = computed(() => props.mode ?? "base");
const localScheme = ref<"base" | "diff">(props.colorScheme ?? "base");
watch(
  () => props.colorScheme,
  (value) => {
    if (value) localScheme.value = value;
    else localScheme.value = "base";
  }
);
const colorScheme = computed(() => localScheme.value);
const colorSchemeModel = computed({
  get: () => localScheme.value,
  set: (value: "base" | "diff") => {
    localScheme.value = value;
    emit("update:colorScheme", value);
  },
});
const referenceMode = computed(() => props.referenceMode ?? "single");
const referenceBases = computed(() => props.reference ?? []);
const rowReferences = computed(() => props.rowReferences ?? []);
const visibleReferenceBases = computed(() =>
  referenceBases.value.slice(
    windowStart.value,
    windowStart.value + windowSize.value
  )
);
const visibleRowReferences = computed(() =>
  rowReferences.value.map((row: string[]) =>
    row.slice(windowStart.value, windowStart.value + windowSize.value)
  )
);
const showColorToggle = computed(() => {
  if (referenceMode.value === "per-row") return false;
  return props.showColorToggle ?? true;
});
const hasDiffReference = computed(() => {
  if (referenceMode.value === "single") return visibleReferenceBases.value.length > 0;
  if (referenceMode.value === "per-row")
    return visibleRowReferences.value.some((row) => row && row.length);
  return false;
});
const title = computed(() =>
  props.title === undefined ? "Alignment heatmap" : props.title
);
const useClusterOrder = ref(true);
const tooltip = ref({ visible: false, text: "", x: 0, y: 0 });
const regionLookup = ref<Record<string, string | null>>({});
const selectedRegion = ref("All");
const regionTreeNewick = ref<string | null>(null);
const regionTreeError = ref<string | null>(null);

let regionTreeFetchInFlight = false;
let regionTreeCooldownUntil = 0;
let lastRegionTreeKey = "";
const { effectiveTheme } = useTheme();
const numericMax = computed(() => {
  if (props.numericMax && props.numericMax > 0) return props.numericMax;
  let maxVal = 0;
  for (const row of props.rows) {
    for (const v of row.values) {
      const num = typeof v === "number" ? v : Number(v);
      if (!Number.isNaN(num)) maxVal = Math.max(maxVal, num);
    }
  }
  return maxVal;
});
const numericMin = computed(() => {
  if (props.numericMin !== undefined) return props.numericMin;
  let minVal = Infinity;
  for (const row of props.rows) {
    for (const v of row.values) {
      const num = typeof v === "number" ? v : Number(v);
      if (!Number.isNaN(num)) minVal = Math.min(minVal, num);
    }
  }
  return isFinite(minVal) ? minVal : 0;
});
const valueLabel = computed(
  () => props.valueLabel ?? (mode.value === "numeric" ? "Value" : "Base")
);

const regionOptions = [
  { value: "All", label: "All regions" },
  { value: "AA_Arm_5prime", label: "AA arm 5' end" },
  { value: "D_Loop", label: "D loop + D stem" },
  { value: "Anticodon_Arm", label: "Anticodon loop + stem" },
  { value: "Variable_Loop", label: "Variable loop" },
  { value: "T_Arm", label: "T loop + T stem" },
  { value: "AA_Arm_3prime", label: "AA arm 3' end" },
];

const safeRows = computed(() => props.rows ?? []);
const safeIds = computed(() => props.ids ?? []);

function displayLabel(row: HeatmapRow) {
  return row.label ?? row.name;
}

const labelWidth = computed(() => {
  const maxLen = Math.max(
    3,
    ...safeRows.value.map((row) => displayLabel(row).length)
  );
  return Math.min(300, Math.max(120, Math.round(maxLen * 8.2)));
});

const treeWidth = computed(() => {
  const base = wrapWidth.value ? wrapWidth.value * 0.28 : 220;
  const min = mode.value === "numeric" ? 200 : 180;
  const max = mode.value === "numeric" ? 320 : 260;
  return Math.max(min, Math.min(max, base));
});

const topLabelHeight = computed(() =>
  Math.max(72, Math.round(cellSize.value * 3.8))
);
const gap = 12;
const leftPadding = 8;

const labelAreaX = computed(() => leftPadding + treeWidth.value + 10);
const heatmapX = computed(() => labelAreaX.value + labelWidth.value + gap);
const heatmapWidth = computed(() =>
  Math.max(1, wrapWidth.value - heatmapX.value)
);

const filteredIndices = computed(() => {
  if (selectedRegion.value === "All") {
    return safeIds.value.map((_, idx) => idx);
  }
  const indices: number[] = [];
  safeIds.value.forEach((id, idx) => {
    if (determineRegion(id) === selectedRegion.value) indices.push(idx);
  });
  return indices;
});

const filteredIds = computed(() =>
  filteredIndices.value.map((idx) => safeIds.value[idx] ?? "")
);

const filteredRowsRaw = computed(() => {
  if (selectedRegion.value === "All") return safeRows.value;
  const indices = filteredIndices.value;
  return safeRows.value.map((row) => ({
    name: row.name,
    values: indices.map((idx) => row.values[idx] ?? "-"),
    details: row.details
      ? indices.map((idx) => row.details?.[idx] ?? [])
      : undefined,
    isReference: row.isReference,
  }));
});

const activeIds = computed(() => filteredIds.value);

const baseMinWindow = 4;
const minWindow = computed(() =>
  Math.min(baseMinWindow, activeIds.value.length || baseMinWindow)
);
const maxWindow = computed(() =>
  Math.max(minWindow.value, activeIds.value.length || baseMinWindow)
);

const windowSize = ref(0);
const windowStart = ref(0);

const maxStart = computed(() =>
  Math.max(0, activeIds.value.length - windowSize.value)
);

const visibleIds = computed(() =>
  activeIds.value.slice(windowStart.value, windowStart.value + windowSize.value)
);

const maxLabels = computed(() => {
  if (!heatmapWidth.value) return 24;
  return Math.max(8, Math.floor(heatmapWidth.value / 36));
});

const labelStep = computed(() => {
  if (!visibleIds.value.length) return 1;
  const cellW = heatmapWidth.value / visibleIds.value.length;
  const stepByWidth = Math.ceil(36 / Math.max(1, cellW));
  const stepByCount = Math.ceil(visibleIds.value.length / maxLabels.value);
  return Math.max(1, stepByWidth, stepByCount);
});

function normalizeLabel(raw: string) {
  let out = (raw || "").trim();
  if (!out) return "seq";
  out = out.replace(/[ ,:();]/g, "_");
  out = out.replace(/'/g, "");
  return out || "seq";
}

function normalizeRegionName(name: string | null): string | null {
  if (!name) return null;
  const normalized = name.trim().toLowerCase();
  switch (normalized) {
    case "aminoacyl arm 5' end":
      return "AA_Arm_5prime";
    case "d loop + d stem":
      return "D_Loop";
    case "anticodon loop + anticodon stem":
      return "Anticodon_Arm";
    case "variable loop":
      return "Variable_Loop";
    case "t loop + t stem":
      return "T_Arm";
    case "aminoacyl arm 3' end":
      return "AA_Arm_3prime";
    default:
      return null;
  }
}

function determineRegion(identifier: string): string | null {
  const mapped = regionLookup.value[identifier];
  if (mapped !== undefined) return mapped;

  let clean = identifier.toLowerCase();
  clean = clean.replace(/i\d+$/, "");

  if (/^-?\d+$/.test(clean)) {
    const num = parseInt(clean, 10);
    if (-1 <= num && num <= 9) return "AA_Arm_5prime";
    if (10 <= num && num <= 26) return "D_Loop";
    if (27 <= num && num <= 43) return "Anticodon_Arm";
    if (44 <= num && num <= 48) return "Variable_Loop";
    if (49 <= num && num <= 65) return "T_Arm";
    if (66 <= num && num <= 76) return "AA_Arm_3prime";
    return null;
  }

  if (["17a", "20a", "20b"].includes(clean)) {
    return "D_Loop";
  }

  if (clean.startsWith("v")) {
    const vnum = clean.slice(1);
    if (/^\d+$/.test(vnum)) {
      const n = parseInt(vnum, 10);
      if ((1 <= n && n <= 5) || (11 <= n && n <= 17) || (21 <= n && n <= 27)) {
        return "Variable_Loop";
      }
    }
  }

  return null;
}

function formatRegion(region: string | null) {
  if (!region) return "—";
  switch (region) {
    case "AA_Arm_5prime":
      return "AA arm 5' end";
    case "D_Loop":
      return "D loop + D stem";
    case "Anticodon_Arm":
      return "Anticodon loop + stem";
    case "Variable_Loop":
      return "Variable loop";
    case "T_Arm":
      return "T loop + T stem";
    case "AA_Arm_3prime":
      return "AA arm 3' end";
    default:
      return region;
  }
}

function tokenizeNewick(text: string): string[] {
  const tokens: string[] = [];
  let buf = "";
  let inQuote = false;
  for (let i = 0; i < (text || "").length; i++) {
    const ch = text[i];
    if (inQuote) {
      if (ch === "'") {
        if (text[i + 1] === "'") {
          buf += "'";
          i += 1;
        } else {
          inQuote = false;
        }
      } else {
        buf += ch;
      }
      continue;
    }
    if (ch === "'") {
      inQuote = true;
      continue;
    }
    if ("(),:;".includes(ch)) {
      if (buf.trim()) {
        tokens.push(buf.trim());
        buf = "";
      }
      tokens.push(ch);
      continue;
    }
    if (/\s/.test(ch)) {
      if (buf.trim()) {
        tokens.push(buf.trim());
        buf = "";
      }
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) tokens.push(buf.trim());
  return tokens;
}

function parseNewick(text: string) {
  const tokens = tokenizeNewick(text || "");
  const ancestors: any[] = [];
  let tree: any = {};
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (!token) continue;
    switch (token) {
      case "(":
        const child = {};
        tree.children = tree.children || [];
        tree.children.push(child);
        ancestors.push(tree);
        tree = child;
        break;
      case ",":
        const sibling = {};
        if (!ancestors.length) break;
        ancestors[ancestors.length - 1].children =
          ancestors[ancestors.length - 1].children || [];
        ancestors[ancestors.length - 1].children.push(sibling);
        tree = sibling;
        break;
      case ")":
        if (ancestors.length) {
          tree = ancestors.pop();
        }
        break;
      case ":":
        break;
      default: {
        const prev = tokens[i - 1];
        if (prev === ":") {
          tree.length = Number(token);
        } else if (prev === ")" || prev === "(" || prev === ",") {
          tree.name = token;
        }
        break;
      }
    }
  }
  return tree;
}

const effectiveTreeNewick = computed(() => {
  if (mode.value === "numeric") return props.treeNewick;
  if (selectedRegion.value === "All") return props.treeNewick;
  return regionTreeNewick.value;
});

function collectLeafOrder(node: any, order: string[]) {
  if (!node) return;
  if (!node.children || node.children.length === 0) {
    if (node.name) order.push(node.name);
    return;
  }
  node.children.forEach((child: any) => collectLeafOrder(child, order));
}

const clusterOrder = computed(() => {
  const treeNewick = effectiveTreeNewick.value;
  if (!treeNewick) return [];
  try {
    const tree = parseNewick(treeNewick);
    const order: string[] = [];
    collectLeafOrder(tree, order);
    return order.map((name) => normalizeLabel(name));
  } catch {
    return [];
  }
});

const clusterOrderAvailable = computed(() => clusterOrder.value.length > 0);

const orderedRows = computed(() => {
  const rows = filteredRowsRaw.value;
  const refRows = rows.filter((row) => row.isReference);
  const dataRows = rows.filter((row) => !row.isReference);
  if (!useClusterOrder.value || clusterOrder.value.length === 0) {
    return [...refRows, ...dataRows];
  }
  const rowMap = new Map<string, HeatmapRow[]>();
  dataRows.forEach((row) => {
    const key = normalizeLabel(row.name);
    const list = rowMap.get(key) ?? [];
    list.push(row);
    rowMap.set(key, list);
  });
  const ordered: HeatmapRow[] = [];
  clusterOrder.value.forEach((name) => {
    const list = rowMap.get(name);
    if (list && list.length) {
      const row = list.shift();
      if (row) ordered.push(row);
    }
  });
  if (ordered.length === dataRows.length) return [...refRows, ...ordered];
  const seen = new Set(ordered);
  return [...refRows, ...ordered, ...dataRows.filter((row) => !seen.has(row))];
});

const visibleRows = computed(() =>
  orderedRows.value.map((row) => ({
    name: row.name,
    values: row.values.slice(
      windowStart.value,
      windowStart.value + windowSize.value
    ),
    details: row.details
      ? row.details.slice(
          windowStart.value,
          windowStart.value + windowSize.value
        )
      : undefined,
    isReference: row.isReference,
  }))
);

function sanitizeBase(raw: string) {
  const up = (raw || "").toUpperCase();
  return colorMap[up] ? up : "N";
}

function isGapValue(raw: unknown) {
  const text = String(raw ?? "").trim();
  return !text || text === "-" || text === ".";
}

function getReferenceBase(_row: HeatmapRow, rIdx: number, cIdx: number) {
  if (referenceMode.value === "none") return null;
  if (referenceMode.value === "per-row") {
    const rowRef = visibleRowReferences.value[rIdx];
    return rowRef ? rowRef[cIdx] ?? null : null;
  }
  return visibleReferenceBases.value[cIdx] ?? null;
}

function formatNumeric(val: number) {
  if (!Number.isFinite(val)) return "NA";
  if (Math.abs(val - Math.round(val)) < 1e-6) return `${Math.round(val)}`;
  if (val === 0) return "0";
  if (val < 0.001) return val.toExponential(2);
  return val.toFixed(4);
}

function colorForNumeric(val: number | null) {
  if (val == null) return "rgba(148, 163, 184, 0.2)";
  const minVal = numericMin.value;
  const maxVal = numericMax.value;
  if (minVal < 0) {
    const maxAbs = Math.max(Math.abs(minVal), Math.abs(maxVal));
    if (maxAbs <= 0) return "rgba(148, 163, 184, 0.2)";
    const t = Math.max(-1, Math.min(1, val / maxAbs));
    return d3.interpolateRdBu(1 - (t + 1) / 2);
  }
  if (maxVal <= 0) return "rgba(148, 163, 184, 0.2)";
  const t = Math.min(1, val / maxVal);
  const hue = 210 - 80 * t;
  const light = 96 - 46 * t;
  return `hsl(${hue.toFixed(0)}, 70%, ${light.toFixed(0)}%)`;
}

const legendItems = computed(() => {
  if (mode.value === "numeric") {
    const maxVal = numericMax.value || 0;
    const minVal = numericMin.value || 0;
    const fmt = (v: number) => formatNumeric(v);
    if (minVal < 0) {
      const maxAbs = Math.max(Math.abs(minVal), Math.abs(maxVal));
      return [
        { label: fmt(-maxAbs), color: colorForNumeric(-maxAbs) },
        { label: fmt(-maxAbs / 2), color: colorForNumeric(-maxAbs / 2) },
        { label: "0", color: colorForNumeric(0) },
        { label: fmt(maxAbs / 2), color: colorForNumeric(maxAbs / 2) },
        { label: fmt(maxAbs), color: colorForNumeric(maxAbs) },
      ];
    }
    const step = maxVal / 4;
    return [
      { label: "0", color: colorForNumeric(0) },
      { label: fmt(step), color: colorForNumeric(step) },
      { label: fmt(step * 2), color: colorForNumeric(step * 2) },
      { label: fmt(step * 3), color: colorForNumeric(step * 3) },
      { label: fmt(maxVal), color: colorForNumeric(maxVal) },
    ];
  }
  if (colorScheme.value === "diff" && hasDiffReference.value) {
    return diffLegendItems;
  }
  return baseLegendItems;
});

async function fetchRegionMapping() {
  const numbers = safeIds.value;
  if (!numbers.length) return;
  try {
    const resp = await apiFetch("/regions/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ numbers }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    const map: Record<string, string | null> = {};
    data.results.forEach((item: any) => {
      map[item.input] = normalizeRegionName(item.region);
    });
    regionLookup.value = map;
  } catch (err) {
    regionLookup.value = {};
  }
}

function truncateLabel(name: string, maxChars: number) {
  const trimmed = (name || "").trim();
  if (trimmed.length <= maxChars) return trimmed || "seq";
  return `${trimmed.slice(0, Math.max(1, maxChars - 1))}…`;
}

function escapeCsv(value: string | number | null) {
  const raw = value == null ? "" : String(value);
  if (/[",\n]/.test(raw)) {
    return `"${raw.replace(/"/g, '""')}"`;
  }
  return raw;
}

function buildAlignmentCsv(ids: string[], rows: HeatmapRow[]) {
  const header = ["name", ...ids].map(escapeCsv).join(",");
  const body = rows.map((row) => {
    const cols = [row.name, ...row.values].map(escapeCsv);
    return cols.join(",");
  });
  return [header, ...body].join("\n");
}

async function fetchRegionTree() {
  if (mode.value === "numeric") {
    regionTreeNewick.value = null;
    regionTreeError.value = null;
    return;
  }
  if (selectedRegion.value === "All") {
    regionTreeNewick.value = null;
    regionTreeError.value = null;
    return;
  }
  if (filteredIds.value.length < 2 || filteredRowsRaw.value.length < 2) {
    regionTreeNewick.value = null;
    regionTreeError.value = null;
    return;
  }
  const now = Date.now();
  if (regionTreeFetchInFlight || now < regionTreeCooldownUntil) return;
  const csvText = buildAlignmentCsv(filteredIds.value, filteredRowsRaw.value);
  const key = `${selectedRegion.value}|${csvText.length}|${csvText.slice(0, 120)}`;
  if (key === lastRegionTreeKey && (regionTreeNewick.value || regionTreeError.value)) return;
  regionTreeFetchInFlight = true;
  lastRegionTreeKey = key;
  regionTreeNewick.value = null;
  regionTreeError.value = null;
  try {
    const fd = new FormData();
    fd.append("file", new Blob([csvText], { type: "text/csv" }), "region.csv");
    const res = await apiFetch("/plot/tree", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    regionTreeNewick.value = await res.text();
    regionTreeError.value = null;
  } catch (err: any) {
    regionTreeNewick.value = null;
    regionTreeError.value = err?.message || "Failed to build region tree.";
    regionTreeCooldownUntil = Date.now() + 8000;
  } finally {
    regionTreeFetchInFlight = false;
  }
}

function positionTooltip(x: number, y: number) {
  const padding = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const el = tooltipRef.value;
  const w = el?.offsetWidth ?? 240;
  const h = el?.offsetHeight ?? 80;
  let tx = x;
  let ty = y;
  if (tx + w + padding > vw) tx = vw - w - padding;
  if (ty + h + padding > vh) ty = vh - h - padding;
  if (tx < padding) tx = padding;
  if (ty < padding) ty = padding;
  tooltip.value.x = tx;
  tooltip.value.y = ty;
}

function showTooltipAt(text: string, x: number, y: number) {
  tooltip.value.visible = true;
  tooltip.value.text = text || "seq";
  nextTick(() => positionTooltip(x, y));
}

function showTooltipFromEvent(text: string, event: MouseEvent) {
  showTooltipAt(text, event.clientX + 12, event.clientY + 12);
}

function hideTooltip() {
  tooltip.value.visible = false;
}

function renderSvg() {
  const svgEl = svgRef.value;
  if (!svgEl) return;
  hideTooltip();

  const rows = visibleRows.value;
  const ids = visibleIds.value;
  const refCount = rows.filter((row) => row.isReference).length;
  const rowCount = rows.length;
  const colCount = ids.length;
  const width = Math.max(1, wrapWidth.value);
  const height = Math.max(
    1,
    topLabelHeight.value + rowCount * cellSize.value + 6
  );

  const chartText = getCssVar("--chart-text", "rgba(255,255,255,0.85)");
  const chartGrid = getCssVar("--chart-grid", "rgba(255,255,255,0.2)");
  const chartBand = getCssVar("--chart-band", "rgba(255,255,255,0.035)");
  const chartNode = getCssVar("--chart-node", "#8aa4ff");
  const chartLeaf = getCssVar("--chart-node-leaf", "#34d399");

  const svg = d3.select(svgEl);
  svg.selectAll("*").remove();
  svg
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .attr("height", height);

  if (!rowCount || !colCount) {
    return;
  }

  const cellW = heatmapWidth.value / colCount;

  const defs = svg.append("defs");
  const gapPatternId = `gap-pattern-${Math.random().toString(36).slice(2, 9)}`;
  const gapPattern = defs
    .append("pattern")
    .attr("id", gapPatternId)
    .attr("patternUnits", "userSpaceOnUse")
    .attr("width", 8)
    .attr("height", 8)
    .attr("patternTransform", "rotate(45)");
  gapPattern
    .append("rect")
    .attr("width", 8)
    .attr("height", 8)
    .attr("fill", diffGapColor);
  gapPattern
    .append("line")
    .attr("x1", 0)
    .attr("y1", 0)
    .attr("x2", 0)
    .attr("y2", 8)
    .attr("stroke", "rgba(100, 116, 139, 0.7)")
    .attr("stroke-width", 2);
  const gapFill = `url(#${gapPatternId})`;

  const treeOffsetX = leftPadding;
  const treeOffsetY = topLabelHeight.value + cellSize.value / 2;
  const treeHeight = Math.max(0, (rowCount - 1) * cellSize.value);

  const idLabelGroup = svg.append("g");
  const clipId = `label-clip-${Math.random().toString(36).slice(2, 9)}`;
  svg
    .append("clipPath")
    .attr("id", clipId)
    .append("rect")
    .attr("x", heatmapX.value)
    .attr("y", 0)
    .attr("width", heatmapWidth.value)
    .attr("height", topLabelHeight.value - 4);

  idLabelGroup.attr("clip-path", `url(#${clipId})`);

  const hoverTickGroup = svg.append("g").attr("pointer-events", "none");
  const hoverTickBg = hoverTickGroup
    .append("rect")
    .attr("rx", 4)
    .attr("ry", 4)
    .attr("fill", "rgba(255, 255, 255, 0.92)")
    .attr("stroke", "rgba(15, 23, 42, 0.35)")
    .attr("stroke-width", 1)
    .attr("opacity", 0);
  const hoverTickText = hoverTickGroup
    .append("text")
    .attr("text-anchor", "middle")
    .attr("fill", "#0f172a")
    .attr("font-size", "12px")
    .attr("font-weight", "700")
    .attr("opacity", 0);

  const regionBandHeight = 14;
  const regionBandY = 14;
  const regionGroup = svg.append("g").attr("pointer-events", "none");
  const regionPalette: Record<string, string> = {
    "AA arm 5' end": "rgba(59, 130, 246, 0.18)",
    "D loop + D stem": "rgba(14, 165, 233, 0.18)",
    "Anticodon loop + stem": "rgba(245, 158, 11, 0.18)",
    "Variable loop": "rgba(139, 92, 246, 0.18)",
    "T loop + T stem": "rgba(34, 197, 94, 0.18)",
    "AA arm 3' end": "rgba(239, 68, 68, 0.18)",
    "—": "rgba(148, 163, 184, 0.18)",
  };
  const segments: { start: number; end: number; label: string }[] = [];
  let segStart = 0;
  let segLabel = formatRegion(determineRegion(ids[0] || ""));
  for (let i = 1; i <= ids.length; i++) {
    const label = i < ids.length ? formatRegion(determineRegion(ids[i] || "")) : segLabel;
    if (label !== segLabel || i === ids.length) {
      segments.push({ start: segStart, end: i - 1, label: segLabel });
      segStart = i;
      segLabel = label;
    }
  }
  segments.forEach((seg) => {
    const x = heatmapX.value + seg.start * cellW;
    const w = Math.max(1, (seg.end - seg.start + 1) * cellW);
    regionGroup
      .append("rect")
      .attr("x", x)
      .attr("y", regionBandY)
      .attr("width", w)
      .attr("height", regionBandHeight)
      .attr("fill", regionPalette[seg.label] || regionPalette["—"])
      .attr("stroke", "rgba(148, 163, 184, 0.35)")
      .attr("stroke-width", 0.6);
    regionGroup
      .append("text")
      .attr("x", x + w / 2)
      .attr("y", regionBandY + regionBandHeight - 3)
      .attr("text-anchor", "middle")
      .attr("fill", chartText)
      .attr("font-size", "10px")
      .text(seg.label);
  });

  const topHighlight = svg
    .append("rect")
    .attr("x", heatmapX.value)
    .attr("y", 0)
    .attr("width", cellW)
    .attr("height", topLabelHeight.value - 4)
    .attr("fill", "rgba(56, 189, 248, 0.2)")
    .attr("opacity", 0)
    .attr("pointer-events", "none");

  const labelY = Math.min(
    topLabelHeight.value - 6,
    regionBandY + regionBandHeight + 26
  );

  ids.forEach((id, idx) => {
    if (idx % labelStep.value !== 0) return;
    const x = heatmapX.value + idx * cellW + 6;
    const y = labelY;
    idLabelGroup
      .append("text")
      .attr("x", x)
      .attr("y", y)
      .attr("transform", `rotate(-90, ${x}, ${y})`)
      .attr("fill", chartText)
      .attr("font-size", "11px")
      .text(id);
  });

  const gridX = labelAreaX.value;
  const gridWidth = heatmapX.value + heatmapWidth.value - labelAreaX.value;
  const rowBandGroup = svg.append("g").attr("pointer-events", "none");
  for (let r = 0; r < rowCount; r++) {
    if (r % 2 !== 0) continue;
    rowBandGroup
      .append("rect")
      .attr("x", gridX)
      .attr("y", topLabelHeight.value + r * cellSize.value)
      .attr("width", gridWidth)
      .attr("height", cellSize.value)
      .attr("fill", chartBand);
  }

  const heatmapGroup = svg
    .append("g")
    .attr("transform", `translate(${heatmapX.value}, ${topLabelHeight.value})`);

  const columnHighlight = heatmapGroup
    .append("rect")
    .attr("x", 0)
    .attr("y", 0)
    .attr("width", cellW)
    .attr("height", rowCount * cellSize.value)
    .attr("fill", "rgba(56, 189, 248, 0.16)")
    .attr("opacity", 0)
    .attr("pointer-events", "none");

  const highlightRect = svg
    .append("rect")
    .attr("fill", "none")
    .attr("stroke", chartText)
    .attr("stroke-width", 1.4)
    .attr("pointer-events", "none")
    .attr("opacity", 0);
  rows.forEach((row, rIdx) => {
    row.values.forEach((base, cIdx) => {
      let fill = colorMap[sanitizeBase(String(base ?? ""))];
      if (mode.value === "numeric") {
        const raw =
          typeof base === "number"
            ? base
            : base == null
            ? Number.NaN
            : Number(base);
        const val = Number.isNaN(raw) ? null : raw;
        fill = colorForNumeric(val);
      } else if (colorScheme.value === "diff" && hasDiffReference.value) {
        const refRaw = getReferenceBase(row, rIdx, cIdx);
        if (refRaw == null) {
          const baseLabel = sanitizeBase(String(base ?? ""));
          fill = colorMap[baseLabel];
        } else {
          const baseGap = isGapValue(base);
          const refGap = isGapValue(refRaw);
          if (baseGap || refGap) {
            fill = gapFill;
          } else {
            const refBase = sanitizeBase(String(refRaw ?? ""));
            const baseLabel = sanitizeBase(String(base ?? ""));
            fill = refBase === baseLabel ? diffMatchColor : diffChangeColor;
          }
        }
      }
      heatmapGroup
        .append("rect")
        .attr("x", cIdx * cellW)
        .attr("y", rIdx * cellSize.value)
        .attr("width", cellW)
        .attr("height", cellSize.value)
        .attr("fill", fill)
        .attr("stroke", "rgba(100, 116, 139, 0.22)")
        .attr("stroke-width", 0.5);

      const hit = heatmapGroup
        .append("rect")
        .attr("x", cIdx * cellW)
        .attr("y", rIdx * cellSize.value)
        .attr("width", cellW)
        .attr("height", cellSize.value)
        .attr("fill", "#ffffff")
        .attr("fill-opacity", 0.001);

      const id = ids[cIdx] || "";
      const region = formatRegion(determineRegion(id));
      let tooltipText = "";
      if (mode.value === "numeric") {
        const raw =
          typeof base === "number"
            ? base
            : base == null
            ? Number.NaN
            : Number(base);
        const valueText = formatNumeric(raw);
        const detailList: VariantDetail[] = row.details?.[cIdx] ?? [];
        const detailLines = detailList.map((item: VariantDetail) => {
          const ref = item.ref || "N";
          const alt = item.alt || "N";
          const pos = item.pos ? `@${item.pos}` : "";
          const trna = item.trna ? ` ${item.trna}` : "";
          const vid = item.variant || "variant";
          return `- ${vid} ${ref}>${alt}${pos}${trna}`;
        });
        const header = `Row: ${displayLabel(row) || "row"}\nCol: ${
          id || "—"
        }\n${valueLabel.value}: ${valueText}\nRegion: ${region}`;
        tooltipText = detailLines.length
          ? `${header}\nVariants (${detailLines.length}):\n${detailLines.join("\n")}`
          : header;
      } else {
        const baseLabel = sanitizeBase(String(base ?? ""));
        const refRaw = getReferenceBase(row, rIdx, cIdx);
        const refLabel = refRaw == null ? "—" : sanitizeBase(String(refRaw ?? ""));
        const colIndex = windowStart.value + cIdx + 1;
        if (colorScheme.value === "diff" && hasDiffReference.value && refRaw != null) {
          const baseGap = isGapValue(base);
          const refGap = isGapValue(refRaw);
          const status = baseGap || refGap ? "gap" : refLabel === baseLabel ? "match" : "changed";
          tooltipText = `Col: ${colIndex} (ID: ${id || "—"})
Region: ${region}
Ref: ${refLabel}
Target: ${baseLabel}
Status: ${status}
Seq: ${row.name || "seq"}`;
        } else {
          tooltipText = `Col: ${colIndex} (ID: ${id || "—"})
Region: ${region}
Ref: ${refLabel}
Target: ${baseLabel}
Seq: ${row.name || "seq"}`;
        }
      }
      hit
        .on("mouseenter", (event: any) => {
          const x = heatmapX.value + cIdx * cellW;
          highlightRect
            .attr("x", x)
            .attr("y", topLabelHeight.value + rIdx * cellSize.value)
            .attr("width", cellW)
            .attr("height", cellSize.value)
            .attr("opacity", 1);
          columnHighlight
            .attr("x", cIdx * cellW)
            .attr("width", cellW)
            .attr("opacity", 0.8);
          topHighlight
            .attr("x", x)
            .attr("width", cellW)
            .attr("opacity", 1);
          const colIndex = windowStart.value + cIdx + 1;
          const tickLabel = id || `#${colIndex}`;
          const tickX = x + cellW / 2;
          const tickY = labelY - 2;
          hoverTickText
            .attr("x", tickX)
            .attr("y", tickY)
            .attr("transform", null)
            .text(tickLabel)
            .attr("opacity", 1);
          const bbox = hoverTickText.node()?.getBBox?.();
          if (bbox) {
            const padX = 6;
            const padY = 4;
            hoverTickBg
              .attr("x", bbox.x - padX)
              .attr("y", bbox.y - padY)
              .attr("width", bbox.width + padX * 2)
              .attr("height", bbox.height + padY * 2)
              .attr("opacity", 1);
          }
          showTooltipFromEvent(tooltipText, event);
        })
        .on("mousemove", (event: any) =>
          showTooltipFromEvent(tooltipText, event)
        )
        .on("mouseleave", () => {
          highlightRect.attr("opacity", 0);
          columnHighlight.attr("opacity", 0);
          topHighlight.attr("opacity", 0);
          hoverTickText.attr("opacity", 0);
          hoverTickBg.attr("opacity", 0);
          hideTooltip();
        });
    });
  });
  const colLineGroup = svg.append("g");
  colLineGroup.attr("pointer-events", "none");
  for (let c = 0; c <= colCount; c++) {
    const x = heatmapX.value + c * cellW;
    colLineGroup
      .append("line")
      .attr("x1", x)
      .attr("y1", topLabelHeight.value)
      .attr("x2", x)
      .attr("y2", topLabelHeight.value + rowCount * cellSize.value)
      .attr("stroke", "rgba(148, 163, 184, 0.25)")
      .attr("stroke-width", 0.6);
  }

  const regionLineGroup = svg.append("g");
  regionLineGroup.attr("pointer-events", "none");
  segments.slice(1).forEach((seg) => {
    const x = heatmapX.value + seg.start * cellW;
    regionLineGroup
      .append("line")
      .attr("x1", x)
      .attr("y1", topLabelHeight.value)
      .attr("x2", x)
      .attr("y2", topLabelHeight.value + rowCount * cellSize.value)
      .attr("stroke", "rgba(71, 85, 105, 0.7)")
      .attr("stroke-width", 1.4);
  });

  const rowLineGroup = svg.append("g");
  rowLineGroup.attr("pointer-events", "none");
  for (let r = 0; r <= rowCount; r++) {
    const y = topLabelHeight.value + r * cellSize.value;
    rowLineGroup
      .append("line")
      .attr("x1", gridX)
      .attr("y1", y)
      .attr("x2", gridX + gridWidth)
      .attr("y2", y)
      .attr("stroke", chartGrid)
      .attr("stroke-width", 1);
  }

  const treeNewick = effectiveTreeNewick.value;
  const leafPos = new Map<string, { x: number; y: number }>();
  let treeAvailable = false;
  if (treeNewick) {
    try {
      const treeData = parseNewick(treeNewick);
      const root = d3.hierarchy(treeData, (d: any) => d.children);
      const cluster = d3.cluster().size([treeHeight, treeWidth.value]);
      cluster(root);

      const hasLengths = root
        .descendants()
        .some((node: any) => typeof node.data.length === "number");
      if (hasLengths) {
        root.each((node: any) => {
          const parent = node.parent as any | null;
          const parentY = parent ? (parent as any).y : 0;
          const len =
            typeof node.data.length === "number" ? node.data.length : 0;
          (node as any).y = parentY + len;
        });
        const maxY =
          d3.max(root.descendants(), (node: any) => (node as any).y) || 1;
        const scale = d3
          .scaleLinear()
          .domain([0, maxY])
          .range([0, treeWidth.value]);
        root.each((node: any) => {
          (node as any).y = scale((node as any).y);
        });
      }

      const treeGroup = svg
        .append("g")
        .attr("class", "phylo-tree")
        .attr("transform", `translate(${treeOffsetX}, ${treeOffsetY})`);

      const linkGen = d3
        .linkHorizontal()
        .x((d: any) => d.y)
        .y((d: any) => d.x);

      const linkSelection = treeGroup
        .selectAll("path")
        .data(root.links())
        .enter()
        .append("path")
        .attr("class", "tree-link")
        .attr("d", linkGen as any)
        .attr("fill", "none")
        .attr("stroke", chartGrid)
        .attr("stroke-width", 1);

      const nodes = treeGroup
        .selectAll("g")
        .data(root.descendants())
        .enter()
        .append("g")
        .attr("class", "tree-node")
        .attr("transform", (d: any) => `translate(${(d as any).y},${d.x})`);

      nodes
        .append("circle")
        .attr("r", (d: any) => (d.children ? 2.4 : 3.2))
        .attr("fill", (d: any) => (d.children ? chartNode : chartLeaf));

      const rowIndexMap = new Map<string, number>();
      rows
        .filter((row) => !row.isReference)
        .forEach((row, idx) => {
          const key = normalizeLabel(row.name);
          if (!rowIndexMap.has(key)) rowIndexMap.set(key, idx + refCount);
        });

      const leaves = root.leaves();
      leaves.forEach((leaf: any) => {
        const key = normalizeLabel(leaf.data.name || "");
        const idx = rowIndexMap.get(key);
        if (idx !== undefined) {
          (leaf as any).x = idx * cellSize.value;
        }
      });
      root.eachAfter((node: any) => {
        if (node.children && node.children.length) {
          const avg =
            d3.mean(node.children, (child: any) => child.x) ?? (node as any).x;
          (node as any).x = avg;
        }
      });

      nodes.attr("transform", (d: any) => `translate(${(d as any).y},${d.x})`);
      linkSelection.attr("d", linkGen as any);

      leaves.forEach((leaf: any) => {
        const key = normalizeLabel(leaf.data.name || "");
        const y = treeOffsetY + (leaf as any).x;
        const x = treeOffsetX + (leaf as any).y;
        leafPos.set(key, { x, y });
      });

      treeAvailable = true;
    } catch {
      treeAvailable = false;
    }
  }

  const labelClipId = `leaf-label-clip-${Math.random()
    .toString(36)
    .slice(2, 9)}`;
  svg
    .append("clipPath")
    .attr("id", labelClipId)
    .append("rect")
    .attr("x", labelAreaX.value)
    .attr("y", 0)
    .attr("width", labelWidth.value)
    .attr("height", height);

  const labelGroup = svg.append("g").attr("clip-path", `url(#${labelClipId})`);

  const rowFont = Math.max(11, Math.min(14, cellSize.value - 2));
  rows.forEach((row, idx) => {
    const key = normalizeLabel(row.name);
    const y =
      leafPos.get(key)?.y ??
      topLabelHeight.value + (idx + 0.5) * cellSize.value;
    const labelX = labelAreaX.value + labelWidth.value - 6;
    labelGroup
      .append("text")
      .attr("x", labelX)
      .attr("y", y)
      .attr("text-anchor", "end")
      .attr("dominant-baseline", "middle")
      .attr("fill", chartText)
      .attr("font-size", `${rowFont}px`)
      .attr("font-weight", row.isReference ? "600" : "400")
      .text(truncateLabel(displayLabel(row), 30));

    const hit = labelGroup
      .append("rect")
      .attr("x", labelAreaX.value)
      .attr("y", y - cellSize.value / 2)
      .attr("width", labelWidth.value)
      .attr("height", cellSize.value)
      .attr("fill", "#ffffff")
      .attr("fill-opacity", 0.001)
      .attr("pointer-events", "all");

    hit
      .on("mouseenter", (event: any) => showTooltipFromEvent(row.name, event))
      .on("mousemove", (event: any) => showTooltipFromEvent(row.name, event))
      .on("mouseleave", hideTooltip);
  });

  if (treeAvailable) {
    const connectorGroup = svg
      .append("g")
      .attr("class", "tree-connectors")
      .attr("pointer-events", "none");
    rows.forEach((row, idx) => {
      if (row.isReference) return;
      const key = normalizeLabel(row.name);
      const pos = leafPos.get(key);
      const y = pos?.y ?? topLabelHeight.value + (idx + 0.5) * cellSize.value;
      const x1 = pos?.x ?? treeOffsetX + treeWidth.value;
      const x2 = labelAreaX.value - 4;
      connectorGroup
        .append("line")
        .attr("class", "tree-connector")
        .attr("x1", x1)
        .attr("y1", y)
        .attr("x2", x2)
        .attr("y2", y)
        .attr("stroke", chartGrid)
        .attr("stroke-width", 0.6);
    });
  }
}

let rafId: number | null = null;
function scheduleRender() {
  if (rafId !== null) return;
  rafId = window.requestAnimationFrame(() => {
    rafId = null;
    renderSvg();
  });
}

watch(
  () => activeIds.value.join(","),
  () => {
    if (!activeIds.value.length) return;
    windowSize.value = maxWindow.value;
    windowStart.value = 0;
  },
  { immediate: true }
);

watch(
  () => activeIds.value.length,
  (len) => {
    if (!len) return;
    if (windowSize.value > len) windowSize.value = len;
    if (windowSize.value < minWindow.value)
      windowSize.value = Math.min(minWindow.value, len);
    if (windowStart.value > maxStart.value) windowStart.value = maxStart.value;
  }
);

watch(
  () => windowSize.value,
  () => {
    if (windowStart.value > maxStart.value) windowStart.value = maxStart.value;
  }
);

watch(
  () => [
    effectiveTreeNewick.value,
    regionTreeError.value,
    activeIds.value,
    filteredRowsRaw.value,
    windowStart.value,
    windowSize.value,
    cellSize.value,
    wrapWidth.value,
    useClusterOrder.value,
    selectedRegion.value,
    mode.value,
    numericMax.value,
    colorScheme.value,
    referenceMode.value,
    visibleReferenceBases.value,
    visibleRowReferences.value,
  ],
  async () => {
    await nextTick();
    scheduleRender();
  },
  { deep: true }
);

onMounted(() => {
  const updateWidth = () => {
    wrapWidth.value = wrapRef.value?.clientWidth || 0;
  };
  updateWidth();
  const observer = new ResizeObserver(updateWidth);
  if (wrapRef.value) observer.observe(wrapRef.value);
  resizeObserver = observer;
  fetchRegionMapping();
  renderSvg();
});

watch(
  () => safeIds.value.join(","),
  () => {
    fetchRegionMapping();
  }
);

watch(
  () => [selectedRegion.value, filteredIds.value.join(","), safeRows.value],
  () => {
    fetchRegionTree();
  }
);

watch(
  () => effectiveTheme.value,
  () => {
    scheduleRender();
  }
);

let resizeObserver: ResizeObserver | null = null;

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  if (rafId !== null) window.cancelAnimationFrame(rafId);
});
</script>

<style scoped>
.tree-heatmap {
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  padding: 0.75rem;
  background: var(--surface-2);
}
.heatmap-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.25rem;
}
.heatmap-title {
  color: var(--text-soft);
  font-weight: 600;
  font-size: 1rem;
}
.heatmap-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  color: var(--text-muted);
  font-size: 0.92rem;
}
.control {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.control-colors {
  gap: 0.5rem;
}
.color-toggle {
  display: inline-flex;
  gap: 6px;
  padding: 2px;
  border-radius: 999px;
  background: var(--surface-3);
  border: 1px solid var(--border-soft);
}
.color-btn {
  border: none;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  cursor: pointer;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.8rem;
}
.color-btn.base.active {
  background: var(--info);
  color: #fff;
  border: 1px solid rgba(var(--info-rgb), 0.65);
  box-shadow: 0 0 0 2px rgba(var(--info-rgb), 0.2);
}
.color-btn.diff.active {
  background: var(--danger);
  color: #fff;
  border: 1px solid rgba(var(--danger-rgb), 0.65);
  box-shadow: 0 0 0 2px rgba(var(--danger-rgb), 0.2);
}
.control input[type="range"] {
  width: 140px;
}
.control-select {
  background: var(--field-bg);
  color: var(--field-text);
  border: 1px solid var(--field-border);
  border-radius: 6px;
  padding: 0.15rem 1.35rem 0.15rem 0.4rem;
}
.control-toggle input {
  accent-color: var(--success);
}
.control-value {
  min-width: 32px;
  text-align: right;
}
.heatmap-legend {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 0.88rem;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}
.legend-swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  border: 1px solid var(--border-soft);
}
.combo-body {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.combo-canvas {
  width: 100%;
  overflow: hidden;
  position: relative;
}
.combo-canvas svg {
  display: block;
}
.svg-tooltip {
  position: fixed;
  z-index: 9999;
  padding: 6px 8px;
  background: var(--surface-dark);
  color: var(--overlay-text, #f8fafc);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  font-size: 0.85rem;
  pointer-events: none;
  max-width: 280px;
  max-height: 240px;
  overflow-y: auto;
  white-space: pre-line;
  line-height: 1.3;
}
@media (max-width: 768px) {
  .tree-heatmap {
    padding: 0.5rem;
  }
  .combo-canvas {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
