<template>
  <div ref="containerRef" class="trna-container" style="position: relative">
    <!-- Main SVG chart -->
    <svg
      :width="width"
      :height="height"
      :viewBox="`0 0 ${width} ${height}`"
      preserveAspectRatio="xMidYMid meet"
    >
      <g
        font-size="12"
        text-anchor="middle"
        stroke="#333"
        stroke-width="1"
        :transform="groupMatrix"
      >
        <g class="pair-layer">
          <line
            v-for="seg in pairSegments"
            :key="seg.key"
            :x1="seg.x1"
            :y1="seg.y1"
            :x2="seg.x2"
            :y2="seg.y2"
            :class="['pair-line', seg.active ? 'pair-line-active' : 'pair-line-inactive']"
          />
        </g>
        <g
          v-for="node in nodes"
          :key="node.id"
          :class="['base-group', { 'base-clickable': editable && node.hasData }]"
          @mouseenter="onNodeEnter($event, node)"
          @mousemove="onNodeMove($event)"
          @mouseleave="hoverNode = null"
          @click="handleNodeClick(node)"
        >
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="r"
            :class="[
              node.colorClass,
              {
                'base-hovered': hoverNode && hoverNode.id === node.id,
                'base-selected': selectedId && selectedId === node.id,
                'overflow-node-compare': node.isOverflow && isCompareMode,
                'overflow-node-target': node.isOverflow && !isCompareMode,
              },
            ]"
            fill="#fff"
          />
          <text :x="node.x" :y="node.y + 4">{{ node.sup_base }}</text>
        </g>
      </g>
    </svg>

    <!-- Tooltip -->
    <div
      v-if="hoverNode"
      ref="tooltipRef"
      class="tooltip"
      :style="tooltipStyle"
    >
      <div><strong>Position:</strong> {{ hoverNode.id }}</div>
      <div><strong>Pair:</strong> {{ getPairPartner(hoverNode.id) }}</div>
      <div>
        <strong>{{ isCompareMode ? "Type" : "Target base" }}:</strong>
        {{ formatNodeType(hoverNode) }}
      </div>
      <div>
        <strong>{{ isCompareMode ? "Mapping" : "Value" }}:</strong>
        {{ formatNodeMapping(hoverNode) }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from "vue";
import { SPRINZL_POSITIONS } from "./layout/sprinzlLayout";

// 接收外部传入的 JSON 已解析数组：[{ id, base, sup_base }, ...]
const props = defineProps({
  data: { type: Array, default: () => [] },
  pairLinks: { type: Array, default: () => [] },
  width: { type: Number, default: 520 },
  height: { type: Number, default: 900 },
  r: { type: Number, default: 12 },
  xOffset: { type: Number, default: 0 },
  yOffset: { type: Number, default: 40 },
  editable: { type: Boolean, default: false },
  colorMode: { type: String, default: "compare" },
  selectedId: { type: String, default: "" },
});

const emit = defineEmits(["node-click"]);
const isCompareMode = computed(() => props.colorMode !== "targetOnly");

function handleNodeClick(node) {
  if (!props.editable || !node?.hasData) return;
  emit("node-click", node.id);
}

function normalizeBase(base) {
  const b = String(base || "-").trim().toUpperCase();
  if (b === "T") return "U";
  if (b === "A" || b === "C" || b === "G" || b === "U") return b;
  return "-";
}

function getCompareType(base, supBase) {
  const ref = normalizeBase(base);
  const sup = normalizeBase(supBase);
  if (ref === "-" && sup === "-") return "gap";
  if (ref === "-" && sup !== "-") return "insertion";
  if (ref !== "-" && sup === "-") return "deletion";
  if (ref === sup) return "match";
  return "mismatch";
}

function getTargetBaseClass(supBase) {
  const base = normalizeBase(supBase);
  if (base === "A") return "target-a";
  if (base === "C") return "target-c";
  if (base === "G") return "target-g";
  if (base === "U") return "target-u";
  return "target-gap";
}

function getCompareClass(type) {
  if (type === "gap") return "gap-node";
  if (type === "insertion") return "insertion-node";
  if (type === "deletion") return "deletion-node";
  if (type === "mismatch") return "mismatch-node";
  return "match-node";
}

function getNodeVisual(base, supBase) {
  const type = getCompareType(base, supBase);
  const colorClass = isCompareMode.value
    ? getCompareClass(type)
    : getTargetBaseClass(supBase);
  return { type, colorClass };
}

// --- 让整组 <g> 自动平移/缩放以适配画布 ---
const fit = computed(() => {
  if (!nodes.value.length) return { s: 1, tx: 0, ty: 0 };
  const pad = 24; // 画布四周留白
  const r = props.r || 12; // 半径也要计入边界

  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity;
  for (const n of nodes.value) {
    if (!n) continue;
    minX = Math.min(minX, n.x - r);
    minY = Math.min(minY, n.y - r);
    maxX = Math.max(maxX, n.x + r);
    maxY = Math.max(maxY, n.y + r);
  }
  if (!isFinite(minX)) return { s: 1, tx: 0, ty: 0 };

  const rawW = Math.max(1, maxX - minX);
  const rawH = Math.max(1, maxY - minY);

  // 只在内容超出时缩小；能放下就不缩放，只平移
  const s = Math.min(
    1,
    (props.width - 2 * pad) / rawW,
    (props.height - 2 * pad) / rawH,
  );

  const contentW = rawW * s;
  const contentH = rawH * s;

  // 居中 + 平移，使左上角 ≥ pad
  const tx = (props.width - contentW) / 2 - s * minX + (props.xOffset || 0);
  const ty = (props.height - contentH) / 2 - s * minY;

  return { s, tx, ty };
});

// 应用到 <g> 的矩阵（避免 transform 顺序歧义）
const groupMatrix = computed(() => {
  const { s, tx, ty } = fit.value;
  return `matrix(${s},0,0,${s},${tx},${ty})`;
});

// 把模型坐标转换成屏幕坐标（给 tooltip 用）
function toScreen(n) {
  const { s, tx, ty } = fit.value;
  return { x: n.x * s + tx, y: n.y * s + ty };
}

// 1. 固定的 canonical 坐标（共享布局模块）
const positions = SPRINZL_POSITIONS;

// 2. 相邻点手动映射（支持环回）
const nextMap = {
  V17: "V1",
  45: "V11",
  V5: "V27",
  V21: "46",
  20: "20a",
  "20a": "20b",
  "20b": "21",
  "-1": "1",
  17: "17a",
  "17a": "18",
};
const mirrorSet = new Set([
  "42",
  "43",
  "44",
  "45",
  "V11",
  "V12",
  "47",
  "48",
  "49",
  "50",
]);
const insertionRe = /^(-?\d+[A-Za-z]?|V\d+)i(\d+)$/;

// 3. 计算节点，包括 canonical 与 insertion
const nodes = computed(() => {
  const list = [];
  const dataById = new Map((props.data || []).map((d) => [String(d.id || ""), d]));
  // canonical
  for (const [id, pos] of Object.entries(positions)) {
    const entry = dataById.get(id);
    const base = entry ? entry.base : "-";
    const sup = entry ? entry.sup_base : "-";
    const { type, colorClass } = getNodeVisual(base, sup);
    list.push({
      id,
      base,
      sup_base: sup,
      type,
      colorClass,
      x: pos.x,
      y: pos.y,
      isOverflow: false,
      hasData: Boolean(entry),
    });
  }
  // insertion
  props.data
    .filter((d) => insertionRe.test(String(d.id || "")))
    .forEach((item) => {
      const [, num, idx] = String(item.id || "").match(insertionRe);
      const p1 = positions[num];
      if (!p1) return console.warn(`no canonical for ${item.id}`);
      // 优先通过 nextMap 获取环回或下一个点
      const numericAnchor = /^-?\d+$/.test(num);
      const nextKey = nextMap[num] ?? (numericAnchor ? String(+num + 1) : "");
      const p2 = positions[nextKey];
      let dx, dy, mx, my;
      if (p2) {
        dx = p2.x - p1.x;
        dy = p2.y - p1.y;
        mx = (p1.x + p2.x) / 2;
        my = (p1.y + p2.y) / 2;
      } else {
        const p0 = numericAnchor ? positions[String(+num - 1)] : null;
        if (p0) {
          dx = p1.x - p0.x;
          dy = p1.y - p0.y;
        } else {
          dx = 18;
          dy = 0;
        }
        mx = p1.x;
        my = p1.y;
      }
      const dist = Math.hypot(dx, dy) || 1;
      const ux = -dy / dist,
        uy = dx / dist;
      const offset = 20 * +idx;
      // 判断是否镜像
      const direction = mirrorSet.has(num) ? -1 : 1;
      const x = mx + ux * offset * direction;
      const y = my + uy * offset * direction;
      const isOverflow = true;
      const { type, colorClass } = getNodeVisual(item.base, item.sup_base);
      list.push({
        id: item.id,
        base: item.base,
        sup_base: item.sup_base,
        type,
        colorClass,
        x,
        y,
        isOverflow,
        hasData: true,
      });
    });
  return list;
});

const pairPartnerMap = computed(() => {
  const mp = new Map();
  (props.pairLinks || []).forEach((pair) => {
    const left = String(pair?.left_id || pair?.leftId || "").trim();
    const right = String(pair?.right_id || pair?.rightId || "").trim();
    if (!left || !right || left === right) return;
    if (!mp.has(left)) mp.set(left, new Set());
    if (!mp.has(right)) mp.set(right, new Set());
    mp.get(left).add(right);
    mp.get(right).add(left);
  });
  return mp;
});

const pairSegments = computed(() => {
  const byId = new Map(nodes.value.map((n) => [n.id, n]));
  const byDataId = new Map((props.data || []).map((d) => [String(d.id || ""), d]));
  const seen = new Set();
  const segs = [];
  (props.pairLinks || []).forEach((pair, idx) => {
    const left = String(pair?.left_id || pair?.leftId || "").trim();
    const right = String(pair?.right_id || pair?.rightId || "").trim();
    if (!left || !right || left === right) return;
    const key = [left, right].sort().join("|");
    if (seen.has(key)) return;
    seen.add(key);
    const a = byId.get(left);
    const b = byId.get(right);
    if (!a || !b) return;
    const da = byDataId.get(left);
    const db = byDataId.get(right);
    const aFilled = da ? String(da.sup_base || "-") !== "-" : false;
    const bFilled = db ? String(db.sup_base || "-") !== "-" : false;
    segs.push({
      key: `${key}#${idx}`,
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      active: aFilled && bFilled,
    });
  });
  return segs;
});

const hoverNode = ref(null);
const containerRef = ref(null);
const tooltipRef = ref(null);
const pointer = ref({ x: 0, y: 0 });

const screenNodes = computed(() =>
  nodes.value.map((n) => {
    const p = toScreen(n);
    return { x: p.x, y: p.y };
  }),
);

function updatePointerFromEvent(event) {
  const el = containerRef.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  pointer.value = {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function onNodeEnter(event, node) {
  hoverNode.value = node;
  updatePointerFromEvent(event);
}

function onNodeMove(event) {
  if (!hoverNode.value) return;
  updatePointerFromEvent(event);
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

function circleRectDistance(cx, cy, left, top, right, bottom) {
  const dx = cx < left ? left - cx : cx > right ? cx - right : 0;
  const dy = cy < top ? top - cy : cy > bottom ? cy - bottom : 0;
  return Math.hypot(dx, dy);
}

function getPairPartner(id) {
  const peers = pairPartnerMap.value.get(id);
  if (!peers || !peers.size) return "—";
  return Array.from(peers).sort().join(", ");
}

function formatNodeType(node) {
  if (!node) return "—";
  if (!isCompareMode.value) return normalizeBase(node.sup_base);
  return String(node.type || "unknown");
}

function formatNodeMapping(node) {
  if (!node) return "—";
  if (!isCompareMode.value) return normalizeBase(node.sup_base);
  return `${normalizeBase(node.base)} → ${normalizeBase(node.sup_base)}`;
}

const tooltipStyle = computed(() => {
  if (!hoverNode.value) return {};
  const container = containerRef.value;
  if (!container) return {};
  const tooltipEl = tooltipRef.value;
  const tooltipWidth = tooltipEl?.offsetWidth || 220;
  const tooltipHeight = tooltipEl?.offsetHeight || 84;
  const containerWidth = container.clientWidth || props.width;
  const containerHeight = container.clientHeight || props.height;
  const offset = 12;
  const maxLeft = Math.max(0, containerWidth - tooltipWidth);
  const maxTop = Math.max(0, containerHeight - tooltipHeight);
  const nodeRadius = Math.max(8, (props.r || 12) * fit.value.s + 2);
  const anchor =
    pointer.value.x || pointer.value.y
      ? pointer.value
      : toScreen(hoverNode.value);

  const candidates = [
    {
      left: anchor.x + offset,
      top: anchor.y - tooltipHeight - offset,
      bias: 0,
    },
    { left: anchor.x + offset, top: anchor.y + offset, bias: 1 },
    {
      left: anchor.x - tooltipWidth - offset,
      top: anchor.y - tooltipHeight - offset,
      bias: 2,
    },
    { left: anchor.x - tooltipWidth - offset, top: anchor.y + offset, bias: 3 },
    {
      left: anchor.x - tooltipWidth / 2,
      top: anchor.y - tooltipHeight - offset,
      bias: 4,
    },
    { left: anchor.x - tooltipWidth / 2, top: anchor.y + offset, bias: 5 },
  ];

  let best = {
    left: clamp(anchor.x + offset, 0, maxLeft),
    top: clamp(anchor.y + offset, 0, maxTop),
    score: Infinity,
  };
  for (const c of candidates) {
    const left = clamp(c.left, 0, maxLeft);
    const top = clamp(c.top, 0, maxTop);
    const right = left + tooltipWidth;
    const bottom = top + tooltipHeight;

    let score = c.bias * 0.01;
    for (const n of screenNodes.value) {
      const d = circleRectDistance(n.x, n.y, left, top, right, bottom);
      if (d < nodeRadius) {
        const overlap = nodeRadius - d + 1;
        score += overlap * overlap;
      }
    }

    if (score < best.score) {
      best = { left, top, score };
    }
  }

  return {
    top: `${best.top}px`,
    left: `${best.left}px`,
  };
});
</script>

<style scoped>
.trna-container {
  background: var(--surface-3); /* 12 % 白，若想更透改 0.12 */
  border-radius: 8px;
  padding: 4px;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}

/* SVG 本身保持居中 */
svg {
  display: block;
  width: 100%;
  max-width: 100%;
  height: auto;
  margin: 0 auto;
  background: transparent; /* 让半透明容器可见 */
}

.base-group {
  cursor: default;
}
.pair-layer {
  pointer-events: none;
}
.pair-line {
  stroke-width: 2;
  stroke-linecap: round;
}
.pair-line-active {
  stroke: #5c6f7b;
  opacity: 0.8;
}
.pair-line-inactive {
  stroke: #9aa8b0;
  stroke-dasharray: 4 3;
  opacity: 0.45;
}
.base-clickable {
  cursor: pointer;
}
.base-hovered {
  stroke: #ff9800;
  stroke-width: 2;
  fill: #fff5e6;
}
.base-selected {
  filter: drop-shadow(0 0 4px rgba(17, 24, 39, 0.32));
}
.tooltip {
  position: absolute;
  background: var(--surface-dark);
  color: var(--overlay-text, #f8fafc);
  padding: 6px 10px;
  pointer-events: none;
  font-size: 14px;
  border-radius: 4px;
  box-shadow: var(--shadow-soft);
  max-width: 240px;
  white-space: normal;
  line-height: 1.35;
  z-index: 10;
}
.gap-node {
  /* base 和 sup 都是 “-” */
  fill: #b0bec5; /* 蓝灰 */
  stroke: #607d8b;
}
.match-node {
  /* 完全匹配 */
  fill: #c8e6c9; /* 绿色 */
  stroke: #4caf50;
}
.insertion-node {
  /* sup 有、base 没有 */
  fill: #bbdefb; /* 亮蓝 */
  stroke: #1e88e5;
}
.deletion-node {
  /* base 有、sup 没有 */
  fill: #ffe0b2; /* 橙 */
  stroke: #fb8c00;
}
.mismatch-node {
  /* 字母不同 */
  fill: #f8bbd0; /* 粉 */
  stroke: #e91e63;
}
.target-a {
  fill: #f8bbd0;
  stroke: #e91e63;
}
.target-c {
  fill: #c8e6c9;
  stroke: #4caf50;
}
.target-g {
  fill: #ffe0b2;
  stroke: #fb8c00;
}
.target-u {
  fill: #bbdefb;
  stroke: #1e88e5;
}
.target-gap {
  fill: #b0bec5;
  stroke: #607d8b;
}
.overflow-node-compare {
  fill: #e1bee7;
  stroke: #8e24aa;
  stroke-width: 1.5;
}
.overflow-node-target {
  stroke: #7e57c2;
  stroke-width: 2;
  stroke-dasharray: 2 1;
  filter: drop-shadow(0 0 1px rgba(126, 87, 194, 0.55));
  opacity: 0.98;
}
.overflow-node-target.target-gap {
  fill: #9fb2c0;
  stroke: #8e24aa;
}
.base-selected {
  stroke: #111827;
  stroke-width: 3;
}
</style>
