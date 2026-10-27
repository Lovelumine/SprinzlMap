<template>
  <section class="preview-card" :class="{ 'variant-hero': props.variant === 'hero' }">
    <div v-if="props.variant === 'hero'" class="run-preview">
      <div class="run-input">
        <div class="run-label">FASTA input</div>
        <div class="seq-lines" aria-hidden="true">
          <span>&gt;acidHosp1:Ala-TGC</span>
          <span>GGGCCGGUAGCUCAGCCUGGAAGAGUGUCCGGCUUGCAACCG...</span>
        </div>
      </div>
      <div class="run-controls">
        <div class="mode-tabs" aria-label="Alignment mode preview">
          <span class="mode-tab">DP</span>
          <span class="mode-tab active">Agent</span>
        </div>
        <span class="status-chip">Ready</span>
      </div>
    </div>

    <header class="preview-header">
      <div class="header-left">
        <div class="mode-badge">{{ data.alignment_mode }}</div>
        <div class="result-title">
          <h3>{{ data.seq_name }}</h3>
          <span class="template-tag">
            {{ data.organism }} &middot; {{ data.isotype }} &middot; Template: {{ data.template_name }}
          </span>
        </div>
      </div>
      <div class="header-right">
        <div class="header-stat">
          <span class="stat-val">{{ matchCount }}</span>
          <span class="stat-label">match</span>
        </div>
        <div class="header-stat">
          <span class="stat-val mismatch">{{ mismatchCount }}</span>
          <span class="stat-label">mismatch</span>
        </div>
        <div class="header-stat">
          <span class="stat-val insertion">{{ insertionCount }}</span>
          <span class="stat-label">insertion</span>
        </div>
        <div class="header-stat">
          <span class="stat-val deletion">{{ deletionCount }}</span>
          <span class="stat-label">deletion</span>
        </div>
      </div>
    </header>

    <div class="preview-main">
      <div class="table-area">
        <div class="region-legend">
          <span v-for="r in regionDefs" :key="r.name" class="region-chip" :style="{ '--rc': r.color }">
            {{ r.name }}
          </span>
        </div>

        <div class="real-table-panel">
          <AlignmentTable
            class="real-alignment-table"
            :alignment-data="displayedAlignmentNodes"
            :chunk-size="previewChunkSize"
            :editable="false"
            :show-origin="true"
            color-mode="compare"
            id-label="Sprinzl"
            origin-label="Template"
            target-label="Target"
          />
        </div>

        <div class="color-legend">
          <span class="legend-item"><i class="dot match"></i> Match</span>
          <span class="legend-item"><i class="dot mismatch"></i> Mismatch</span>
          <span class="legend-item"><i class="dot insertion"></i> Insertion</span>
          <span class="legend-item"><i class="dot deletion"></i> Deletion</span>
        </div>
      </div>

      <div class="structure-area">
        <div class="structure-head">
          <div class="structure-title">Secondary Structure</div>
          <div class="structure-meta">tRNAscan-SE &middot; {{ data.structure_pairs.length }} pairs</div>
        </div>
        <SupTrnaRadial
          class="real-sprinzl-map"
          :data="radialNodes"
          :pair-links="pairLinks"
          color-mode="compare"
          :editable="false"
          :width="mapSize"
          :height="mapSize"
          :r="12"
        />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AlignmentTable from "@/components/AlignmentTable.vue";
import SupTrnaRadial from "@/components/supTrnaRadial.vue";
import demoData from "./demo-alignment-data.json";

const data = demoData;

const props = withDefaults(defineProps<{ variant?: "default" | "hero" }>(), {
  variant: "default",
});

const viewportWidth = ref(1024);

const syncViewportWidth = () => {
  if (typeof window !== "undefined") {
    viewportWidth.value = window.innerWidth;
  }
};

onMounted(() => {
  syncViewportWidth();
  window.addEventListener("resize", syncViewportWidth);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", syncViewportWidth);
});

type PreviewCell = {
  id: string;
  base: string;
  target: string;
  kind: "is-match" | "is-mismatch" | "is-insertion" | "is-deletion";
};

const cells = computed<PreviewCell[]>(() => {
  const refById = new Map(data.nums.map((id, i) => [id, data.ref[i] || "-"]));
  return data.nums.map((id, i) => {
    const base = refById.get(id) || "-";
    const target = data.pred[i] || "-";
    let kind: PreviewCell["kind"] = "is-match";
    if (base === "-" && target !== "-") kind = "is-insertion";
    else if (base !== "-" && target === "-") kind = "is-deletion";
    else if (base !== target) kind = "is-mismatch";
    return { id, base, target, kind };
  });
});

