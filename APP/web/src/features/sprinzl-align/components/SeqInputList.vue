<template>
  <div class="seq-list">
    <div class="mode-toggle" role="group" aria-label="Input mode">
      <button
        type="button"
        class="toggle-btn"
        :class="{ active: inputMode === 'list' }"
        @click="setInputMode('list')"
      >
        List
      </button>
      <button
        type="button"
        class="toggle-btn"
        :class="{ active: inputMode === 'fasta' }"
        @click="setInputMode('fasta')"
      >
        FASTA
      </button>
    </div>

    <div v-if="inputMode === 'fasta'" class="fasta-block">
      <textarea
        v-model="fastaText"
        class="fasta-input"
        placeholder=">Sequence 1&#10;AUGC...&#10;>Sequence 2&#10;AUGC..."
        @input="applyFastaText"
      />
      <div class="fasta-hint" :class="{ warn: hasWarnings }">
        Parsed {{ localList.length }} sequence(s)
        <span v-if="hasWarnings">· invalid chars removed</span>
      </div>
    </div>

    <template v-else>
      <div v-for="(item, i) in localList" :key="i" class="seq-item">
        <div class="seq-header">
          <span class="seq-index">Seq {{ i + 1 }}</span>
          <span v-if="warnFlags[i]" class="seq-status-chip">sanitized</span>
          <button
            type="button"
            class="btn-remove"
            @click="removeSeq(i)"
            aria-label="Remove sequence"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </div>
        <label class="seq-field-label">Name</label>
        <input
          v-model="item.name"
          class="seq-name-input"
          placeholder="Sequence name (optional)"
        />
        <label class="seq-field-label">Sequence</label>
        <textarea
          v-model="item.seq"
          class="seq-input"
          :placeholder="`Enter tRNA sequence #${i + 1}`"
          @input="onSeqInput(i)"
        />
      </div>

      <button type="button" class="btn-add-seq" @click="addSeq">＋ Add Sequence</button>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, reactive, ref, watch, onMounted } from "vue";

interface SeqEntry {
  name: string;
  seq: string;
  anticode: string;
}

const props = defineProps<{
  modelValue: SeqEntry[];
  defaultSeq: string;
  defaultFasta?: string;
  defaultMode?: "list" | "fasta";
}>();
const emit = defineEmits<{
  (e: "update:modelValue", value: SeqEntry[]): void;
}>();

/* ========= 本地状态 ========= */
const localList = reactive<SeqEntry[]>(
  props.modelValue.map((s) => ({ ...s, name: s.name || "" }))
);
const warnFlags = reactive<boolean[]>(Array(localList.length).fill(false));
const inputMode = ref<"list" | "fasta">(props.defaultMode ?? "list");
const fastaText = ref("");
const hasWarnings = computed(() => warnFlags.some(Boolean));

/* ========= 序列规范化函数（与主组件保持一致） ========= */
function normalizeSeq(raw: string): string {
  return (raw || "")
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/T/g, "U")
    .replace(/[^AUGC]/g, ""); // 移除非 A/U/G/C
}

function containsInvalidSymbols(raw: string): boolean {
  return /[^AUGCT\s]/i.test(String(raw || ""));
}

/* ========= 单向同步：子 → 父 ========= */
watch(
  localList,
  (v) =>
    emit(
      "update:modelValue",
      v.map((s) => ({ ...s }))
    ),
  { deep: true }
);

/* ========= 事件处理 ========= */
function onSeqInput(idx: number) {
  const raw = localList[idx].seq;
  // 使用与主组件相同的规范化逻辑
  const sanitized = normalizeSeq(raw);
  warnFlags[idx] = containsInvalidSymbols(raw);
  localList[idx].seq = sanitized;
}

function addSeq() {
  localList.push({ name: "", seq: props.defaultSeq, anticode: "ACG" });
  warnFlags.push(false); // 对齐 warn 数组
}

function removeSeq(i: number) {
  localList.splice(i, 1);
  warnFlags.splice(i, 1);
}

function setInputMode(mode: "list" | "fasta") {
  if (inputMode.value === mode) return;
  inputMode.value = mode;
  if (mode === "fasta") {
    fastaText.value = listToFasta(localList);
  }
}

function listToFasta(entries: SeqEntry[]) {
  return entries
    .map((entry, idx) => {
      const name = (entry.name || "").trim() || `Sequence ${idx + 1}`;
      const seq = entry.seq || "";
      return `>${name}\n${wrapSeq(seq, 80)}`;
    })
    .join("\n");
}

function wrapSeq(seq: string, width: number) {
  if (!seq) return "";
  const parts = [];
  for (let i = 0; i < seq.length; i += width) {
    parts.push(seq.slice(i, i + width));
  }
  return parts.join("\n");
}

function applyFastaText() {
  if (inputMode.value !== "fasta") return;
  const parsed = parseFasta(fastaText.value);
  localList.splice(0, localList.length, ...parsed.entries);
  warnFlags.splice(0, warnFlags.length, ...parsed.warns);
}

function parseFasta(text: string) {
  const entries: SeqEntry[] = [];
  const warns: boolean[] = [];
  let curName = "";
  let curSeq = "";
  let sawHeader = false;

  const pushEntry = () => {
    if (!curSeq && !curName) return;
    const raw = curSeq.trim();
    const sanitized = normalizeSeq(raw);
    const warn = raw && containsInvalidSymbols(raw);
    const name = curName || `Sequence ${entries.length + 1}`;
    entries.push({ name, seq: sanitized, anticode: "ACG" });
    warns.push(Boolean(warn));
    curName = "";
    curSeq = "";
  };

  const lines = (text || "").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith(">")) {
      sawHeader = true;
      pushEntry();
      curName = trimmed.slice(1).trim();
    } else {
      curSeq += trimmed;
    }
  }
  pushEntry();

  if (!sawHeader && entries.length === 0 && text.trim()) {
    const raw = text.trim().replace(/\s+/g, "");
    const sanitized = normalizeSeq(raw);
    entries.push({ name: "Sequence 1", seq: sanitized, anticode: "ACG" });
    warns.push(containsInvalidSymbols(raw));
  }

  return { entries, warns };
}

