// Client side of sessions / saved areas / pins (api/sessions.py) + per-browser preferences (localStorage, guarded).
export type SessionRow = { id: string; title: string; created: number; updated: number; status?: string | null; scope?: string | null };
export type SavedArea = { id: string; name: string; geometry: any; created: number };
export type Pin = { id: string; session_id: string; msg_id: string; title: string; created: number };
export type Area = { label: string; place?: string; bbox?: number[]; geojson?: any } | null;
export type Year = "2022" | "2020" | "compare";
export type Mode = "find" | "measure" | "compare";
export type Prefs = { year: 2022 | 2020; units: "m" | "ft"; reduceMotion: boolean; sidebar: "open" | "rail" };

const PREFS_KEY = "gt.prefs.v1";
const OWNER_KEY = "gt.owner.v1";

export function lsGet<T>(k: string, d: T): T {
  try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; }
}
export function lsSet(k: string, v: unknown) {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window: preferences just don't persist */ }
}

let _owner: string | null = null;
/** Anonymous browser id (random UUID). No login, nothing about the person. */
export function owner(): string {
  if (_owner) return _owner;
  let o = lsGet<string | null>(OWNER_KEY, null);
  if (!o || !/^[A-Za-z0-9-]{8,64}$/.test(o)) {
    o = (crypto as any).randomUUID ? crypto.randomUUID() : `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    lsSet(OWNER_KEY, o);
  }
  _owner = o;
  return o;
}

export const defaultPrefs: Prefs = { year: 2022, units: "m", reduceMotion: false, sidebar: "open" };
export const loadPrefs = (): Prefs => ({ ...defaultPrefs, ...lsGet<Partial<Prefs>>(PREFS_KEY, {}) });
export const savePrefs = (p: Prefs) => lsSet(PREFS_KEY, p);

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1${path}`, { method, headers: { "Content-Type": "application/json", "X-GT-Owner": owner() },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw new Error(`${method} ${path}: HTTP ${r.status}`);
  return r.json();
}

export const api = {
  sessions: (limit = 20, q = "") => call<{ sessions: SessionRow[]; total: number }>("GET", `/sessions?limit=${limit}&q=${encodeURIComponent(q)}`),
  investigations: () => call<{ investigations: any[] }>("GET", "/investigations"),
  session: (id: string) => call<SessionRow & { data: any }>("GET", `/sessions/${id}`),
  create: (title: string, data: any) => call<SessionRow>("POST", "/sessions", { title, data }),
  patch: (id: string, p: { title?: string; data?: any }) => call<{ id: string }>("PATCH", `/sessions/${id}`, p),
  remove: (id: string) => call<{ deleted: string }>("DELETE", `/sessions/${id}`),
  areas: () => call<{ areas: SavedArea[] }>("GET", "/areas"),
  addArea: (name: string, geometry: any) => call<{ id: string }>("POST", "/areas", { name, geometry }),
  delArea: (id: string) => call<{ deleted: string }>("DELETE", `/areas/${id}`),
  pins: () => call<{ pins: Pin[] }>("GET", "/pins"),
  addPin: (session_id: string, msg_id: string, title: string) => call<{ id: string }>("POST", "/pins", { session_id, msg_id, title }),
  delPin: (id: string) => call<{ deleted: string }>("DELETE", `/pins/${id}`),
};

/** Session title by rules: "Lake Norman · houses with pools" (place · what), from the first answered request. */
export function ruleTitle(text: string, plan: Record<string, any> | undefined, area: Area): string {
  const place = plan?.place ?? area?.label ?? null;
  let what = String(plan?.concept_query ?? plan?.target ?? text).replace(/_/g, " ").trim();
  if (plan?.target_kind === "derived") what = "houses with pools";
  what = what.replace(/\b(in|on|around|near) (lake norman|ballantyne|myers park|[a-z ]+)$/i, "").trim() || text;
  const t = place ? `${place} · ${what}` : what;
  return t.length > 60 ? `${t.slice(0, 57)}…` : t.replace(/^\w/, (c) => c.toUpperCase());
}

export function relTime(sec: number): string {
  const d = Date.now() / 1000 - sec;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

/** Units preference on display strings: "70 m" → "230 ft", "41 m²" → "441 ft²" (engine values stay metric). */
export function units(s: string, u: "m" | "ft"): string {
  if (u === "m" || !s) return s;
  return s.replace(/(\d[\d,]*(?:\.\d+)?)\s?m²/g, (_, n) => `${Math.round(parseFloat(n.replace(/,/g, "")) * 10.7639).toLocaleString("en-US")} ft²`)
    .replace(/(\d[\d,]*(?:\.\d+)?)\s?m\b/g, (_, n) => `${Math.round(parseFloat(n.replace(/,/g, "")) * 3.28084).toLocaleString("en-US")} ft`);
}
