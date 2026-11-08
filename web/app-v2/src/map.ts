// One MapLibre instance for the whole session: NAIP (local COGs via the API), reference layers, and the result set.
// Results are GeoJSON sources with zoom-dependent styling (DESIGN_TARGET "Results styling" + MEGA BUILD 1.4):
//   z ≤ 12 glowing 6 px dots · z13–15 18 px accent rings with a dark halo · z ≥ 16 outline + 25 % fill + halo (F0.3).
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
  onHover: (id: string | null, x: number, y: number) => void = () => {};
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
    // attribution: shown behind the map's "i" button (App), not as a MapLibre control
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
      paint: { "fill-color": "#2F6FED", "fill-opacity": 0.35, "fill-outline-color": "#2F6FED" } });
    m.addSource("vt-hwy", { type: "vector", tiles: [`${location.origin}/api/v1/vt/osm_motorways/{z}/{x}/{y}.pbf`], minzoom: 9, maxzoom: 15 });
    m.addLayer({ id: "hwy", type: "line", source: "vt-hwy", "source-layer": "osm_motorways", layout: { visibility: "none", "line-cap": "round" },
      paint: { "line-color": "#f4f1ea", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 16, 5], "line-opacity": 0.85 } });
    m.addSource("bldg", { type: "geojson", data: EMPTY as any });
    m.addLayer({ id: "bldg3d", type: "fill-extrusion", source: "bldg", layout: { visibility: "none" },
      paint: { "fill-extrusion-color": "#d9d5cc", "fill-extrusion-opacity": 0.85,
        "fill-extrusion-height": ["coalesce", ["get", "height_m"], 6], "fill-extrusion-base": 0 } as any });
    this.addResultLayers();
    // the chosen / drawn area (white dashed outline) and the "Find like this" hover preview
    for (const id of ["area", "draw", "hover"]) m.addSource(id, { type: "geojson", data: EMPTY as any });
    m.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#ffffff", "line-width": 2, "line-dasharray": [3, 2] } });
    m.addLayer({ id: "draw-fill", type: "fill", source: "draw", paint: { "fill-color": "#ffffff", "fill-opacity": 0.12 } });
    m.addLayer({ id: "draw-line", type: "line", source: "draw", paint: { "line-color": "#ffffff", "line-width": 2 } });
    m.addLayer({ id: "draw-pts", type: "circle", source: "draw", filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-radius": 4, "circle-color": "#ffffff", "circle-stroke-color": "#191919", "circle-stroke-width": 1.5 } });
    m.addLayer({ id: "hover-fill", type: "fill", source: "hover", paint: { "fill-color": ACCENT, "fill-opacity": 0.14 } });
    m.addLayer({ id: "hover-line", type: "line", source: "hover", paint: { "line-color": "#ffffff", "line-width": 2 } });
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
    // Zoom bands (F0.3): z ≤ 12 glowing 6 px dots · z13–15 18 px rings · z ≥ 16 outline + 25 % fill + dark halo.
    // A zoom interpolation must be the outermost expression, so look/dim/selection go inside its stops.
    const T = { dot: 700 };
    // docks (z13+): 4 px #F5A524 with a dark halo; the house → dock connector dashed 1.5 px
    m.addLayer({ id: "links", type: "line", source: "links", minzoom: 13, layout: { "line-cap": "round" },
      paint: { "line-color": DOCK, "line-width": 1.5, "line-dasharray": [2, 2], "line-opacity": ["*", 0.9, dim],
        "line-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "docks-halo", type: "line", source: "docks", minzoom: 13, layout: { "line-join": "round" },
      paint: { "line-color": HALO, "line-width": 7, "line-opacity": ["*", 0.45, dim], "line-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "docks", type: "line", source: "docks", minzoom: 13, layout: { "line-join": "round" },
      paint: { "line-color": DOCK, "line-width": 4, "line-opacity": dim, "line-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "docks-fill", type: "fill", source: "docks", minzoom: 13,
      paint: { "fill-color": DOCK, "fill-opacity": ["*", 0.85, dim], "fill-opacity-transition": { duration: T.dot } } as any });
    // z ≥ 16: outlines
    const polyOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 15.5, 0, 16, ["*", a, dim]];
    m.addLayer({ id: "res-fill", type: "fill", source: "res",
      paint: { "fill-color": ACCENT, "fill-opacity": polyOp(0.25) } as any });
    m.addLayer({ id: "res-halo", type: "line", source: "res", layout: { "line-join": "round" },
      paint: { "line-color": HALO, "line-width": 6, "line-opacity": polyOp(0.4) } as any });
    m.addLayer({ id: "res-line", type: "line", source: "res", layout: { "line-join": "round" },
      paint: { "line-color": ["case", sel, "#ffffff", ACCENT], "line-width": ["case", sel, 3, 2.5], "line-opacity": polyOp(1) } as any });
    // z ≤ 12 (and every zoom for the county overview "glow" look): 6 px dot + soft glow
    const dotOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 12.5, ["*", a, dim],
      13, ["case", glowLook, ["*", a, dim], 0]];
    m.addLayer({ id: "pts-glow", type: "circle", source: "res-pts",
      paint: { "circle-color": ACCENT, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 6, 13, 11], "circle-blur": 1,
        "circle-opacity": dotOp(0.55), "circle-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "pts-dot", type: "circle", source: "res-pts",
      paint: { "circle-color": ACCENT, "circle-radius": 3,
        "circle-stroke-color": "rgba(255,255,255,0.85)", "circle-stroke-width": 1.5,
        "circle-opacity": dotOp(1), "circle-stroke-opacity": dotOp(1), "circle-opacity-transition": { duration: T.dot } } as any });
    // z13–15: 18 px accent rings with a dark halo; the selected ring is white, 3 px
    const ringOn: any = ["case", glowLook, 0, dim];
    const ringOp = (a: number): any => ["interpolate", ["linear"], ["zoom"], 12.5, 0, 13, ["*", a, ringOn],
      15.5, ["*", a, ringOn], 16, 0];
    m.addLayer({ id: "pts-ringhalo", type: "circle", source: "res-pts",
      paint: { "circle-radius": 9, "circle-opacity": 0, "circle-stroke-color": "rgba(0,0,0,0.4)", "circle-stroke-width": 4.5,
        "circle-stroke-opacity": ringOp(1), "circle-stroke-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "pts-ring", type: "circle", source: "res-pts",
      paint: { "circle-radius": 9, "circle-opacity": 0, "circle-stroke-color": ["case", sel, "#ffffff", ACCENT],
        "circle-stroke-width": ["case", sel, 3, 2], "circle-stroke-opacity": ringOp(1),
        "circle-stroke-opacity-transition": { duration: T.dot } } as any });
    m.addLayer({ id: "pts-pulse", type: "circle", source: "res-pts", filter: ["to-boolean", ["get", "pulse"]],
      paint: { "circle-radius": 9, "circle-opacity": 0, "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
        "circle-stroke-opacity": 0 } as any });
    m.addLayer({ id: "pts-hit", type: "circle", source: "res-pts", filter: ["!", ["to-boolean", ["get", "dim"]]],
      paint: { "circle-radius": 12, "circle-opacity": 0.001, "circle-color": "#000" } });
    for (const id of ["pts-hit", "res-fill"]) {
      m.on("click", id, (e) => { const f = e.features?.[0]; if (f?.properties?.id) this.onOpen(String(f.properties.id)); });
      m.on("mouseenter", id, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", id, () => { m.getCanvas().style.cursor = ""; this.onHover(null, 0, 0); });
      m.on("mousemove", id, (e) => { const f = e.features?.[0]; if (f?.properties?.id) this.onHover(String(f.properties.id), e.point.x, e.point.y); });
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

  /** Two white pulses (1.3 s each, ease-out) on the matches: radius 9 → 25 px, opacity .85 → 0. */
  private pulse() {
    cancelAnimationFrame(this.pulseRaf);
    if (reduced()) return;
    const m = this.map, t0 = performance.now(), D = 1300;
    const tick = (t: number) => {
      const k = (t - t0) / D;
      if (k >= 2 || !m.getLayer("pts-pulse")) { m.setPaintProperty("pts-pulse", "circle-stroke-opacity", 0); return; }
      const p = 1 - Math.pow(1 - (k % 1), 3);
      m.setPaintProperty("pts-pulse", "circle-radius", 9 + 16 * p);
      m.setPaintProperty("pts-pulse", "circle-stroke-opacity", 0.85 * (1 - p));
      this.pulseRaf = requestAnimationFrame(tick);
    };
    this.pulseRaf = requestAnimationFrame(tick);
  }

  /** flyTo 1.6 s with the camera easing from the design. */
  flyToBbox(b: number[], padding = 48, maxZoom = 16.5) {
    const reduce = reduced();
    this.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding, maxZoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true });
  }

  flyToCenter(c: [number, number], zoom: number) {
    const reduce = reduced();
    this.map.flyTo({ center: c, zoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true });
  }

  flyToFeature(f: Feature, zoom = 18.2) {
    const c = centroid(focusGeom(f));
    const reduce = reduced();
    this.map.flyTo({ center: c, zoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true, offset: [-180, 0] });
  }

  zoom(by: number) { this.map.easeTo({ zoom: this.map.getZoom() + by, duration: 180 }); }

  // ---------------- camera state (sessions) ----------------
  camera(): { center: [number, number]; zoom: number } {
    const c = this.map.getCenter();
    return { center: [+c.lng.toFixed(6), +c.lat.toFixed(6)], zoom: +this.map.getZoom().toFixed(2) };
  }
  restoreCamera(c: { center: [number, number]; zoom: number }) {
    const reduce = reduced();
    this.map.flyTo({ center: c.center, zoom: c.zoom, duration: reduce ? 0 : 1600, easing: CAMERA_EASE, essential: true });
  }

  // ---------------- the chosen area ----------------
  async showArea(g: any | null, fly = true) {
    await this.ready;
    (this.map.getSource("area") as GeoJSONSource).setData(g ? { type: "Feature", geometry: g, properties: {} } as any : EMPTY as any);
    if (g && fly) this.flyToBbox(geomBbox(g), 48, 16);
  }

  // ---------------- drawing an area: rectangle = drag; polygon = click points, double-click / Enter to finish -------
  private drawOff: (() => void) | null = null;
  draw(kind: "rectangle" | "polygon"): Promise<any | null> {
    this.drawOff?.();
    const m = this.map, src = () => m.getSource("draw") as GeoJSONSource;
    m.getCanvas().style.cursor = "crosshair";
    return new Promise((resolve) => {
      const pts: [number, number][] = [];
      let start: [number, number] | null = null;
      const show = (g: any) => src().setData(g);
      const rect = (a: [number, number], b: [number, number]) => ({ type: "Polygon", coordinates: [[a, [b[0], a[1]], b, [a[0], b[1]], a]] });
      const poly = () => (pts.length >= 3 ? { type: "Polygon", coordinates: [[...pts, pts[0]]] } : null);
      const finish = (g: any | null) => { off(); show(EMPTY); resolve(g); };
      const down = (e: maplibregl.MapMouseEvent) => {
        if (kind !== "rectangle") return;
        e.preventDefault(); m.dragPan.disable(); start = [e.lngLat.lng, e.lngLat.lat];
      };
      const move = (e: maplibregl.MapMouseEvent) => {
        const c: [number, number] = [e.lngLat.lng, e.lngLat.lat];
        if (kind === "rectangle" && start) show({ type: "Feature", geometry: rect(start, c), properties: {} });
        if (kind === "polygon" && pts.length) show({ type: "FeatureCollection", features: [
          { type: "Feature", geometry: { type: "LineString", coordinates: [...pts, c] }, properties: {} },
          ...pts.map((q) => ({ type: "Feature", geometry: { type: "Point", coordinates: q }, properties: {} }))] });
      };
      const up = (e: maplibregl.MapMouseEvent) => {
        if (kind !== "rectangle" || !start) return;
        const b: [number, number] = [e.lngLat.lng, e.lngLat.lat];
        finish(Math.abs(b[0] - start[0]) > 1e-4 && Math.abs(b[1] - start[1]) > 1e-4 ? rect(start, b) : null);
      };
      const click = (e: maplibregl.MapMouseEvent) => { if (kind === "polygon") { pts.push([e.lngLat.lng, e.lngLat.lat]); move(e); } };
      const dbl = (e: maplibregl.MapMouseEvent) => { if (kind === "polygon") { e.preventDefault(); finish(poly()); } };
      const key = (e: KeyboardEvent) => {
        if (e.key === "Escape") finish(null);
        if (e.key === "Enter" && kind === "polygon") finish(poly());
      };
      const off = () => {
        m.off("mousedown", down); m.off("mousemove", move); m.off("mouseup", up); m.off("click", click); m.off("dblclick", dbl);
        removeEventListener("keydown", key); m.dragPan.enable(); m.doubleClickZoom.enable(); m.getCanvas().style.cursor = "";
        this.drawOff = null;
      };
      m.doubleClickZoom.disable();
      m.on("mousedown", down); m.on("mousemove", move); m.on("mouseup", up); m.on("click", click); m.on("dblclick", dbl);
      addEventListener("keydown", key);
      this.drawOff = () => finish(null);
    });
  }
  cancelDraw() { this.drawOff?.(); }

  // ---------------- "Find like this": crosshair + 17 m preview; click = example, shift-click = add, alt-drag = box ----
  private pointOff: (() => void) | null = null;
  pointMode(on: boolean, onPick: (p: { lon: number; lat: number } | { bbox: number[] }, add: boolean) => void) {
    this.pointOff?.();
    if (!on) return;
    const m = this.map, hover = () => m.getSource("hover") as GeoJSONSource;
    const sq = (lon: number, lat: number, half = 8.4) => {
      const dLat = half / 111320, dLon = half / (111320 * Math.cos((lat * Math.PI) / 180));
      return { type: "Polygon", coordinates: [[[lon - dLon, lat - dLat], [lon + dLon, lat - dLat], [lon + dLon, lat + dLat],
        [lon - dLon, lat + dLat], [lon - dLon, lat - dLat]]] };
    };
    let box: [number, number] | null = null;
    m.getCanvas().style.cursor = "crosshair";
    const move = (e: maplibregl.MapMouseEvent) => {
      const { lng, lat } = e.lngLat;
      const g = box ? { type: "Polygon", coordinates: [[box, [lng, box[1]], [lng, lat], [box[0], lat], box]] } : sq(lng, lat);
      hover().setData({ type: "Feature", geometry: g, properties: {} } as any);
    };
    const down = (e: maplibregl.MapMouseEvent) => {
      if (!e.originalEvent.altKey) return;
      e.preventDefault(); m.dragPan.disable(); box = [e.lngLat.lng, e.lngLat.lat];
    };
    const up = (e: maplibregl.MapMouseEvent) => {
      if (!box) return;
      const b = [Math.min(box[0], e.lngLat.lng), Math.min(box[1], e.lngLat.lat), Math.max(box[0], e.lngLat.lng), Math.max(box[1], e.lngLat.lat)];
      box = null; m.dragPan.enable();
      if (b[2] - b[0] > 2e-5 && b[3] - b[1] > 2e-5) onPick({ bbox: b }, e.originalEvent.shiftKey);
    };
    const click = (e: maplibregl.MapMouseEvent) => { if (!e.originalEvent.altKey) onPick({ lon: e.lngLat.lng, lat: e.lngLat.lat }, e.originalEvent.shiftKey); };
    const leave = () => hover().setData(EMPTY as any);
    m.on("mousemove", move); m.on("mousedown", down); m.on("mouseup", up); m.on("click", click); m.on("mouseout", leave);
    this.pointOff = () => {
      m.off("mousemove", move); m.off("mousedown", down); m.off("mouseup", up); m.off("click", click); m.off("mouseout", leave);
      m.dragPan.enable(); m.getCanvas().style.cursor = ""; hover().setData(EMPTY as any); this.pointOff = null;
    };
  }
  resize() { this.map.resize(); }
}

/** prefers-reduced-motion, or the app's "Reduce motion" setting. */
export const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches || !!(window as any).__gtReduce;

export function geomBbox(g: any): number[] {
  let w = 180, so = 90, e = -180, n = -90;
  const walk = (a: any) => {
    if (typeof a[0] === "number") { w = Math.min(w, a[0]); e = Math.max(e, a[0]); so = Math.min(so, a[1]); n = Math.max(n, a[1]); }
    else a.forEach(walk);
  };
  walk(g.coordinates);
  return [w, so, e, n];
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