const matchCount = computed(() => cells.value.filter(c => c.kind === "is-match").length);
const mismatchCount = computed(() => cells.value.filter(c => c.kind === "is-mismatch").length);
const insertionCount = computed(() => cells.value.filter(c => c.kind === "is-insertion").length);
const deletionCount = computed(() => cells.value.filter(c => c.kind === "is-deletion").length);

const radialNodes = computed(() =>
  data.nums.map((id, i) => ({
    id,
    base: data.ref[i] || "-",
    sup_base: data.pred[i] || "-",
  }))
);

const displayedAlignmentNodes = computed(() =>
  props.variant === "hero" ? radialNodes.value : radialNodes.value
);

const previewChunkSize = computed(() => {
  if (props.variant !== "hero") return 24;
  return viewportWidth.value <= 1280 ? 8 : 10;
});

const pairLinks = computed(() =>
  data.structure_pairs.map((pair) => ({
    left_id: pair.left,
    right_id: pair.right,
  }))
);

const mapSize = computed(() => (props.variant === "hero" ? 360 : 460));

const regionDefs = [
  { name: "Acceptor", color: "#0f8f8c", ids: new Set(["-1","1","2","3","4","5","6","7","66","67","68","69","70","71","72","73","74","75","76"]) },
  { name: "D-arm", color: "#1f9d68", ids: new Set(["10","11","12","13","22","23","24","25"]) },
  { name: "Anticodon", color: "#f59e0b", ids: new Set(["27","28","29","30","31","32","33","34","35","36","37","38","39","40","41","42","43","44"]) },
  { name: "Variable", color: "#8b5cf6", ids: new Set(["45","V1","V2","V3","V4","V5","V11","V12","V13","V14","V15","V16","V17","V21","V22","V23","V24","V25","V26","V27"]) },
  { name: "T-arm", color: "#3578c8", ids: new Set(["46","47","48","49","50","51","52","53","54","55","56","57","58","59","60","61","62","63","64","65"]) },
];

</script>

<style scoped>
.preview-card {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
  max-width: 100%;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface-1);
  box-shadow: var(--shadow-soft);
  padding: 24px;
}

.run-preview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: stretch;
  padding: 12px;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: rgba(var(--card-bg-rgb), 0.72);
}

.run-input {
  min-width: 0;
}

.run-label {
  margin-bottom: 6px;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text-muted);
  text-transform: uppercase;
}

.seq-lines {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.72rem;
  line-height: 1.35;
  color: var(--text-soft);
}

.seq-lines span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-controls {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 10px;
  align-items: flex-end;
}

.mode-tabs {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  background: var(--surface-2);
}

.mode-tab {
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 0.68rem;
  font-weight: 700;
  color: var(--text-muted);
}

.mode-tab.active {
  background: var(--accent);
  color: #ffffff;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 9px;
  border-radius: 999px;
  background: rgba(var(--success-rgb), 0.12);
  color: var(--success-strong);
  font-size: 0.7rem;
  font-weight: 700;
}

.preview-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.mode-badge {
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.14);
  color: var(--accent-strong);
  text-transform: uppercase;
}

.result-title h3 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
}

.template-tag {
  font-size: 0.82rem;
  color: var(--text-muted);
}

.header-right {
  display: flex;
  gap: 20px;
}

.header-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.stat-val {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--success);
}
.stat-val.mismatch { color: var(--danger); }
.stat-val.insertion { color: var(--accent-strong); }
.stat-val.deletion { color: #f59e0b; }

.stat-label {
  font-size: 0.7rem;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.preview-main {
  display: grid;
  grid-template-columns: minmax(0, 0.78fr) minmax(340px, 1fr);
  gap: 20px;
  align-items: start;
}

.table-area {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.region-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.region-chip {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--rc) 14%, transparent);
  color: var(--rc);
  border: 1px solid color-mix(in srgb, var(--rc) 30%, transparent);
}

.real-table-panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: rgba(var(--card-bg-rgb), 0.72);
}

.real-table-panel :deep(.alignment-wrappers) {
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px;
  overflow-x: hidden;
  overflow-y: auto;
  --label-width: 76px;
}

.real-table-panel :deep(.alignment-table) {
  flex: 0 0 auto;
  background: transparent;
  backdrop-filter: none;
}

.real-table-panel :deep(.alignment-table th),
.real-table-panel :deep(.alignment-table td) {
  padding: 4px 6px;
  font-size: 0.72rem;
}

.preview-blocks {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.preview-block {
  overflow-x: auto;
  border-radius: 12px;
  border: 1px solid var(--border-soft);
  background: rgba(var(--card-bg-rgb), 0.72);
}

.region-bar {
  display: flex;
}

.region-cell {
  display: block;
  min-width: 36px;
  height: 4px;
  flex-shrink: 0;
}

.region-cell:first-child {
  min-width: 72px;
}

.preview-block table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.76rem;
}

