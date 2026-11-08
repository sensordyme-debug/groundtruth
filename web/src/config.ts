import type { Bbox } from "./api";

export const EXAMPLE_QUERIES = ["find swimming pools", "parking lots in Uptown", "solar on warehouses"];
export const AGENT_DEMO_PROMPT = "pools within 1 km of I-485 in Ballantyne";
// Landing chips: the measured, demo-ready requests (each < 2 s warm). Solar/parking stay typeable but are experimental.
export const LANDING_CHIPS = [
  "find swimming pools", "pools within 1 km of I-485 in Ballantyne", "pools in flood zones in Matthews",
  "new pools since 2020 in Ballantyne", "boat docks on Lake Norman",
];
export const AGENT_CHIPS = [...LANDING_CHIPS, "parking lots in Uptown (experimental)", "solar on warehouses (experimental)"];
export const DEFAULT_PLACES = ["Ballantyne", "NoDa", "South End", "Myers Park", "Lake Norman", "University City", "Matthews", "Uptown"];

/** Context layer. Shown from vector tiles (GET /api/v1/vt) when the API lists the key, else as GeoJSON from add_layer.
 *  vectorOnly: no GeoJSON fallback. geojsonOnly: ignore the tiles (they do not match what add_layer shows).
 *  opacity / radius / edge override the default look (subtle Overture layers). */
export type LayerDef = {
  key: string; label: string; color: string; kind: "line" | "circle" | "fill"; bboxed?: boolean;
  vectorOnly?: boolean; geojsonOnly?: boolean; opacity?: number; radius?: number; edge?: string; attribution?: string;
};
const OVERTURE_ATTRIBUTION = "© OpenStreetMap contributors, Overture Maps Foundation";
export const CONTEXT_LAYERS: LayerDef[] = [
  { key: "osm_i485", label: "I-485", color: "#1457cc", kind: "line" },
  { key: "osm_i77", label: "I-77", color: "#5847b8", kind: "line" },
  { key: "osm_i85", label: "I-85", color: "#0f766e", kind: "line" },
  { key: "osm_light_rail", label: "Light rail (LYNX)", color: "#b42318", kind: "line" },
  { key: "osm_light_rail_stations", label: "Light-rail stations", color: "#b42318", kind: "circle" },
  { key: "fema_flood_zones", label: "Flood zones (FEMA)", color: "#1f8fc4", kind: "fill", bboxed: true },
  { key: "osm_unc_charlotte_campus", label: "UNC Charlotte campus", color: "#3b6b1e", kind: "fill" },
  { key: "overture_buildings", label: "Buildings (Overture)", color: "#6b6b68", kind: "fill", vectorOnly: true,
    opacity: 0.18, edge: "#ffffff", attribution: OVERTURE_ATTRIBUTION },
  { key: "overture_places", label: "Places (Overture)", color: "#8a5a00", kind: "circle", vectorOnly: true, radius: 2.5,
    attribution: OVERTURE_ATTRIBUTION },
];
export const layerLabel = (k: string) => CONTEXT_LAYERS.find((l) => l.key === k)?.label ?? k;

/** Layers offered in the "within … of" filter. */
export const NEAR_LAYERS = ["osm_i485", "osm_i77", "osm_light_rail", "fema_flood_zones"];

export const COLORS = {
  hit: "#ff6a2b",
  accepted: "#13794a",
  rejected: "#b42318",
  new: "#ff6a2b",
  existing: "#8e8e8c",
  unsure: "#e3b341",
  pick: "#191919",
};

/** "find swimming pools in Ballantyne" → { text: "swimming pools", place: "Ballantyne" } (case-insensitive place match). */
export function parseQuery(raw: string, places: Record<string, Bbox>): { text: string; place: string | null } {
  let text = raw.trim().replace(/^(please\s+)?(find|show( me)?|search( for)?|where are|look for)\s+/i, "").trim();
  let place: string | null = null;
  const m = text.match(/^(.*?)\s+(?:in|around|near)\s+(.+)$/i);
  if (m) {
    const hit = matchPlace(m[2], places);
    if (hit) { place = hit; text = m[1].trim(); }
  }
  return { text: text || raw.trim(), place };
}

export function matchPlace(q: string, places: Record<string, Bbox>): string | null {
  const s = q.trim().toLowerCase().replace(/^the\s+/, "");
  if (!s) return null;
  const names = Object.keys(places);
  return names.find((n) => n.toLowerCase() === s)
    ?? names.find((n) => n.toLowerCase().startsWith(s))
    ?? names.find((n) => n.toLowerCase().includes(s))
    ?? null;
}

export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
export const fmtAcres = (a: number | null | undefined) =>
  a == null ? "—" : a >= 10 ? a.toFixed(0) : a >= 1 ? a.toFixed(2) : a.toFixed(3);
export const fmtM2 = (m: number | null | undefined) => (m == null ? "—" : `${Math.round(m).toLocaleString()} m²`);
export const fmtM = (m: number | null | undefined) =>
  m == null ? "—" : m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
export const pretty = (s: string) => s.replace(/_/g, " ");
