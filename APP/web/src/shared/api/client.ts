const rawPrefix = String(import.meta.env.VITE_API_PREFIX || "/api").trim();

function normalizePrefix(prefix: string): string {
  if (!prefix) return "/api";
  let out = prefix;
  if (!out.startsWith("/")) out = `/${out}`;
  out = out.replace(/\/+$/, "");
  return out || "/api";
}

const API_PREFIX = normalizePrefix(rawPrefix);

export function apiUrl(path: string): string {
  const raw = String(path || "").trim();
  if (!raw) return API_PREFIX;
  if (/^https?:\/\//i.test(raw)) return raw;
  const normalized = raw.startsWith("/") ? raw : `/${raw}`;
  if (normalized === API_PREFIX || normalized.startsWith(`${API_PREFIX}/`)) {
    return normalized;
  }
  return `${API_PREFIX}${normalized}`;
}

export function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(input), init);
}
