import { onBeforeUnmount, ref } from "vue";
import type { Ref } from "vue";
import { apiFetch } from "@/shared/api";
import type { AlignmentResult, SeqEntry, StructureOverlay, StructurePairLink, RunProcessEvent } from "../model/types";
import {
  getErrorMessage,
  parseAlignmentCsv,
  parseTwoLineCsv,
  readErrorMessage,
} from "../model/utils";

interface AlignmentApiResponse {
  template_name?: string;
  csv_content?: string;
  warnings?: unknown[];
  structure?: unknown;
  llm_trace?: unknown;
  agent?: unknown;
  scientific_provenance?: unknown;
}

interface AlignmentBatchApiItem {
  index?: number;
  target_seq?: string;
  ok?: boolean;
  template_name?: string;
  csv_content?: string;
  warnings?: unknown[];
  structure?: unknown;
  llm_trace?: unknown;
  agent?: unknown;
  scientific_provenance?: unknown;
  error?: string;
}

interface AlignmentBatchApiResponse {
  results?: AlignmentBatchApiItem[];
}

interface ExportApiResponse {
  csv_content?: string;
}

interface ParsedSseEvent {
  event: string;
  data: string;
}

function needsTemplateReference(row: AlignmentResult): boolean {
  return (
    String(row.scientific_provenance?.numbering_backend || "") !== "cmalign"
    && String(row.template_name || "").trim().length > 0
    && Array.isArray(row.nums)
    && row.nums.length > 0
    && !row.error
  );
}

interface AlignmentBatchStatusEvent {
  index?: number;
  process_event?: RunProcessEvent;
  stage?: string;
  message?: string;
  completed?: number;
  total?: number;
  succeeded?: number;
  failed?: number;
  active_workers?: number;
  pending?: number;
  transport?: string;
  current_index?: number;
  content?: string;
}

function normalizeStructureOverlay(raw: unknown): StructureOverlay | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const src = raw as Record<string, unknown>;
  const linksRaw = Array.isArray(src.id_pairs) ? src.id_pairs : [];
  const idPairs: StructurePairLink[] = linksRaw
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const rec = item as Record<string, unknown>;
      const leftId = String(rec.left_id || "").trim();
      const rightId = String(rec.right_id || "").trim();
      if (!leftId || !rightId) return null;
      const out: StructurePairLink = { left_id: leftId, right_id: rightId };
      const li = Number(rec.left_index);
      const ri = Number(rec.right_index);
      if (Number.isFinite(li)) out.left_index = li;
      if (Number.isFinite(ri)) out.right_index = ri;
      return out;
    })
    .filter((v): v is StructurePairLink => !!v);

  return {
    annotation_source: String(src.annotation_source || "").trim() || undefined,
    secondary_structure: String(src.secondary_structure || "").trim() || undefined,
    residue_count: Number.isFinite(Number(src.residue_count))
      ? Number(src.residue_count)
      : undefined,
    pair_count: Number.isFinite(Number(src.pair_count))
      ? Number(src.pair_count)
      : undefined,
    id_pairs: idPairs,
  };
}

function parseSseEventBlock(block: string): ParsedSseEvent | null {
  const lines = String(block || "").split(/\r?\n/);
  let event = "message";
  const dataParts: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      const v = line.slice(6).trim();
      if (v) event = v;
      continue;
    }
    if (line.startsWith("data:")) {
      dataParts.push(line.slice(5).trimStart());
    }
  }
  if (!dataParts.length) return null;
  return { event, data: dataParts.join("\n") };
}

async function readEventStream(response: Response, handle: (event: ParsedSseEvent | null) => void) {
  if (!response.body) throw new Error("Alignment stream body is empty.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let separator: RegExpExecArray | null;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        handle(parseSseEventBlock(buffer.slice(0, separator.index)));
        buffer = buffer.slice(separator.index + separator[0].length);
      }
      if (done) break;
    }
    if (buffer.trim()) handle(parseSseEventBlock(buffer));
  } finally {
    reader.releaseLock();
  }
}