.preview-block th,
.preview-block td {
  padding: 6px 8px;
  text-align: center;
  border-bottom: 1px solid var(--border-soft);
  border-right: 1px solid var(--border-soft);
  white-space: nowrap;
}

.row-label {
  position: sticky;
  left: 0;
  z-index: 1;
  background: rgba(var(--card-bg-rgb), 0.96);
  font-size: 0.7rem;
  color: var(--text-muted);
  text-align: right !important;
  padding-right: 10px !important;
  min-width: 64px;
}

.preview-block thead th {
  background: rgba(var(--card-bg-rgb), 0.96);
  color: var(--text-soft);
  font-size: 0.7rem;
}

.preview-block tbody tr:last-child th,
.preview-block tbody tr:last-child td {
  border-bottom: none;
}

.gap-cell {
  color: var(--text-muted);
  opacity: 0.5;
}

.is-match { color: var(--text-strong); }
.is-mismatch {
  background: rgba(var(--danger-rgb), 0.12);
  color: var(--danger);
  font-weight: 600;
}
.is-insertion {
  background: rgba(var(--success-rgb), 0.14);
  color: var(--success-strong);
  font-weight: 600;
}
.is-deletion {
  background: rgba(245,158,11,0.14);
  color: #d97706;
  font-weight: 600;
}

.color-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding-top: 4px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.76rem;
  color: var(--text-muted);
}

.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.dot.match { background: rgba(var(--success-rgb), 0.5); }
.dot.mismatch { background: rgba(var(--danger-rgb), 0.5); }
.dot.insertion { background: rgba(var(--accent-rgb), 0.5); }
.dot.deletion { background: rgba(245,158,11,0.5); }

.structure-area {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: rgba(var(--card-bg-rgb), 0.72);
  overflow: hidden;
}

.structure-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.structure-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--text-strong);
}

.real-sprinzl-map {
  width: 100%;
}

.structure-meta {
  font-size: 0.72rem;
  color: var(--text-muted);
  white-space: nowrap;
}

.real-sprinzl-map :deep(.trna-container) {
  width: 100%;
  background: transparent;
  padding: 0;
}

.real-sprinzl-map :deep(svg) {
  width: 100%;
  max-width: 100%;
  height: auto;
}

@media (max-width: 720px) {
  .preview-card {
    padding: 16px;
  }

  .preview-main {
    grid-template-columns: 1fr;
  }

  .structure-area {
    order: -1;
  }

  .header-right {
    width: 100%;
    justify-content: flex-start;
    gap: 16px;
  }
}

@media (max-width: 640px) {
  .run-preview {
    grid-template-columns: 1fr;
  }

  .run-controls {
    align-items: stretch;
  }

  .mode-tabs {
    width: 100%;
  }

  .mode-tab {
    flex: 1;
    text-align: center;
  }
}

/* ── Hero variant (compact) ── */
.variant-hero {
  border-radius: 12px;
  padding: 16px;
  gap: 14px;
  max-height: none;
  overflow: hidden;
}

.variant-hero .preview-header {
  gap: 10px;
}

.variant-hero .result-title h3 {
  font-size: 0.9rem;
}

.variant-hero .template-tag {
  font-size: 0.72rem;
}

.variant-hero .header-stat .stat-val {
  font-size: 1rem;
}

.variant-hero .header-stat .stat-label {
  font-size: 0.65rem;
}

.variant-hero .preview-main {
  grid-template-columns: minmax(0, 0.86fr) minmax(0, 1fr);
  gap: 14px;
}

.variant-hero .preview-blocks {
  gap: 6px;
}

.variant-hero .real-table-panel :deep(.alignment-wrappers) {
  max-height: 360px;
  overflow-x: hidden;
  overflow-y: auto;
}

.variant-hero .structure-area {
  order: 0;
}

.variant-hero .real-sprinzl-map {
  max-width: 430px;
  align-self: center;
}

.variant-hero .preview-block table {
  font-size: 0.68rem;
}

.variant-hero .preview-block th,
.variant-hero .preview-block td {
  padding: 4px 6px;
}

.variant-hero .color-legend {
  gap: 10px;
}

.variant-hero .legend-item {
  font-size: 0.68rem;
}

@media (max-width: 960px) {
  .variant-hero {
    max-height: none;
    overflow: visible;
  }

  .variant-hero .preview-main {
    grid-template-columns: 1fr;
  }

  .variant-hero .structure-area {
    order: -1;
    min-width: 0;
    overflow: hidden;
  }

  .variant-hero .real-sprinzl-map {
    max-width: min(430px, 100%);
  }

  .variant-hero .real-table-panel {
    overflow-x: auto;
  }

  .variant-hero .real-table-panel :deep(.alignment-wrappers) {
    max-height: none;
    overflow-x: auto;
    overflow-y: visible;
  }
}
</style>
