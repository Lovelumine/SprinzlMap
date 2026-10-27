<!-- 文件：src/views/api/ApiReference.vue
     原生 Vue 3 API 参考页：直接读取后端 Flask-RESTX 暴露的 /swagger.json，
     按 tag 分组渲染端点、参数、请求体与响应，样式跟随站点主题变量（含暗色）。 -->
<template>
  <div class="api-page">
    <!-- ── 顶部概览 ── -->
    <header class="api-hero">
      <div class="hero-left">
        <p class="hero-eyebrow">REST API</p>
        <h1>
          {{ info.title || "SprinzlMap API" }}
          <span v-if="info.version" class="ver">v{{ info.version }}</span>
        </h1>
        <p v-if="info.description" class="hero-desc">{{ info.description }}</p>
      </div>
      <div class="hero-meta">
        <div class="stat">
          <span class="k">Base URL</span>
          <code class="v">{{ baseUrl }}</code>
        </div>
        <div class="stat">
          <span class="k">Endpoints</span>
          <span class="v">{{ endpoints.length }}</span>
        </div>
        <div class="stat">
          <span class="k">Groups</span>
          <span class="v">{{ groups.length }}</span>
        </div>
      </div>
    </header>

    <!-- ── 加载 / 失败态 ── -->
    <div v-if="loading" class="state">Loading API specification…</div>
    <div v-else-if="error" class="state state-error">
      <strong>Could not load the API specification.</strong>
      <span>{{ error }}</span>
      <span class="hint">The backend may be offline. Try <code>{{ specUrl }}</code> directly.</span>
    </div>

    <!-- ── 主体：侧栏 + 内容 ── -->
    <div v-else class="api-body">
      <aside class="api-side">
        <div class="search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input v-model="query" type="search" placeholder="Search endpoints…" aria-label="Search endpoints" />
          <button v-if="query" class="clear" type="button" aria-label="Clear search" @click="query = ''">×</button>
        </div>

        <nav class="side-nav">
          <div v-for="g in visibleGroups" :key="g.name" class="side-group">
            <div class="side-group-title">
              <span>{{ g.name }}</span>
              <span class="count">{{ g.items.length }}</span>
            </div>
            <a
              v-for="e in g.items"
              :key="e.id"
              :href="`#${e.id}`"
              :class="{ active: activeId === e.id }"
              @click.prevent="jumpTo(e.id)"
            >
              <span class="m" :class="`m-${e.method}`">{{ e.method.toUpperCase() }}</span>
              <span class="p">{{ e.path }}</span>
            </a>
          </div>
        </nav>
      </aside>

      <main class="api-main">
        <section v-for="g in visibleGroups" :key="g.name" class="tag-section">
          <div class="tag-head">
            <h2>{{ g.name }}</h2>
            <p v-if="g.description">{{ g.description }}</p>
          </div>

          <article
            v-for="e in g.items"
            :key="e.id"
            :id="e.id"
            class="ep"
            :class="{ open: isOpen(e.id) }"
          >
            <button class="ep-head" type="button" :aria-expanded="isOpen(e.id)" @click="toggle(e.id)">
              <span class="m" :class="`m-${e.method}`">{{ e.method.toUpperCase() }}</span>
              <code class="ep-path">{{ e.path }}</code>
              <span class="ep-title">{{ e.title }}</span>
              <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>

            <div v-if="isOpen(e.id)" class="ep-body">
              <p class="ep-desc">{{ e.description }}</p>

              <div class="ep-actions">
                <button class="mini" type="button" @click="copy(`${requestOrigin}${baseUrl}${e.path}`)">
                  {{ copiedId === e.id ? "Copied ✓" : "Copy full URL" }}
                </button>
                <code class="curl">{{ curlFor(e) }}</code>
              </div>

              <!-- 参数 -->
              <template v-if="e.params.length">
                <h4>Parameters</h4>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Name</th><th>In</th><th>Type</th><th>Required</th><th>Description</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="p in e.params" :key="`${p.in}-${p.name}`">
                        <td><code>{{ p.name }}</code></td>
                        <td><span class="pill">{{ p.in }}</span></td>
                        <td><code class="t">{{ p.type }}</code></td>
                        <td>
                          <span v-if="p.required" class="req">required</span>
                          <span v-else class="opt">optional</span>
                        </td>
                        <td class="desc">{{ p.description || "—" }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>

              <!-- 请求体 -->
              <template v-if="e.body">
                <h4>
                  Request body
                  <code class="ref">{{ e.bodyName }}</code>
                </h4>
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Field</th><th>Type</th><th>Required</th><th>Description</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="f in e.body" :key="f.name">
                        <td><code>{{ f.name }}</code></td>
                        <td>
                          <code class="t">{{ f.type }}</code>
                          <span v-if="f.enum" class="enum">{{ f.enum.join(" · ") }}</span>
                        </td>
                        <td>
                          <span v-if="f.required" class="req">required</span>
                          <span v-else class="opt">optional</span>
                        </td>
                        <td class="desc">
                          {{ f.description || "—" }}
                          <span v-if="f.default !== undefined" class="dflt">default: {{ fmt(f.default) }}</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>

              <!-- 响应 -->
              <h4>Responses</h4>
              <ul class="responses">
                <li v-for="r in e.responses" :key="r.code">
                  <span class="code" :class="statusClass(r.code)">{{ r.code }}</span>
                  <span>{{ r.description }}</span>
                </li>
              </ul>
            </div>
          </article>
        </section>

        <div v-if="!visibleGroups.length" class="state">
          No endpoints match “{{ query }}”.
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { apiUrl } from "@/shared/api";

/* ── OpenAPI 结构（只声明用到的字段） ── */
interface JsonSchema {
  $ref?: string;
  type?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
}
interface RawParam {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  type?: string;
  schema?: JsonSchema;
}
interface RawOperation {
  operationId?: string;
  tags?: string[];
  summary?: string;
  description?: string;
  parameters?: RawParam[];
  responses?: Record<string, { description?: string }>;
}
interface Spec {
  info?: { title?: string; version?: string; description?: string };
  tags?: Array<{ name: string; description?: string }>;
  paths?: Record<string, Record<string, RawOperation>>;
  definitions?: Record<string, JsonSchema>;
}

interface Param { name: string; in: string; type: string; required: boolean; description: string }
interface Field { name: string; type: string; required: boolean; description: string; default?: unknown; enum?: string[] }
interface Endpoint {
  id: string;
  method: string;
  path: string;
  tag: string;
  title: string;
  description: string;
  params: Param[];
  body: Field[] | null;
  bodyName: string;
  responses: Array<{ code: string; description: string }>;
}

const HTTP_METHODS = ["get", "post", "put", "patch", "delete"];

/* 规格里没有 summary，这里为每个 operationId 补一句人话标题与说明。 */
const LABELS: Record<string, [string, string]> = {
  post_align: ["Number a single tRNA", "Submit one mature tRNA sequence and receive a per-residue Sprinzl coordinate table, secondary structure and agent trace."],
  post_align_batch: ["Number a batch of sequences", "Submit many sequences at once; per-item status is returned alongside the results."],
  post_align_batch_stream: ["Stream a batch run (SSE)", "Same as the batch endpoint, but reports progress as Server-Sent Events."],
  post_align_trace: ["Inspect the analysis trace", "Return region assignments and warnings without producing a full coordinate table."],
  post_align_scientific_interpretation: ["Explain a result", "Generate an LLM-written scientific interpretation for an existing analysis."],
  post_alignment_session: ["Save an alignment session", "Persist a payload together with its results so it can be retrieved later."],
  get_alignment_session: ["List saved sessions", "Browse previously stored alignment sessions."],
  get_alignment_session_get: ["Fetch a saved session", "Retrieve one stored session by its request id."],
  patch_alignment_session_email: ["Attach an email to a session", "Associate a contact address with a stored session."],
  get_templates: ["List numbering templates", "Enumerate the Sprinzl reference templates available for alignment."],
  get_export: ["Export a template as CSV", "Download the numbered CSV for a given template."],
  post_cluster_map: ["Clustermap (raster)", "Upload a two-line alignment CSV and receive a clustered heatmap image."],
  post_cluster_map_svg: ["Clustermap (SVG)", "Upload an alignment CSV and receive the clustermap as scalable SVG."],
  post_phylo_tree: ["Phylogenetic tree", "Upload an alignment CSV and receive a Newick tree."],
  get_my_sql_get_species: ["List species", "Species available in the reference database."],
  get_my_sql_get_trna_filters: ["List isotype / anticodon filters", "Filter values for browsing tRNA records."],
  post_my_sql_query: ["Query a database table", "Run a filtered query against a table and return the matching rows."],
  post_region_lookup: ["Look up Sprinzl regions", "Map Sprinzl numbers, including variants, to their structural region."],
  get_gold_curation_cases: ["List curation cases", "Paginated, filterable queue of gold-standard curation cases."],
  get_gold_curation_case: ["Fetch one curation case", "Full record for a single case, identified by sequence name."],
  post_gold_curation_decision: ["Submit a curation decision", "Record the final decision and curated labels for a case."],
  post_gold_curation_draft: ["Save a curation draft", "Store an in-progress decision without finalising it."],
  post_gold_curation_interpretation: ["Explain a curation case", "Produce a scientific explanation for a curation decision."],
  post_gold_curation_export: ["Export curation results", "Download the curated gold-standard records."],
  get_gold_curation_summary: ["Curation summary", "Progress counters for the curation workspace."],
};

const specUrl = apiUrl("/swagger.json");
const baseUrl = apiUrl("/").replace(/\/$/, "");
const requestOrigin = typeof window !== "undefined" ? window.location.origin : "";

const spec = ref<Spec | null>(null);
const loading = ref(true);
const error = ref("");
const query = ref("");
const openIds = reactive(new Set<string>());
const activeId = ref("");
const copiedId = ref("");

const info = computed(() => spec.value?.info ?? {});
const tagMeta = computed(() => {
  const map = new Map<string, string>();
  for (const t of spec.value?.tags ?? []) map.set(t.name, t.description ?? "");
  return map;
});

/* ── 工具函数 ── */
function resolveRef(ref?: string): JsonSchema | undefined {
  if (!ref) return undefined;
  const name = ref.split("/").pop() ?? "";
  return spec.value?.definitions?.[name];
}

function typeName(schema?: JsonSchema): string {
  if (!schema) return "any";
  if (schema.$ref) return schema.$ref.split("/").pop() ?? "object";
  if (schema.type === "array") return `${typeName(schema.items)}[]`;
  return schema.type ?? "any";
}

function fieldsOf(schema?: JsonSchema): Field[] {
  const resolved = schema?.$ref ? resolveRef(schema.$ref) : schema;
  const props = resolved?.properties;
  if (!props) return [];
  const required = new Set(resolved?.required ?? []);
  return Object.entries(props).map(([name, sub]) => ({
    name,
    type: typeName(sub),
    required: required.has(name),
    description: sub.description ?? "",
    default: sub.default,
    enum: Array.isArray(sub.enum) ? sub.enum.map((v) => String(v)) : undefined,
  }));
}

function fmt(value: unknown): string {
  return typeof value === "string" ? `"${value}"` : JSON.stringify(value);
}

function statusClass(code: string): string {
  if (code.startsWith("2")) return "ok";
  if (code.startsWith("4")) return "warn";
  return "err";
}

function curlFor(e: Endpoint): string {
  const body = e.body ? " \\\n  -H 'Content-Type: application/json' \\\n  -d '{ … }'" : "";
  return `curl -X ${e.method.toUpperCase()} '${baseUrl}${e.path}'${body}`;
}

/* ── 组装端点列表 ── */
const endpoints = computed<Endpoint[]>(() => {
  const paths = spec.value?.paths ?? {};
  const out: Endpoint[] = [];
  const tagOrder = (spec.value?.tags ?? []).map((t) => t.name);
  for (const [path, ops] of Object.entries(paths)) {
    for (const [method, op] of Object.entries(ops)) {
      if (!HTTP_METHODS.includes(method)) continue;
      const tag = op.tags?.[0] ?? "default";
      const [title, description] = LABELS[op.operationId ?? ""] ?? [
        op.summary || op.operationId || `${method.toUpperCase()} ${path}`,
        op.description || "",
      ];
      const params: Param[] = (op.parameters ?? [])
        .filter((p) => p.in !== "body" && p.in !== "formData")
        .map((p) => ({
          name: p.name,
          in: p.in,
          type: p.schema?.type || p.type || "string",
          required: Boolean(p.required),
          description: p.description ?? "",
        }));
      const formParam = (op.parameters ?? []).find((p) => p.in === "formData");
      const bodyParam = (op.parameters ?? []).find((p) => p.in === "body");
      let body: Field[] | null = null;
      let bodyName = "";
      if (bodyParam?.schema) {
        body = fieldsOf(bodyParam.schema);
        bodyName = bodyParam.schema.$ref?.split("/").pop() ?? "object";
      } else if (formParam) {
        body = [{
          name: formParam.name,
          type: "file",
          required: Boolean(formParam.required),
          description: formParam.description ?? "Uploaded CSV file.",
        }];
        bodyName = "multipart/form-data";
      }
      const responses = Object.entries(op.responses ?? {}).map(([code, r]) => ({
        code,
        description: r?.description || "—",
      }));
      out.push({
        id: `${method}-${path.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "")}`,
        method,
        path,
        tag,
        title,
        description,
        params,
        body,
        bodyName,
        responses,
      });
    }
  }
  const rank = (t: string) => (tagOrder.indexOf(t) === -1 ? 99 : tagOrder.indexOf(t));
  return out.sort((a, b) => rank(a.tag) - rank(b.tag) || a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
});

const groups = computed(() => {
  const map = new Map<string, Endpoint[]>();
  for (const e of endpoints.value) {
    if (!map.has(e.tag)) map.set(e.tag, []);
    map.get(e.tag)!.push(e);
  }
  return [...map.entries()].map(([name, items]) => ({
    name,
    description: tagMeta.value.get(name) ?? "",
    items,
  }));
});

const visibleGroups = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return groups.value;
  return groups.value
    .map((g) => ({
      ...g,
      items: g.items.filter((e) =>
        `${e.method} ${e.path} ${e.title} ${e.description}`.toLowerCase().includes(q),
      ),
    }))
    .filter((g) => g.items.length);
});