function formatBackendStageLabel(stage: string): string {
  switch (stage) {
    case "accepted":
      return "Backend accepted request";
    case "dispatching":
      return "Dispatching workers";
    case "aligning":
      return "Aligning sequence";
    case "working":
      return "Backend running";
    case "progress":
      return "Progress update";
    case "template_matching":
      return "Selecting template";
    case "annotation_ready":
      return "Structure annotated";
    case "boundary_refinement":
      return "Refining boundaries";
    case "dp_alignment":
      return "Running DP alignment";
    case "llm_postcheck":
      return "Running optional regional review";
    case "dp_only":
      return "DP-only alignment";
    case "normalizing":
      return "Normalizing alignment";
    case "finalizing_rows":
      return "Finalizing rows";
    case "rendering_views":
      return "Rendering result views";
    case "completed":
      return "Alignment complete";
    default:
      return stage || "Backend update";
  }
}

interface UseSprinzlAlignmentRunnerDeps {
  seqList: Ref<SeqEntry[]>;
  useLlm: Ref<boolean>;
  llmReasoning: Ref<boolean>;
  results: Ref<AlignmentResult[]>;
  running: Ref<boolean>;
  progress: Ref<number>;
  resetRequestState: () => void;
  saveSession: (
    payload: Record<string, unknown>,
    savedResults: AlignmentResult[],
  ) => Promise<void>;
  buildPayload: () => Record<string, unknown>;
  buildAlignmentOptions: () => Record<string, unknown>;
}

