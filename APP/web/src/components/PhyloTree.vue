<template>
  <div class="tree-wrap">
    <div class="tree-toolbar">
      <span class="tree-title">Phylogenetic tree</span>
      <button class="btn-reset" type="button" @click="resetZoom">
        Reset view
      </button>
    </div>
    <div ref="container" class="tree-canvas"></div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { useTheme } from "@/composables/useTheme";

const props = defineProps<{
  newick: string | null;
  height?: number;
}>();

function getCssVar(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value ? value.trim() : fallback;
}

const container = ref<HTMLDivElement | null>(null);
let svgSelection: any | null = null;
let zoomBehavior: any | null = null;
let baseTransform = d3.zoomIdentity;
const { effectiveTheme } = useTheme();

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
        ancestors[ancestors.length - 1].children.push(sibling);
        tree = sibling;
        break;
      case ")":
        tree = ancestors.pop();
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

function renderTree() {
  if (!container.value) return;
  container.value.innerHTML = "";
  if (!props.newick) return;

  const data = parseNewick(props.newick);
  const root = d3.hierarchy(data, (d: any) => d.children);
  const leaves = root.leaves().length;
  const height = props.height ?? Math.max(360, leaves * 26);
  const width = Math.max(720, container.value.clientWidth || 0);
  const margin = { top: 24, right: 200, bottom: 24, left: 24 };
  const innerW = Math.max(200, width - margin.left - margin.right);
  const innerH = Math.max(200, height - margin.top - margin.bottom);

  const cluster = d3.cluster().size([innerH, innerW]);
  cluster(root);

  const hasLengths = root
    .descendants()
    .some((node: any) => typeof node.data.length === "number");
  if (hasLengths) {
    root.each((node: any) => {
      const parent = node.parent as any | null;
      const parentY = parent ? (parent as any).y : 0;
      const len = typeof node.data.length === "number" ? node.data.length : 0;
      (node as any).y = parentY + len;
    });
    const maxY =
      d3.max(root.descendants(), (node: any) => (node as any).y) || 1;
    const scale = d3.scaleLinear().domain([0, maxY]).range([0, innerW]);
    root.each((node: any) => {
      (node as any).y = scale((node as any).y);
    });
  }

  svgSelection = d3
    .select(container.value)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .attr("height", height);

  const chartText = getCssVar("--chart-text", "rgba(255,255,255,0.85)");
  const chartGrid = getCssVar("--chart-grid", "rgba(255,255,255,0.2)");
  const chartNode = getCssVar("--chart-node", "#8aa4ff");
  const chartLeaf = getCssVar("--chart-node-leaf", "#34d399");

  const zoomLayer = svgSelection.append("g");
  const g = zoomLayer.append("g");

  const linkGen = d3
    .linkHorizontal()
    .x((d: any) => d.y)
    .y((d: any) => d.x);

  g.selectAll("path")
    .data(root.links())
    .enter()
    .append("path")
    .attr("d", linkGen as any)
    .attr("fill", "none")
    .attr("stroke", chartGrid)
    .attr("stroke-width", 1);

  const node = g
    .selectAll("g")
    .data(root.descendants())
    .enter()
    .append("g")
    .attr("transform", (d: any) => `translate(${(d as any).y},${d.x})`);

  node
    .append("circle")
    .attr("r", (d: any) => (d.children ? 2.4 : 3.2))
    .attr("fill", (d: any) => (d.children ? chartNode : chartLeaf));

  node
    .filter((d: any) => !d.children)
    .append("text")
    .attr("x", 8)
    .attr("dy", "0.32em")
    .attr("fill", chartText)
    .attr("font-size", "16px")
    .text((d: any) => d.data.name || "");

  zoomBehavior = d3
    .zoom()
    .scaleExtent([0.5, 6])
    .on("zoom", (event: any) => {
      zoomLayer.attr("transform", event.transform.toString());
    });

  baseTransform = d3.zoomIdentity.translate(margin.left, margin.top);
  svgSelection.call(zoomBehavior).call(zoomBehavior.transform, baseTransform);
}

function resetZoom() {
  if (!svgSelection || !zoomBehavior) return;
  svgSelection
    .transition()
    .duration(200)
    .call(zoomBehavior.transform, baseTransform);
}

onMounted(() => {
  renderTree();
});

watch(
  () => props.newick,
  async () => {
    await nextTick();
    renderTree();
  }
);

watch(
  () => effectiveTheme.value,
  () => {
    renderTree();
  }
);

defineExpose({ resetZoom });
</script>

<style scoped>
.tree-wrap {
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  padding: 0.75rem;
  background: var(--surface-2);
}
.tree-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}
.tree-title {
  color: var(--text-soft);
  font-weight: 600;
  font-size: 1rem;
}
.btn-reset {
  border: 1px solid var(--border-strong);
  background: transparent;
  color: var(--text-soft);
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  cursor: pointer;
  font-size: 0.9rem;
}
.btn-reset:hover {
  border-color: var(--accent);
}
.tree-canvas {
  width: 100%;
  overflow: hidden;
}
</style>