/* ── 交互 ── */
function isOpen(id: string) {
  return openIds.has(id);
}
function toggle(id: string) {
  if (openIds.has(id)) openIds.delete(id);
  else openIds.add(id);
}
function jumpTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  activeId.value = id;
}
async function copy(text: string) {
  const id = activeId.value;
  try {
    await navigator.clipboard.writeText(text);
    copiedId.value = id;
    window.setTimeout(() => { if (copiedId.value === id) copiedId.value = ""; }, 1500);
  } catch { /* 剪贴板不可用时静默失败 */ }
}

/* ── 滚动高亮 ── */
let observer: IntersectionObserver | null = null;
function observeEndpoints() {
  observer?.disconnect();
  observer = new IntersectionObserver(
    (entries) => {
      const hit = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (hit?.target.id) activeId.value = hit.target.id;
    },
    { rootMargin: "-72px 0px -70% 0px", threshold: 0 },
  );
  for (const e of endpoints.value) {
    const el = document.getElementById(e.id);
    if (el) observer.observe(el);
  }
}

onMounted(async () => {
  try {
    const res = await fetch(specUrl, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    spec.value = (await res.json()) as Spec;
    // 默认展开分组里的第一个端点
    const first = endpoints.value[0];
    if (first) openIds.add(first.id);
    await Promise.resolve();
    observeEndpoints();
    const hash = window.location.hash.replace("#", "");
    if (hash) window.setTimeout(() => jumpTo(hash), 60);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
});

onBeforeUnmount(() => observer?.disconnect());
</script>

<style scoped>
.api-page {
  width: 100%;
  padding: 1.4rem clamp(1rem, 2.2vw, 2.6rem) 4rem;
  box-sizing: border-box;
  color: var(--text-strong);
}

/* ── 顶部概览：整幅通栏，左侧标题、右侧指标 ── */
.api-hero {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1.5rem 2.5rem;
  border: 1px solid var(--border-soft);
  border-radius: 16px;
  padding: 1.5rem 1.9rem;
  background: linear-gradient(135deg, var(--surface-1) 0%, var(--surface-2) 100%);
  box-shadow: var(--shadow-soft);
  overflow: hidden;
}
.api-hero::after {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 3px;
  background: var(--accent-grad);
}
.hero-eyebrow {
  margin: 0 0 0.35rem;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--accent-strong);
}
.api-hero h1 {
  margin: 0;
  font-size: 1.85rem;
  line-height: 1.2;
  letter-spacing: -0.01em;
}
.ver {
  margin-left: 0.5rem;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  vertical-align: middle;
  color: var(--accent-strong);
  background: var(--accent-soft);
  border: 1px solid var(--border-soft);
}
.hero-desc {
  margin: 0.55rem 0 0;
  max-width: 70ch;
  font-size: 0.94rem;
  line-height: 1.6;
  color: var(--text-soft);
}
.hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 106px;
  padding: 0.6rem 0.95rem;
  border-radius: 12px;
  background: var(--field-bg);
  border: 1px solid var(--field-border);
}
.stat .k {
  font-size: 0.66rem;
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.stat .v {
  font-size: 1.15rem;
  font-weight: 700;
  line-height: 1.25;
  color: var(--accent-strong);
}
.stat code.v {
  font-size: 0.95rem;
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}

/* ── 状态 ── */
.state {
  margin-top: 1.5rem;
  padding: 2.5rem 1.5rem;
  border: 1px dashed var(--border-strong);
  border-radius: 14px;
  text-align: center;
  color: var(--text-muted);
}
.state-error {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  border-style: solid;
  border-color: var(--danger);
  background: var(--danger-soft);
  color: var(--text-strong);
}
.state .hint {
  font-size: 0.85rem;
  color: var(--text-muted);
}

/* ── 布局 ── */
.api-body {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 2rem;
  margin-top: 1.5rem;
  align-items: start;
}

/* ── 侧栏 ── */
.api-side {
  position: sticky;
  top: 72px;
  max-height: calc(100vh - 92px);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding-right: 1.25rem;
  border-right: 1px solid var(--border-soft);
}
.search {
  position: relative;
  display: flex;
  align-items: center;
}
.search svg {
  position: absolute;
  left: 10px;
  width: 15px;
  height: 15px;
  color: var(--text-muted);
  pointer-events: none;
}
.search input {
  width: 100%;
  padding: 0.5rem 2rem 0.5rem 2rem;
  border-radius: 10px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  color: var(--field-text);
  font-size: 0.86rem;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.search input::placeholder { color: var(--field-placeholder); }
.search input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.14);
}
.search .clear {
  position: absolute;
  right: 6px;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}
