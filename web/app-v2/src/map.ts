// One MapLibre instance for the whole session: NAIP (local COGs via the API), reference layers, and the result set.
// Results are GeoJSON sources with zoom-dependent styling (DESIGN_TARGET "Results styling" + MEGA BUILD 1.4):
//   below z14 glowing accent dots · z14–16 accent rings (30 px, 2.5 px border, dark halo) · above z16 outline + fill + halo.
// Every change of look is a paint-property transition; the only animation loop is the two-beat pulse on matches.
import maplibregl, { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { FC, Feature } from "./api";
import { centroid, featId } from "./turns";

const EMPTY: FC = { type: "FeatureCollection", features: [] };
export const ACCENT = "#EC3E8C";
export const DOCK = "#F5A524";
const HALO = "#14110f";
export const CAMERA_EASE = (t: number) => cubicBezier(0.22, 0.8, 0.2, 1, t);
export const COUNTY: [number, number, number, number] = [-81.058, 35.002, -80.55, 35.515];

export type Look = "glow" | "rings";
export type ResultState = { features: Feature[]; docks: Feature[]; links: Feature[]; look: Look;
  /** ids still matching after a follow-up; the others fade to .16 (undefined = all match) */
  matches?: Set<string>; pulse?: boolean };

export class GtMap {
  map: MlMap;
  ready: Promise<void>;
  onOpen: (id: string) => void = () => {};
  private year = 2022;
  private sel: string | null = null;
  private pulseRaf = 0;

  constructor(el: HTMLElement) {
    this.map = new maplibregl.Map({
      container: el,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#6f8a55" } }] },
      bounds: COUNTY, fitBoundsOptions: { padding: 20 }, maxZoom: 19.5, attributionControl: false,
      fadeDuration: 200,
    });
    const m = this.map;
    m.addControl(new maplibregl.AttributionControl({ compact: true,
      customAttribution: "NAIP (USDA) · © OpenStreetMap contributors · FEMA NFHL · Overture Maps" }), "bottom-right");
    m.dragRotate.disable();
    m.touchZoomRotate.disableRotation();
    const w = window as any;
    w.__gtMap = m;
    m.on("idle", () => { w.__gtMapIdle = true; });
    m.on("dataloading", () => { w.__gtMapIdle = false; });
    this.ready = new Promise((res) => m.on("load", () => { this.setup(); res(); }));
  }

  private setup() {
    const m = this.map;
    // imagery starts with the first question (startImagery): on the landing the map is hidden, and its tile requests
    // would hold the browser's connections while the first chat request waits
    m.addLayer({ id: "anchor-ref", type: "background", paint: { "background-opacity": 0 } });
    // reference layers (frosted layers popover): vector tiles from the API
    m.addSource("vt-flood", { type: "vector", tiles: [`${location.origin}/api/v1/vt/fema_flood_zones/{z}/{x}/{y}.pbf`], minzoom: 9, maxzoom: 15 });
    m.addLayer({ id: "flood", type: "fill", source: "vt-flood", "source-layer": "fema_flood_zones", layout: { visibility: "none" },
      paint: { "fill-color": "#4aa3df", "fill-opacity": 0.28, "fill-outline-color": "#2f7fb8" } });
    m.addSource("vt-hwy", { type: "vector", tiles: [`${location.origin}/api/v1/vt/osm_motorways/{z}/{x}/{y}.pbf`], minzoom: 9, maxzoom: 15 });
    m.addLayer({ id: "hwy", type: "line", source: "vt-hwy", "source-layer": "osm_motorways", layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": "#f4f1ea", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 16, 5], "line-opacity": 0.85 } });
    m.addSource("bldg", { type: "geojson", data: EMPTY as any });
    m.addLayer({ id: "bldg3d", type: "fill-extrusion", source: "bldg", layout: { visibility: "none" },
      paint: { "fill-extrusion-color": "#d9d5cc", "fill-extrusion-opacity": 0.85,
        "fill-extrusion-height": ["coalesce", ["get", "height_m"], 6], "fill-extrusion-base": 0 } as any });
    this.addResultLayers();
  }

  private addImagery(year: number) {
    const m = this.map;
    const before = m.getLayer("anchor-ref") ? "anchor-ref" : undefined;
    if (m.getLayer("naip")) m.removeLayer("naip");
    if (m.getSource("naip")) m.removeSource("naip");
    m.addSource("naip", { type: "raster", tiles: [`/api/v1/tiles/naip/${year}/{z}/{x}/{y}.png`], tileSize: 256, minzoom: 10, maxzoom: 18 });
    // spotlight: imagery brightness drops 22 % while results are on screen (raster-brightness-max transitions)
    m.addLayer({ id: "naip", type: "raster", source: "naip",
      paint: { "raster-fade-duration": 200, "raster-brightness-max": this.dimmed ? 0.78 : 1,
        "raster-brightness-max-transition": { duration: 700, delay: 0 },
        "raster-saturation": year === 2020 ? -0.15 : 0 } as any }, before);
    this.year = year;
  }

  private dimmed = false;
  spotlight(on: boolean) {
    this.dimmed = on;
    if (this.map.getLayer("naip")) this.map.setPaintProperty("naip", "raster-brightness-max", on ? 0.78 : 1);
  }

  private imagery = false;
  async startImagery() {
    await this.ready;
    if (!this.imagery) { this.imagery = true; this.addImagery(this.year); }
  }

  async setYear(year: number) {
    await this.ready;
    if (!this.imagery) { this.year = year; return; }
    if (year !== this.year) this.addImagery(year);
  }

  async setLayer(id: "flood" | "hwy" | "bldg3d", on: boolean) {
    await this.ready;
    const m = this.map;
    if (id === "bldg3d" && on) {
      const b = m.getBounds();
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
      if ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 0.0025) {
        const r = await fetch("/api/v1/tools/find_buildings", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bbox, limit: 8000 }) }).then((x) => x.json()).catch(() => null);
        if (r?.result) (m.getSource("bldg") as GeoJSONSource).setData(r.result.geojson);
      }
      m.easeTo({ pitch: 50, duration: 900 });
    } else if (id === "bldg3d") m.easeTo({ pitch: 0, duration: 700 });
    m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }

  // ---------------- results ----------------
  private addResultLayers() {
    const m = this.map;
    for (const s of ["res", "res-pts", "docks", "links"]) m.addSource(s, { type: "geojson", data: EMPTY as any, promoteId: "id" });
    const dim: any = ["case", ["to-boolean", ["get", "dim"]], 0.16, 1];
    const sel: any = ["to-boolean", ["get", "sel"]];
    const glowLook: any = ["==", ["get", "look"], "glow"];
    // dashed connector house → dock, then the dock bars (second accent)
    m.addLayer({ id: "links", type: "line", source: "links", layout: { "line-cap": "round" },
      paint: { "line-color": DOCK, "line-width": 1.5, "line-dasharray": [2, 2], "line-opacity": ["*", 0.85, dim],
        "line-opacity-transition": { duration: 700 } } as any });
    m.addLayer({ id: "docks-halo", type: "line", source: "docks",
      paint: { "line-color": HALO, "line-width": 4, "line-opacity": ["*", 0.28, dim], "line-opacity-transition": { duration: 700 } } as any });
    m.addLayer({ id: "docks", type: "fill", source: "docks",
      paint: { "fill-color": DOCK, "fill-opacity": ["*", 0.9, dim], "fill-opacity-transition": { duration: 700 } } as any });
    // above z16: outline + 25 % fill + dark halo
    const polyOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 15.6, 0, 16.2, ["*", a, dim]];
    m.addLayer({ id: "res-fill", type: "fill", source: "res",
      paint: { "fill-color": ACCENT, "fill-opacity": polyOp(0.25) } as any });
    m.addLayer({ id: "res-halo", type: "line", source: "res", layout: { "line-join": "round" },
      paint: { "line-color": HALO, "line-width": 6, "line-opacity": polyOp(0.35) } as any });
    m.addLayer({ id: "res-line", type: "line", source: "res", layout: { "line-join": "round" },
      paint: { "line-color": ["case", sel, "#ffffff", ACCENT], "line-width": ["case", sel, 3.5, 2.5], "line-opacity": polyOp(1) } as any });
    // below z14 (and always for the county "glow" look): blurred accent glow + 10 px dot with a white edge
    // (a zoom interpolation must be the outermost expression: the look/dim cases go inside its stops)
    const dotOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 13.6, ["*", a, dim],
      14.1, ["case", glowLook, ["*", a, dim], 0]];
    m.addLayer({ id: "pts-glow", type: "circle", source: "res-pts",
      paint: { "circle-color": ACCENT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 7, 14, 14], "circle-blur": 1,
        "circle-opacity": dotOp(0.6), "circle-opacity-transition": { duration: 700 } } as any });
    m.addLayer({ id: "pts-dot", type: "circle", source: "res-pts",
      paint: { "circle-color": ACCENT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 3, 14, 5],
        "circle-stroke-color": "rgba(255,255,255,0.85)", "circle-stroke-width": 2,
        "circle-opacity": dotOp(1), "circle-stroke-opacity": dotOp(1), "circle-opacity-transition": { duration: 700 } } as any });
    // z14–16: 30 px accent rings (2.5 px border) with a dark halo; the selected ring turns white
    const ringOn: any = ["case", glowLook, 0, dim];
    const ringOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 13.6, 0, 14.1, ["*", a, ringOn],
      16, ["*", a, ringOn], 16.6, 0];
    m.addLayer({ id: "pts-ringhalo", type: "circle", source: "res-pts",
      paint: { "circle-radius": 15, "circle-opacity": 0, "circle-stroke-color": "rgba(0,0,0,0.35)", "circle-stroke-width": 4.5,
        "circle-stroke-opacity": ringOp(1), "circle-stroke-opacity-transition": { duration: 700 } } as any });
    m.addLayer({ id: "pts-ring", type: "circle", source: "res-pts",
      paint: { "circle-radius": 13.75, "circle-opacity": 0, "circle-stroke-color": ["case", sel, "#ffffff", ACCENT],
        "circle-stroke-width": ["case", sel, 3.5, 2.5], "circle-stroke-opacity": ringOp(1),
        "circle-stroke-opacity-transition": { duration: 700 } } as any });
    m.addLayer({ id: "pts-pulse", type: "circle", source: "res-pts", filter: ["to-boolean", ["get", "pulse"]],
      paint: { "circle-radius": 15, "circle-opacity": 0, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
        "circle-stroke-opacity": 0 } as any });
    m.addLayer({ id: "pts-hit", type: "circle", source: "res-pts", filter: ["!", ["to-boolean", ["get", "dim"]]],
      paint: { "circle-radius": 16, "circle-opacity": 0.001, "circle-color": "#000" } });
    for (const id of ["pts-hit", "res-fill"]) {
      m.on("click", id, (e) => { const f = e.features?.[0]; if (f?.properties?.id) this.onOpen(String(f.properties.id)); });
      m.on("mouseenter", id, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", id, () => { m.getCanvas().style.cursor = ""; });
    }
  }

  private last: ResultState | null = null;

  async setResults(st: ResultState | null) {
    await this.ready;
    this.last = st;
    const m = this.map;
    const src = (id: string) => m.getSource(id) as GeoJSONSource;
    if (!st) {
      for (const s of ["res", "res-pts", "docks", "links"]) src(s).setData(EMPTY as any);
      this.spotlight(false);
      return;
    }
    const tag = (f: Feature) => {
      const id = featId(f);
      const isDim = st.matches ? !st.matches.has(id) && !st.matches.has(String(f.properties?.house_id ?? "")) : false;
      return { ...f.properties, id, look: st.look, dim: isDim, sel: id === this.sel, pulse: !!st.pulse && !isDim };
    };
    // close zoom outlines the object itself: the pool for a house with a pool, else the result's own shape
    const polys = st.features.filter((f) => f.geometry).map((f) => ({ type: "Feature", geometry: focusGeom(f), properties: tag(f) }))
      .filter((f) => f.geometry.type !== "Point");
    src("res").setData({ type: "FeatureCollection", features: polys } as any);
    src("res-pts").setData({ type: "FeatureCollection", features: st.features.filter((f) => f.geometry).map((f) => ({
      type: "Feature", geometry: { type: "Point", coordinates: centroid(focusGeom(f)) },
      properties: tag(f) })) } as any);
    src("docks").setData({ type: "FeatureCollection", features: st.docks.map((f) => ({ ...f, properties: tag(f) })) } as any);
    src("links").setData({ type: "FeatureCollection", features: st.links.map((f) => ({ ...f, properties: tag(f) })) } as any);
    this.spotlight(st.features.length > 0);
    if (st.pulse) this.pulse();
  }

  select(id: string | null) {
    this.sel = id;
    if (this.last) void this.setResults({ ...this.last, pulse: false });
  }

  /** Two white pulses (1.3 s each, ease-out) on the matches: radius 15 → 31 px, opacity .85 → 0. */
  private pulse() {
    cancelAnimationFrame(this.pulseRaf);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const m = this.map, t0 = performance.now(), D = 1300;
    const tick = (t: number) => {
      const k = (t - t0) / D;
      if (k >= 2 || !m.getLayer("pts-pulse")) { m.setPaintProperty("pts-pulse", "circle-stroke-opacity", 0); return; }
      const p = 1 - Math.pow(1 - (k % 1), 3);
      m.setPaintProperty("pts-pulse", "circle-radius", 15 + 16 * p);
      m.setPaintProperty("pts-pulse", "circle-stroke-opacity", 0.85 * (1 - p));
      this.pulseRaf = requestAnimationFrame(tick);
    };
    this.pulseRaf = requestAnimationFrame(tick);
  }

  /** flyTo 1.6 s with the camera easing from the design. */
  flyToBbox(b: number[], padding = 48, maxZoom = 16.5) {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding, maxZoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true });
  }

  flyToFeature(f: Feature, zoom = 18.2) {
    const c = centroid(focusGeom(f));
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.map.flyTo({ center: c, zoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true, offset: [-180, 0] });
  }

  zoom(by: number) { this.map.easeTo({ zoom: this.map.getZoom() + by, duration: 180 }); }
  resize() { this.map.resize(); }
}

/** The geometry a result is "about": its (first) pool for a house with a pool, else its own geometry. */
export function focusGeom(f: Feature): any {
  const pg = f.properties?.pool_geometries;
  return Array.isArray(pg) && pg.length ? pg[0] : f.geometry;
}

/** CSS cubic-bezier(x1, y1, x2, y2) evaluated at t (Newton on x). */
function cubicBezier(x1: number, y1: number, x2: number, y2: number, t: number): number {
  const bx = (s: number, a: number, b: number) => 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3;
  let s = t;
  for (let i = 0; i < 8; i++) {
    const x = bx(s, x1, x2) - t;
    const d = 3 * x1 * (1 - s) ** 2 + 6 * (x2 - x1) * s * (1 - s) + 3 * (1 - x2) * s * s;
    if (Math.abs(x) < 1e-5 || d === 0) break;
    s -= x / d;
  }
  return bx(Math.min(1, Math.max(0, s)), y1, y2);
}
