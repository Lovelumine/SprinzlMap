export function getErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export async function readErrorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data?.message) return String(data.message);
    return JSON.stringify(data);
  } catch {
    try {
      const text = await res.text();
      return text || `HTTP ${res.status}`;
    } catch {
      return `HTTP ${res.status}`;
    }
  }
}

export function parseTwoLineCsv(
  raw: string,
): { header: string[]; body: string[] } | null {
  const [header, body] = String(raw || "").trim().split(/\r?\n/);
  if (!header || !body) return null;
  return {
    header: header.split(","),
    body: body.split(","),
  };
}

export function parseAlignmentCsv(
  raw: string,
): { header: string[]; body: string[]; rows: string[][] } | null {
  const lines = String(raw || "")
    .trim()
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length < 2) return null;
  const rows = lines.map((line) => line.split(","));
  const header = rows[0] || [];
  const body = rows[rows.length - 1] || [];
  if (!header.length || !body.length) return null;
  return { header, body, rows };
}

export function normalizeSeq(raw: string): string {
  return (raw || "")
    .toUpperCase()
    .replace(/%20/gi, " ")
    .replace(/\s+/g, "")
    .replace(/T/g, "U")
    .replace(/[^AUGC]/g, "");
}

export function getSequenceFromSearch(search: string): string | null {
  try {
    const params = new URLSearchParams(search);
    const q = params.get("sequence");
    if (!q) return null;
    const decoded = decodeURIComponent(q);
    const norm = normalizeSeq(decoded);
    return norm.length ? norm : null;
  } catch {
    return null;
  }
}