.search .clear:hover { background: var(--surface-3); color: var(--text-strong); }

.side-nav {
  overflow-y: auto;
  padding-right: 4px;
}
.side-group + .side-group { margin-top: 1rem; }
.side-group-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 0.5rem 0.35rem;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.side-group-title .count {
  font-weight: 600;
  letter-spacing: 0;
  color: var(--text-muted);
}
.side-nav a {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.35rem 0.5rem;
  border-radius: 8px;
  text-decoration: none;
  color: var(--text-soft);
  font-size: 0.82rem;
  transition: background 0.14s ease, color 0.14s ease;
}
.side-nav a:hover { background: var(--surface-2); color: var(--text-strong); }
.side-nav a.active {
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-weight: 600;
}
.side-nav .p {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 方法徽标 ── */
.m {
  flex: 0 0 auto;
  min-width: 44px;
  padding: 0.12rem 0.4rem;
  border-radius: 5px;
  font-size: 0.63rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-align: center;
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}
.m-get { color: #2563eb; background: rgba(37, 99, 235, 0.13); }
.m-post { color: var(--accent-strong); background: rgba(var(--accent-rgb), 0.15); }
.m-patch { color: #b45309; background: rgba(180, 83, 9, 0.14); }
.m-put { color: #7c3aed; background: rgba(124, 58, 237, 0.13); }
.m-delete { color: var(--danger); background: var(--danger-soft); }

/* ── 内容区 ── */
.tag-section + .tag-section { margin-top: 2rem; }
.tag-head { margin-bottom: 0.7rem; }
.tag-head h2 {
  margin: 0;
  font-size: 1.05rem;
  letter-spacing: 0.01em;
}
.tag-head p {
  margin: 0.2rem 0 0;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.ep {
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--surface-1);
  margin-bottom: 0.6rem;
  overflow: hidden;
  scroll-margin-top: 72px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.ep:hover { border-color: var(--border-strong); }
.ep.open {
  border-color: var(--accent);
  box-shadow: 0 6px 20px rgba(var(--accent-rgb), 0.09);
}
.ep-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.65rem;
  padding: 0.7rem 0.9rem;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.ep-path {
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.84rem;
  color: var(--text-strong);
}
.ep-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.87rem;
  color: var(--text-soft);
}
.chev {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  color: var(--text-muted);
  transition: transform 0.2s ease;
}
.ep.open .chev { transform: rotate(180deg); }

.ep-body {
  padding: 0.2rem 0.95rem 1rem;
  border-top: 1px solid var(--border-soft);
}
.ep-desc {
  margin: 0.7rem 0 0.9rem;
  font-size: 0.88rem;
  line-height: 1.6;
  color: var(--text-soft);
}
.ep-actions {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.mini {
  padding: 0.3rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  color: var(--accent-strong);
  font-size: 0.76rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}
.mini:hover { border-color: var(--accent); background: var(--accent-soft); }
.curl {
  flex: 1;
  min-width: 240px;
  max-width: 820px;
  margin: 0;
  padding: 0.5rem 0.7rem;
  border-radius: 8px;
  background: var(--surface-dark);
  color: var(--overlay-text, #e2e8f0);
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.74rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.ep-body h4 {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 1rem 0 0.45rem;
  font-size: 0.76rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.ep-body h4 .ref {
  text-transform: none;
  letter-spacing: 0;
  font-size: 0.76rem;
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  color: var(--accent-strong);
  background: var(--accent-soft);
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
th {
  text-align: left;
  padding: 0.45rem 0.7rem;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-muted);
  background: var(--surface-2);
  border-bottom: 1px solid var(--border-soft);
  white-space: nowrap;
}
td {
  padding: 0.5rem 0.7rem;
  border-bottom: 1px solid var(--border-soft);
  vertical-align: top;
  color: var(--text-soft);
}
tr:last-child td { border-bottom: 0; }
td code {
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.8rem;
  color: var(--text-strong);
}
td code.t { color: var(--accent-strong); }
td.desc { line-height: 1.55; }
.pill {
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  font-size: 0.72rem;
  background: var(--surface-3);
  color: var(--text-soft);
}
.req, .opt {
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
}
.req { color: var(--danger); }
.opt { color: var(--text-muted); }
.enum, .dflt {
  display: inline-block;
  margin-left: 0.35rem;
  font-size: 0.72rem;
  color: var(--text-muted);
}
.dflt { display: block; margin-top: 0.15rem; margin-left: 0; }

.responses {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.responses li {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  font-size: 0.83rem;
  color: var(--text-soft);
}
.code {
  min-width: 46px;
  padding: 0.12rem 0.4rem;
  border-radius: 5px;
  font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.74rem;
  font-weight: 700;
  text-align: center;
}
.code.ok { color: var(--success-strong); background: rgba(var(--success-rgb), 0.14); }
.code.warn { color: #b45309; background: rgba(180, 83, 9, 0.14); }
.code.err { color: var(--danger); background: var(--danger-soft); }

/* ── 窄屏 ── */
@media (max-width: 900px) {
  .api-body { grid-template-columns: minmax(0, 1fr); }
  .api-side {
    position: static;
    max-height: none;
    padding-right: 0;
    border-right: 0;
  }
  .side-nav {
    display: flex;
    gap: 0.4rem;
    overflow-x: auto;
    padding-bottom: 0.3rem;
  }
  .side-group { display: flex; align-items: center; gap: 0.3rem; }
  .side-group + .side-group { margin-top: 0; }
  .side-group-title { display: none; }
  .side-nav a { white-space: nowrap; }
  .side-nav .p { max-width: 180px; }
  .ep-title { display: none; }
}
</style>