export function useSprinzlAlignmentRunner(deps: UseSprinzlAlignmentRunnerDeps) {
  const {
    seqList,
    useLlm,
    llmReasoning,
    results,
    running,
    progress,
    resetRequestState,
    saveSession,
    buildPayload,
    buildAlignmentOptions,
  } = deps;

  const doneCount = ref(0);
  const totalCount = ref(0);
  const successCount = ref(0);
  const failedCount = ref(0);
  const abstainedCount = ref(0);
  const runStage = ref("Idle");
  const backendStatus = ref("");
  const backendPending = ref(0);
  const backendWorkers = ref(0);
  const transportMode = ref<"sse" | "batch" | "serial" | "unknown">("unknown");
  const currentSequence = ref("");
  const currentIndex = ref<number | null>(null);
  const elapsedSeconds = ref(0);
  const silenceSeconds = ref(0);
  let startedAtMs = 0;
  let lastEventAtMs = 0;
  let fakeTimer: ReturnType<typeof setInterval> | null = null;
  let statusTimer: ReturnType<typeof setInterval> | null = null;
  const preferredBatchWorkers = 20;

  function clearProgressTimer() {
    if (fakeTimer !== null) {
      clearInterval(fakeTimer);
      fakeTimer = null;
    }
  }

  function clearStatusTimer() {
    if (statusTimer !== null) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  function touchEvent() {
    lastEventAtMs = Date.now();
  }

  function setCurrent(idx: number | null, name: string) {
    currentIndex.value = idx;
    currentSequence.value = String(name || "").trim();
    touchEvent();
  }

  function startStatusTicker() {
    clearStatusTimer();
    statusTimer = setInterval(() => {
      if (!running.value) return;
      const now = Date.now();
      elapsedSeconds.value = Math.max(0, Math.floor((now - startedAtMs) / 1000));
      silenceSeconds.value = Math.max(0, Math.floor((now - lastEventAtMs) / 1000));
    }, 1000);
  }

  function initializePlaceholderRows() {
    results.value = seqList.value.map((item, i) => ({
      seq_name: (item.name || "").trim() || `Seq ${i + 1}`,
      seq_index: i,
      pending: true,
      warnings: [],
      run_process: { state: "queued", stage: "queued", events: [] },
    }));
  }

  function recordProcess(idx: number, event: RunProcessEvent) {
    const row = results.value[idx];
    if (!row?.pending || !row.run_process) return;
    const process = row.run_process;
    process.state = "running";
    process.stage = event.stage;
    process.started_at ||= event.at;
    process.events.push(event);
  }

  function finishProcess(idx: number, res: AlignmentResult) {
    const process = results.value[idx]?.run_process;
    if (!process) return undefined;
    const at = new Date().toISOString();
    process.state = res.error ? "error" : "completed";
    process.stage = res.error ? "failed" : "completed";
    process.finished_at = at;
    process.events.push({ stage: process.stage, at, source: "system",
      disposition: res.agent?.work_state_summary?.disposition, error: res.error });
    return process;
  }

  function markPendingRowsAsError(message: string) {
    const unresolved: number[] = [];
    for (let i = 0; i < results.value.length; i += 1) {
      const row = results.value[i];
      if (row?.pending) unresolved.push(i);
    }
    if (!unresolved.length) return;

    for (const idx of unresolved) {
      const fallbackName = (seqList.value[idx]?.name || "").trim();
      results.value[idx] = {
        seq_name: fallbackName || `Seq ${idx + 1}`,
        seq_index: idx,
        error: message,
        pending: false,
        run_process: finishProcess(idx, { error: message }),
      };
    }

    doneCount.value += unresolved.length;
    failedCount.value += unresolved.length;
    touchEvent();
    const realPercent = Math.floor((doneCount.value / Math.max(1, totalCount.value)) * 100);
    progress.value = Math.max(progress.value, realPercent);
  }

  function pushResult(res: AlignmentResult) {
    const seqIdxRaw = Number(res.seq_index);
    const hasSlot =
      Number.isFinite(seqIdxRaw)
      && seqIdxRaw >= 0
      && seqIdxRaw < totalCount.value;

    if (hasSlot) {
      const seqIdx = seqIdxRaw;
      const fallbackName = (seqList.value[seqIdx]?.name || "").trim();
      results.value[seqIdx] = {
        ...res,
        seq_index: seqIdx,
        seq_name: (res.seq_name || fallbackName || `Seq ${seqIdx + 1}`).trim(),
        pending: false,
        run_process: finishProcess(seqIdx, res),
      };
    } else {
      results.value.push({ ...res, pending: false });
    }
    doneCount.value += 1;
    if (res.error) failedCount.value += 1;
    else if (String(res.agent?.work_state_summary?.disposition || "") === "abstained") {
      abstainedCount.value += 1;
    } else successCount.value += 1;
    touchEvent();

    const realPercent = Math.floor((doneCount.value / totalCount.value) * 100);
    progress.value = Math.max(progress.value, realPercent);
  }

  function normalizeWarnings(raw: unknown): string[] {
    return Array.isArray(raw) ? raw.map((w) => String(w)).filter(Boolean) : [];
  }

  function parseAlignmentOrThrow(rawCsv: string): { header: string[]; body: string[] } {
    const parsed = parseAlignmentCsv(rawCsv || "");
    if (!parsed) throw new Error("Alignment response missing CSV content.");
    return { header: parsed.header, body: parsed.body };
  }

  function objectOrUndefined(raw: unknown): Record<string, unknown> | undefined {
    return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : undefined;
  }

  function parseOptionalAlignment(
    rawCsv: string,
    agent: unknown,
    provenance: unknown,
  ): { header: string[]; body: string[] } | null {
    if (String(rawCsv || "").trim()) return parseAlignmentOrThrow(rawCsv);
    if (objectOrUndefined(agent) || objectOrUndefined(provenance)) return null;
    throw new Error("Alignment response missing CSV content and scientific status.");
  }

  function hasReferenceBaseline(row: AlignmentResult): boolean {
    return !!(
      Array.isArray(row.refNums)
      && Array.isArray(row.ref)
      && row.refNums.length > 0
      && row.refNums.length === row.ref.length
    );
  }

  function addRowWarning(row: AlignmentResult, message: string): void {
    const next = Array.isArray(row.warnings) ? [...row.warnings] : [];
    if (!next.includes(message)) next.push(message);
    row.warnings = next;
  }

  async function fetchTemplateReference(
    templateName: string,
    attempts = 1,
  ): Promise<{ header: string[]; body: string[] } | null> {
    const maxAttempts = Math.max(1, Math.min(Number(attempts) || 1, 3));
    for (let i = 0; i < maxAttempts; i += 1) {
      try {
        const res = await apiFetch(`/align/export/?template_name=${encodeURIComponent(templateName)}`);
        if (res.ok) {
          const j2 = (await res.json()) as ExportApiResponse;
          const parsedRef = parseTwoLineCsv(j2.csv_content || "");
          if (
            parsedRef
            && parsedRef.header.length > 0
            && parsedRef.header.length === parsedRef.body.length
          ) {
            return parsedRef;
          }
        }
      } catch {
        // retry below
      }
      if (i < maxAttempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, 120 * (i + 1)));
      }
    }
    return null;
  }

  async function attachReferencesInBulk(
    rows: AlignmentResult[],
    attempts = 1,
    emitWarningOnMissing = false,
  ): Promise<void> {
    const candidates = rows.filter(
      (r) =>
        needsTemplateReference(r)
        && !hasReferenceBaseline(r),
    );
    if (!candidates.length) return;

    const templateNames = Array.from(
      new Set(candidates.map((r) => String(r.template_name || "").trim()).filter(Boolean)),
    );
    if (!templateNames.length) return;

    const refMap = new Map<string, { header: string[]; body: string[] }>();
    await Promise.all(
      templateNames.map(async (name) => {
        const ref = await fetchTemplateReference(name, attempts);
        if (ref) refMap.set(name, ref);
      }),
    );
    for (const row of candidates) {
      if (hasReferenceBaseline(row)) continue;
      const name = String(row.template_name || "").trim();
      if (!name) continue;
      const ref = refMap.get(name);
      if (ref) {
        row.refNums = ref.header;
        row.ref = ref.body;
      } else if (emitWarningOnMissing) {
        addRowWarning(row, `Template baseline unavailable (${name}); showing target-only reference row.`);
      }
    }
  }

  function buildResultFromSingleResponse(seqName: string, seqIndex: number, data: AlignmentApiResponse): AlignmentResult {
    const parsed = parseOptionalAlignment(
      data.csv_content || "",
      data.agent,
      data.scientific_provenance,
    );
    return {
      seq_name: seqName,
      seq_index: seqIndex,
      template_name: data.template_name,
      nums: parsed?.header,
      seq: parsed?.body,
      warnings: normalizeWarnings(data.warnings),
      structure: normalizeStructureOverlay(data.structure),
      llm_trace:
        data.llm_trace && typeof data.llm_trace === "object"
          ? (data.llm_trace as Record<string, unknown>)
          : undefined,
      agent: objectOrUndefined(data.agent),
      scientific_provenance: objectOrUndefined(data.scientific_provenance),
    };
  }

  function buildResultFromBatchItem(seqName: string, seqIndex: number, item: AlignmentBatchApiItem): AlignmentResult {
    if (!item || !item.ok) {
      return {
        seq_name: seqName,
        seq_index: seqIndex,
        error: String(item?.error || `Unknown error (#${seqIndex + 1})`),
      };
    }
    try {
      const parsed = parseOptionalAlignment(
        item.csv_content || "",
        item.agent,
        item.scientific_provenance,
      );
      return {
        seq_name: seqName,
        seq_index: seqIndex,
        template_name: item.template_name,
        nums: parsed?.header,
        seq: parsed?.body,
        warnings: normalizeWarnings(item.warnings),
        structure: normalizeStructureOverlay(item.structure),
        llm_trace:
          item.llm_trace && typeof item.llm_trace === "object"
            ? (item.llm_trace as Record<string, unknown>)
            : undefined,
        agent: objectOrUndefined(item.agent),
        scientific_provenance: objectOrUndefined(item.scientific_provenance),
      };
    } catch (e: unknown) {
      return {
        seq_name: seqName,
        seq_index: seqIndex,
        error: getErrorMessage(e, `Invalid batch alignment payload (#${seqIndex + 1})`),
      };
    }
  }

  async function runAlignSerialByIndices(indices: number[]): Promise<void> {
    transportMode.value = "serial";
    runStage.value = `Running serial alignment for ${indices.length} sequence(s)`;
    for (const i of indices) {
      if (i < 0 || i >= seqList.value.length) continue;
      const item = seqList.value[i];
      const seqName = (item.name || "").trim();
      setCurrent(i, seqName || `Seq ${i + 1}`);
      runStage.value = `Aligning ${i + 1}/${totalCount.value}`;
      recordProcess(i, { stage: "started", at: new Date().toISOString(), source: "system" });
      try {
        // Mixed anticodons still get live, sequence-specific steps through one-item streams.
        let received = false;
        try {
          const stream = await apiFetch("/align/batch/stream", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_seqs: [item.seq], anticodon: item.anticode,
              use_llm: useLlm.value, llm_reasoning: llmReasoning.value,
              batch_max_workers: 1, ...buildAlignmentOptions() }),
          });
          if (!stream.ok) throw new Error(await readErrorMessage(stream));
          await readEventStream(stream, (event) => {
            if (!event) return;
            if (event.event === "status") {
              const status = JSON.parse(event.data) as AlignmentBatchStatusEvent;
              if (status.index === 0) recordProcess(i, status.process_event || {
                stage: String(status.stage || "working"), at: new Date().toISOString(), source: "tool",
              });
            } else if (event.event === "item" && !received) {
              const response = JSON.parse(event.data) as AlignmentBatchApiItem;
              if (response.index !== 0) return;
              pushResult(buildResultFromBatchItem(seqName, i, response));
              received = true;
            }
          });
        } catch {
          if (!received) recordProcess(i, { stage: "stream_reconnecting", at: new Date().toISOString(), source: "system" });
        }
        if (received) continue;
        recordProcess(i, { stage: "batch_waiting", at: new Date().toISOString(), source: "system" });
        const res1 = await apiFetch("/align/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target_seq: item.seq,
            anticodon: item.anticode,
            use_llm: useLlm.value,
            llm_reasoning: llmReasoning.value,
            ...buildAlignmentOptions(),
          }),
        });
        if (!res1.ok) throw new Error(await readErrorMessage(res1));
        const j1 = (await res1.json()) as AlignmentApiResponse;
        const aline = buildResultFromSingleResponse(seqName, i, j1);
        if (needsTemplateReference(aline)) {
          const ref = await fetchTemplateReference(String(aline.template_name), 1);
          if (ref) {
            aline.refNums = ref.header;
            aline.ref = ref.body;
          }
        }
        pushResult(aline);
      } catch (err: unknown) {
        pushResult({
          seq_name: seqName,
          seq_index: i,
          error: getErrorMessage(err, `Unknown error (#${i + 1})`),
        });
      }
    }
    currentIndex.value = null;
    currentSequence.value = "";
  }

  async function runAlignSerial(): Promise<void> {
    const indices = seqList.value.map((_, i) => i);
    await runAlignSerialByIndices(indices);
  }

  async function runAlignBatchStreamOrFallback(): Promise<void> {
    const anticodes = seqList.value
      .map((x) => String(x.anticode || "").trim())
      .filter(Boolean);
    const uniqueAnticodes = Array.from(new Set(anticodes));
    if (uniqueAnticodes.length > 1) {
      runStage.value = "Mixed anticodons detected, fallback to serial";
      await runAlignSerial();
      return;
    }

    const sharedAnticodon = uniqueAnticodes[0] || "";
    const seen = new Set<number>();
    transportMode.value = "sse";
    runStage.value = `Connecting live stream for ${seqList.value.length} sequence(s)`;

    try {
      const response = await apiFetch("/align/batch/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_seqs: seqList.value.map((x) => x.seq),
          anticodon: sharedAnticodon,
          use_llm: useLlm.value,
          llm_reasoning: llmReasoning.value,
          batch_max_workers: preferredBatchWorkers,
          ...buildAlignmentOptions(),
        }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      if (!response.body) throw new Error("Batch stream body is empty.");
      runStage.value = `Streaming alignment results (0/${totalCount.value})`;
      touchEvent();

      const handleEvent = (evt: ParsedSseEvent | null) => {
        if (!evt) return;

        if (evt.event === "meta") {
          try {
            const meta = JSON.parse(evt.data) as Record<string, unknown>;
            const workerCount = Number(meta.batch_workers);
            if (Number.isFinite(workerCount) && workerCount >= 0) {
              backendWorkers.value = workerCount;
            }
          } catch {
            // Ignore malformed meta payloads.
          }
          touchEvent();
          return;
        }

        if (evt.event === "status") {
          try {
            const status = JSON.parse(evt.data) as AlignmentBatchStatusEvent;
            const itemIndex = Number(status.index);
            if (Number.isInteger(itemIndex) && itemIndex >= 0 && itemIndex < seqList.value.length) {
              const processEvent = status.process_event || {
                stage: String(status.stage || "working"), at: new Date().toISOString(), source: "tool",
              };
              recordProcess(itemIndex, processEvent);
            }
            const stage = String(status.stage || "").trim();
            const message = String(status.message || "").trim();
            if (stage) {
              runStage.value = formatBackendStageLabel(stage);
            } else if (message) {
              runStage.value = message;
            }
            if (message) {
              backendStatus.value = message;
            }
            const pending = Number(status.pending);
            if (Number.isFinite(pending) && pending >= 0) {
              backendPending.value = pending;
            }
            const workers = Number(status.active_workers);
            if (Number.isFinite(workers) && workers >= 0) {
              backendWorkers.value = workers;
            }
            const current = Number(status.current_index);
            if (Number.isFinite(current) && current >= 0 && current < seqList.value.length) {
              const seqName = (seqList.value[current].name || "").trim();
              currentIndex.value = current;
              currentSequence.value = seqName || `Seq ${current + 1}`;
            }
          } catch {
            // Ignore malformed status payloads.
          }
          touchEvent();
          return;
        }

        if (evt.event === "heartbeat") {
          try {
            const heartbeat = JSON.parse(evt.data) as Record<string, unknown>;
            const message = String(heartbeat.message || "").trim();
            if (message) {
              backendStatus.value = message;
            }
            const pending = Number(heartbeat.pending);
            if (Number.isFinite(pending) && pending >= 0) {
              backendPending.value = pending;
            }
            const workers = Number(heartbeat.active_workers);
            if (Number.isFinite(workers) && workers >= 0) {
              backendWorkers.value = workers;
            }
          } catch {
            // Ignore malformed heartbeat payloads.
          }
          touchEvent();
          return;
        }

        if (evt.event === "done") {
          backendPending.value = 0;
          backendWorkers.value = 0;
          backendStatus.value = "Backend finished streaming all alignment rows.";
          touchEvent();
          return;
        }

        if (evt.event !== "item") return;
        let item: AlignmentBatchApiItem;
        try {
          item = JSON.parse(evt.data) as AlignmentBatchApiItem;
        } catch {
          return;
        }
        const idx = Number(item.index);
        if (!Number.isFinite(idx) || idx < 0 || idx >= seqList.value.length) return;
        if (seen.has(idx)) return;
        seen.add(idx);

        const seqName = (seqList.value[idx].name || "").trim();
        setCurrent(idx, seqName || `Seq ${idx + 1}`);
        runStage.value = `Received streamed result ${doneCount.value + 1}/${totalCount.value}`;
        const row = buildResultFromBatchItem(seqName, idx, item);
        pushResult(row);

        const tpl = String(row.template_name || "").trim();
        if (tpl && needsTemplateReference(row)) {
          void fetchTemplateReference(tpl, 1).then((ref) => {
            if (!ref) return;
            row.refNums = ref.header;
            row.ref = ref.body;
          });
        }
      };

      await readEventStream(response, handleEvent);
    } catch {
      if (seen.size === 0) {
        runStage.value = "Live stream unavailable, falling back to batch API";
        await runAlignBatchOrFallback();
        return;
      }
      runStage.value = "Live stream interrupted, completing missing rows serially";
    }

    const missing = seqList.value
      .map((_, i) => i)
      .filter((i) => !seen.has(i));
    if (missing.length) {
      runStage.value = `Completing ${missing.length} missing result(s) with serial fallback`;
      await runAlignSerialByIndices(missing);
    }
    currentIndex.value = null;
    currentSequence.value = "";
  }

  async function runAlignBatchOrFallback(): Promise<void> {
    const anticodes = seqList.value
      .map((x) => String(x.anticode || "").trim())
      .filter(Boolean);
    const uniqueAnticodes = Array.from(new Set(anticodes));
    if (uniqueAnticodes.length > 1) {
      runStage.value = "Mixed anticodons detected, fallback to serial";
      await runAlignSerial();
      return;
    }

    const sharedAnticodon = uniqueAnticodes[0] || "";
    let batchData: AlignmentBatchApiResponse | null = null;
    transportMode.value = "batch";
    runStage.value = `Calling batch API for ${seqList.value.length} sequence(s)`;
    seqList.value.forEach((_, i) => recordProcess(i, {
      stage: "batch_waiting", at: new Date().toISOString(), source: "system",
    }));
    try {
      const response = await apiFetch("/align/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_seqs: seqList.value.map((x) => x.seq),
          anticodon: sharedAnticodon,
          use_llm: useLlm.value,
          llm_reasoning: llmReasoning.value,
          batch_max_workers: preferredBatchWorkers,
          ...buildAlignmentOptions(),
        }),
      });
      if (!response.ok) throw new Error(await readErrorMessage(response));
      batchData = (await response.json()) as AlignmentBatchApiResponse;
      touchEvent();
    } catch {
      runStage.value = "Batch API unavailable, falling back to serial alignment";
      await runAlignSerial();
      return;
    }

    const items = Array.isArray(batchData?.results) ? batchData!.results! : [];
    const itemByIndex = new Map<number, AlignmentBatchApiItem>();
    for (let i = 0; i < items.length; i++) {
      const rec = items[i];
      const idx = Number(rec?.index);
      if (Number.isFinite(idx) && idx >= 0 && idx < seqList.value.length) itemByIndex.set(idx, rec);
      else if (!itemByIndex.has(i) && i < seqList.value.length) itemByIndex.set(i, rec);
    }

    const staged: AlignmentResult[] = [];
    for (let i = 0; i < seqList.value.length; i++) {
      const seqName = (seqList.value[i].name || "").trim();
      setCurrent(i, seqName || `Seq ${i + 1}`);
      runStage.value = `Parsing batch response ${i + 1}/${totalCount.value}`;
      const rec = itemByIndex.get(i);
      if (!rec) {
        staged.push({
          seq_name: seqName,
          seq_index: i,
          error: `Missing batch result (#${i + 1})`,
        });
        continue;
      }
      staged.push(buildResultFromBatchItem(seqName, i, rec));
    }

    await attachReferencesInBulk(staged.filter((x) => !x.error), 2, false);
    for (const row of staged) pushResult(row);
    currentIndex.value = null;
    currentSequence.value = "";
  }

  async function runAlign() {
    results.value = [];
    running.value = true;
    progress.value = 0;
    doneCount.value = 0;
    totalCount.value = seqList.value.length;
    successCount.value = 0;
    failedCount.value = 0;
    abstainedCount.value = 0;
    runStage.value = `Preparing ${totalCount.value} sequence(s)`;
    backendStatus.value = "";
    backendPending.value = 0;
    backendWorkers.value = 0;
    transportMode.value = "unknown";
    currentSequence.value = "";
    currentIndex.value = null;
    startedAtMs = Date.now();
    lastEventAtMs = startedAtMs;
    elapsedSeconds.value = 0;
    silenceSeconds.value = 0;
    resetRequestState();
    startStatusTicker();
    initializePlaceholderRows();

    clearProgressTimer();

    try {
      await runAlignBatchStreamOrFallback();
      markPendingRowsAsError("No alignment result returned for this sequence.");
      if (results.value.length) {
        runStage.value = `Finalizing ${results.value.length} result(s)`;
        await attachReferencesInBulk(results.value, 3, true);
        runStage.value = "Saving session metadata";
        // Session persistence is best-effort; do not block alignment completion.
        void saveSession(buildPayload(), results.value);
      }
      runStage.value = "Completed";
    } catch (err: unknown) {
      const msg = getErrorMessage(err, "Alignment failed.");
      if (totalCount.value > 0) markPendingRowsAsError(msg);
      else pushResult({ error: msg });
      runStage.value = "Completed with errors";
    }

    progress.value = 100;
    running.value = false;
    clearProgressTimer();
    clearStatusTimer();
    setTimeout(() => (progress.value = 0), 1000);
  }

  onBeforeUnmount(() => {
    clearProgressTimer();
    clearStatusTimer();
  });

  return {
    runAlign,
    clearProgressTimer,
    runStage,
    transportMode,
    currentSequence,
    currentIndex,
    doneCount,
    totalCount,
    successCount,
    failedCount,
    abstainedCount,
    backendStatus,
    backendPending,
    backendWorkers,
    elapsedSeconds,
    silenceSeconds,
  };
}