/* ========= 组件挂载时检查是否需要初始化URL序列 ========= */
onMounted(() => {
  // 如果从父组件传递了序列，确保它们被规范化
  localList.forEach((item, idx) => {
    const sanitized = normalizeSeq(item.seq);
    if (item.seq !== sanitized) {
      item.seq = sanitized;
      warnFlags[idx] = true;
    }
  });

  if (props.defaultFasta && inputMode.value === "fasta") {
    fastaText.value = props.defaultFasta.trim();
    applyFastaText();
  }
});
</script>

<style scoped>
.seq-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* ── Segmented control ── */
.mode-toggle {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 3px;
  padding: 3px;
  border-radius: 14px;
  background: rgba(var(--card-bg-rgb), 0.82);
  border: 1px solid var(--border-soft);
  align-self: flex-start;
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, 0.04);
}
.toggle-btn {
  border: none;
  min-height: 38px;
  padding: 0.35rem 0.95rem;
  border-radius: 11px;
  cursor: pointer;
  background: transparent;
  color: var(--text-muted);
  font-size: 0.85rem;
  font-weight: 650;
  letter-spacing: 0.01em;
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.toggle-btn.active {
  background: rgba(var(--accent-rgb), 0.14);
  color: var(--accent-strong);
  box-shadow:
    inset 0 0 0 1px rgba(var(--accent-rgb), 0.38),
    0 1px 2px rgba(var(--accent-rgb), 0.08);
}

/* ── FASTA mode ── */
.fasta-block {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.fasta-input {
  min-height: 140px;
  resize: vertical;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  color: var(--field-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
    "Courier New", monospace;
  font-size: 0.85rem;
}
.fasta-input::placeholder {
  color: var(--field-placeholder);
}
.fasta-hint {
  color: var(--text-muted);
  font-size: 0.8rem;
}
.fasta-hint.warn {
  color: var(--danger);
}

/* ── Sequence card ── */
.seq-item {
  position: relative;
  background: var(--surface-1);
  padding: 0.75rem;
  border-radius: 12px;
  border: 1px solid var(--border-soft);
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  box-shadow: 0 1px 0 rgba(15, 23, 42, 0.03);
}

.seq-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: 30px;
  padding: 0 38px;
  margin-bottom: 0.05rem;
}
.seq-index {
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--text-strong);
}
.seq-status-chip {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.15);
  color: var(--accent);
}

.seq-field-label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: -0.1rem;
}

.seq-name-input {
  padding: 0.35rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  color: var(--field-text);
  font-size: 0.85rem;
}
.seq-name-input::placeholder {
  color: var(--field-placeholder);
}

.seq-input {
  min-height: 60px;
  resize: vertical;
  padding: 0.35rem 0.6rem;
  border-radius: 6px;
  border: 1px solid var(--field-border);
  background: var(--field-bg);
  color: var(--field-text);
  font-family: monospace;
  font-size: 0.85rem;
}
.seq-input::placeholder {
  color: var(--field-placeholder);
}

.btn-remove {
  position: absolute;
  top: 0.55rem;
  right: 0.55rem;
  width: 32px;
  height: 32px;
  background: rgba(var(--danger-rgb), 0.06);
  border: 1px solid rgba(var(--danger-rgb), 0.16);
  color: var(--danger);
  cursor: pointer;
  padding: 0;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.78;
  transition: opacity 0.15s ease, background 0.15s ease, border-color 0.15s ease;
}
.btn-remove:hover {
  opacity: 1;
  color: var(--danger);
  border-color: rgba(var(--danger-rgb), 0.28);
  background: var(--danger-soft);
}

.btn-add-seq {
  margin-top: 0.25rem;
  background: var(--accent);
  color: #fff;
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 500;
  transition: opacity 0.15s;
}
.btn-add-seq:hover {
  opacity: 0.88;
}

/* ── Mobile ── */
@media (max-width: 768px) {
  .mode-toggle {
    width: 100%;
    margin-bottom: 0.5rem;
    border-radius: 16px;
  }
  .toggle-btn {
    min-height: 42px;
    padding: 0.55rem;
    font-size: 0.92rem;
  }

  .fasta-input {
    min-height: 120px;
    font-size: 14px;
    padding: 12px;
  }
  .fasta-hint {
    font-size: 0.78rem;
    padding: 0.25rem 0;
  }

  .seq-item {
    padding: 0.65rem;
    gap: 0.4rem;
  }

  .seq-header {
    min-height: 30px;
    margin-bottom: 0;
  }

  .seq-name-input {
    width: 100%;
    min-height: 40px;
    font-size: 16px;
    padding: 10px 12px;
  }

  .seq-input {
    width: 100%;
    min-height: 80px;
    font-size: 14px;
    padding: 10px 12px;
  }

  .btn-remove {
    top: 0.55rem;
    right: 0.55rem;
    width: 32px;
    height: 32px;
    min-width: 32px;
    min-height: 32px;
  }

  .btn-add-seq {
    width: 100%;
    min-height: 48px;
    padding: 0.75rem;
    font-size: 0.95rem;
  }
}

@media (max-width: 480px) {
  .seq-item {
    padding: 0.5rem;
  }
  .fasta-input {
    min-height: 100px;
  }
}
</style>
