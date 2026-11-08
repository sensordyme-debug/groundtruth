// Imperative MapLibre wrapper: NAIP imagery, county boundary, context layers, results (outline polygons or
// cell boxes), the picked examples, and agent-added layers. React state pushes data in; clicks come back out.
import maplibregl, { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import type { Bbox, FC } from "./api";
import { COLORS, CONTEXT_LAYERS, LayerDef } from "./config";

const EMPTY: FC = { type: "FeatureCollection", features: [] };
/** One entry of GET /api/v1/vt: gzip MVT tiles, source-layer name == layer key. */
type VtLayer = { minzoom: number; maxzoom: number; url: string };

export class MapView {
  map: MlMap;
  private ready: Promise<void>;
  private year = 0;
  private vt: Record<string, VtLayer> = {};
  onResultClick: (example: string) => void = () => {};

  constructor(container: HTMLElement) {
    this.map = new maplibregl.Map({
      container,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: "bg", type: "background", paint: { "background-color": "#e9e9e6" } }],
      },
      center: [-80.825, 35.058], zoom: 13.2, maxZoom: 19.5, attributionControl: false,
    });
    const m = this.map;
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: false, showCompass: false }), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    m.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: "NAIP (USDA, public domain) · © OpenStreetMap contributors · FEMA NFHL · US Census",
    }));
    // Bench hook (scripts/ui_bench.py): the map and the time of every 'idle' event, nothing else.
    const w = window as any;
    w.__gtMap = m; w.__gtIdleTimes = [];
    m.on("idle", () => { w.__gtMapIdle = true; w.__gtIdleTimes.push(performance.now()); });
    m.on("dataloading", () => { w.__gtMapIdle = false; });
    m.dragRotate.disable();
    m.touchZoomRotate.disableRotation();
    // Vector-tile catalogue; an older API without /vt (or an error) just means every layer falls back to GeoJSON.
    const vt: Promise<Record<string, VtLayer>> = fetch("/api/v1/vt").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
    const loaded = new Promise<void>((res) => m.on("load", () => { this.setup(); res(); }));
    this.ready = Promise.all([vt, loaded]).then(([v]) => { this.vt = v && typeof v === "object" ? v : {}; });
  }

  private setup() {
    const m = this.map;
    m.addSource("boundary", { type: "geojson", data: "/api/v1/boundary" });
    m.addLayer({ id: "anchor-imagery", type: "background", paint: { "background-opacity": 0 } });
    m.addLayer({ id: "boundary", type: "line", source: "boundary",
      paint: { "line-color": "#ffffff", "line-width": 1.4, "line-dasharray": [3, 2], "line-opacity": 0.9 } });
    m.addLayer({ id: "anchor-context", type: "background", paint: { "background-opacity": 0 } });
    for (const id of ["results", "pick", "agent"]) m.addSource(id, { type: "geojson", data: EMPTY as any });
    const color: any = ["case",
      ["==", ["get", "state"], "accepted"], COLORS.accepted,
      ["==", ["get", "change"], "new"], COLORS.new,
      ["==", ["get", "change"], "existing"], COLORS.existing,
      ["==", ["get", "change"], "unsure"], COLORS.unsure,
      COLORS.hit];
    m.addLayer({ id: "results-fill", type: "fill", source: "results", filter: ["==", ["get", "kind"], "outline"],
      paint: { "fill-color": color, "fill-opacity": ["case", ["get", "dim"], 0.08, 0.38] } });
    m.addLayer({ id: "results-line", type: "line", source: "results", filter: ["==", ["get", "kind"], "outline"],
      paint: { "line-color": color, "line-width": ["interpolate", ["linear"], ["zoom"], 14, 1.2, 18, 2.4],
        "line-opacity": ["case", ["get", "dim"], 0.35, 1] } });
    m.addLayer({ id: "results-cell", type: "line", source: "results", filter: ["==", ["get", "kind"], "cell"],
      paint: { "line-color": color, "line-width": 1.4, "line-dasharray": [2, 1.5],
        "line-opacity": ["case", ["get", "dim"], 0.3, 0.95] } });
    m.addLayer({ id: "results-hit", type: "fill", source: "results", paint: { "fill-color": "#000", "fill-opacity": 0.001 } });
    m.addLayer({ id: "results-sel", type: "line", source: "results", filter: ["==", ["get", "sel"], true],
      paint: { "line-color": "#191919", "line-width": 3.2 } });
    m.addLayer({ id: "pick", type: "line", source: "pick", paint: { "line-color": "#ffffff", "line-width": 3 } });
    m.addLayer({ id: "pick-inner", type: "line", source: "pick", paint: { "line-color": COLORS.pick, "line-width": 1.5 } });
    m.addLayer({ id: "agent-line", type: "line", source: "agent", paint: { "line-color": "#5847b8", "line-width": 2 } });
    m.on("click", "results-hit", (e) => {
      const ex = e.features?.[0]?.properties?.example;
      if (ex) this.onResultClick(ex);
    });
    m.on("mouseenter", "results-hit", () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", "results-hit", () => (m.getCanvas().style.cursor = ""));
  }

  whenReady() { return this.ready; }

  async setYear(year: number) {
    await this.ready;
    if (year === this.year) return;
    this.year = year;
    const m = this.map;
    if (m.getLayer("naip")) m.removeLayer("naip");
    if (m.getSource("naip")) m.removeSource("naip");
    m.addSource("naip", { type: "raster", tiles: [`/api/v1/tiles/naip/${year}/{z}/{x}/{y}.png`], tileSize: 256, minzoom: 10, maxzoom: 18 });
    m.addLayer({ id: "naip", type: "raster", source: "naip", paint: { "raster-fade-duration": 200 } }, "anchor-imagery");
  }

  async setResults(fc: FC) {
    await this.ready;
    (this.map.getSource("results") as GeoJSONSource).setData(fc as any);
  }

  async setPick(fc: FC) {
    await this.ready;
    (this.map.getSource("pick") as GeoJSONSource).setData(fc as any);
  }

  /** True when the API serves this context layer as vector tiles (then no GeoJSON fetch is needed). */
  async hasVector(key: string) {
    await this.ready;
    return key in this.vt && !CONTEXT_LAYERS.find((l) => l.key === key)?.geojsonOnly;
  }

  /** Context layer: vector tiles when /api/v1/vt lists it, else the GeoJSON passed in (fallback). */
  async setContext(def: LayerDef, data: FC | null, visible: boolean) {
    await this.ready;
    const m = this.map;
    const id = `ctx-${def.key}`;
    const vt = def.geojsonOnly ? undefined : this.vt[def.key];
    if (vt) {
      // Tiles cover the whole county, so any envelope GeoJSON (agent add_layer) is already on screen.
      if (visible && !m.getSource(id)) {
        m.addSource(id, { type: "vector", tiles: [`${window.location.origin}${vt.url}`], minzoom: vt.minzoom,
          maxzoom: vt.maxzoom, ...(def.attribution ? { attribution: def.attribution } : {}) });
        this.addContextLayers(def, id, def.key);
      }
    } else if (data) {
      if (m.getSource(id)) (m.getSource(id) as GeoJSONSource).setData(data as any);
      else {
        m.addSource(id, { type: "geojson", data: data as any, ...(def.attribution ? { attribution: def.attribution } : {}) });
        this.addContextLayers(def, id);
      }
    }
    for (const lid of [id, `${id}-edge`, `${id}-casing`])
      if (m.getLayer(lid)) m.setLayoutProperty(lid, "visibility", visible ? "visible" : "none");
  }

  private addContextLayers(def: LayerDef, id: string, sourceLayer?: string) {
    const m = this.map;
    const src: any = sourceLayer ? { source: id, "source-layer": sourceLayer } : { source: id };
    if (def.kind === "fill") {
      m.addLayer({ id, type: "fill", ...src, paint: { "fill-color": def.color, "fill-opacity": def.opacity ?? 0.4 } }, "anchor-context");
      m.addLayer({ id: `${id}-edge`, type: "line", ...src, paint: { "line-color": def.edge ?? "#e8f6ff", "line-width": 1,
        "line-opacity": def.opacity != null ? Math.min(1, def.opacity * 2) : 0.8 } }, "anchor-context");
    } else if (def.kind === "circle") {
      const r = def.radius ?? 5;
      m.addLayer({ id, type: "circle", ...src, paint: { "circle-color": def.color, "circle-radius": r,
        "circle-stroke-color": "#fff", "circle-stroke-width": r < 4 ? 0.8 : 1.5 } });
    } else {
      m.addLayer({ id: `${id}-casing`, type: "line", ...src, paint: { "line-color": "#ffffff", "line-width": 5.5, "line-opacity": 0.85 },
        layout: { "line-cap": "round", "line-join": "round" } }, "anchor-context");
      m.addLayer({ id, type: "line", ...src, paint: { "line-color": def.color, "line-width": 3 },
        layout: { "line-cap": "round", "line-join": "round" } }, "anchor-context");
    }
  }

  /** Agent-provided layer (add_layer with source "envelope"). Known context keys reuse their style. */
  async setAgentLayer(layerId: string, data: FC, style?: Record<string, any>) {
    const known = CONTEXT_LAYERS.find((l) => l.key === layerId || `osm_${layerId}` === l.key);
    if (known) return this.setContext(known, data, true);
    await this.ready;
    (this.map.getSource("agent") as GeoJSONSource).setData(data as any);
    if (style?.color) this.map.setPaintProperty("agent-line", "line-color", style.color);
  }

  fitBbox(b: Bbox | number[], opts: { maxZoom?: number; padding?: number; duration?: number } = {}) {
    const [w, s, e, n] = b;
    this.map.fitBounds([[w, s], [e, n]], { padding: opts.padding ?? 60, maxZoom: opts.maxZoom ?? 17, duration: opts.duration ?? 900 });
  }

  bounds(): Bbox {
    const b = this.map.getBounds();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  }

  resize() { this.map.resize(); }
  remove() { this.map.remove(); }
}

export function unionBbox(boxes: number[][]): Bbox | null {
  if (!boxes.length) return null;
  let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const b of boxes) { w = Math.min(w, b[0]); s = Math.min(s, b[1]); e = Math.max(e, b[2]); n = Math.max(n, b[3]); }
  return [w, s, e, n];
}

export function inside(b: number[], view: Bbox) {
  return b[2] >= view[0] && b[0] <= view[2] && b[3] >= view[1] && b[1] <= view[3];
}
