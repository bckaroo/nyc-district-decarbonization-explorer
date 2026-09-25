/**
 * Static data layer for the GitHub Pages build.
 *
 * GitHub Pages cannot run the FastAPI backend, so this module backs the same
 * app against pre-baked JSON instead. It is inert on the live server: every
 * entry point first tries the API and only falls back here when the API is
 * genuinely absent (a static host returns 404/HTML for /api/*). That ordering
 * matters — otherwise the static path would shadow the real backend and the
 * tailnet deployment would silently serve stale baked data.
 *
 * Honesty rules carry over unchanged: nulls stay null ("not modelled", never 0),
 * and modeled values are never labeled as measured.
 */

export interface StaticMeta {
  static_export: boolean;
  counts: {
    districts: number;
    net_thermal_footprints: number;
    buildings: number;
    properties: number;
  };
  note: string;
}

let staticMode: boolean | null = null;
let netThermalCache: GeoJSON.FeatureCollection | null = null;
let districtsCache: GeoJSON.FeatureCollection | null = null;
let buildingsCache: Record<string, unknown> | null = null;

/** Is the API actually present? A static host answers /api/* with HTML/404. */
export async function isStaticMode(): Promise<boolean> {
  if (staticMode !== null) return staticMode;
  try {
    const res = await fetch("/api/counters", { headers: { Accept: "application/json" } });
    const ct = res.headers.get("content-type") || "";
    // A static host typically returns 200 + HTML for an unknown path (SPA
    // fallback), so the content type is the reliable signal, not the status.
    staticMode = !res.ok || !ct.includes("json");
  } catch {
    staticMode = true;
  }
  return staticMode;
}

/** True when the app is running from the baked static export. */
export function staticModeSync(): boolean {
  return staticMode === true;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return (await res.json()) as T;
}

// Vite serves the export at the Pages subpath, so data URLs must be relative to
// the bundle rather than absolute — an absolute /net_thermal.json 404s under
// /nyc-district-decarbonization-explorer/.
const url = (name: string) =>
  new URL(name, import.meta.url).toString();

export async function loadNetThermal(): Promise<GeoJSON.FeatureCollection> {
  if (!netThermalCache) {
    netThermalCache = await getJson<GeoJSON.FeatureCollection>(url("net_thermal.json"));
  }
  return netThermalCache;
}

export async function loadDistricts(): Promise<GeoJSON.FeatureCollection> {
  if (!districtsCache) {
    districtsCache = await getJson<GeoJSON.FeatureCollection>(url("districts.json"));
  }
  return districtsCache;
}

export async function loadBuildings(): Promise<Record<string, unknown>> {
  if (!buildingsCache) {
    buildingsCache = await getJson<Record<string, unknown>>(url("buildings.json"));
  }
  return buildingsCache;
}

/** One building by BBL, mirroring GET /api/building/{bbl} (404 -> null). */
export async function buildingByBbl(bbl: string): Promise<unknown | null> {
  const all = await loadBuildings();
  return all[bbl] ?? null;
}

/** The LL84 property table, mirroring GET /api/properties. */
export async function loadTable(): Promise<{ total: number; properties: unknown[] }> {
  return getJson<{ total: number; properties: unknown[] }>(url("ll84_table.json"));
}

/** Baked export metadata (counts + scope note). */
export async function loadMeta(): Promise<StaticMeta> {
  return getJson<StaticMeta>(url("meta.json"));
}

/** Bounding-box filter over the baked net-thermal layer. */
export async function netThermalInBbox(
  minX: number, minY: number, maxX: number, maxY: number, limit: number,
): Promise<{ features: GeoJSON.Feature[]; matched: number }> {
  const fc = await loadNetThermal();
  const hits: GeoJSON.Feature[] = [];
  let matched = 0;
  for (const f of fc.features) {
    const b = featureBbox(f);
    if (!b) continue;
    if (b[0] > maxX || b[2] < minX || b[1] > maxY || b[3] < minY) continue;
    matched += 1;
    if (hits.length < limit) hits.push(f);
  }
  return { features: hits, matched };
}

function featureBbox(f: GeoJSON.Feature): [number, number, number, number] | null {
  return geomBbox(f.geometry as never);
}

/** [minX, minY, maxX, maxY] for a Polygon/MultiPolygon, else null. */
export function geomBbox(g: GeoJSON.Geometry | null): [number, number, number, number] | null {
  if (!g) return null;
  const rings: number[][][] =
    g.type === "Polygon"
      ? (g.coordinates as number[][][])
      : g.type === "MultiPolygon"
        ? (g.coordinates as number[][][][]).flat()
        : [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of rings) {
    for (const pt of ring) {
      if (pt[0] < x0) x0 = pt[0];
      if (pt[1] < y0) y0 = pt[1];
      if (pt[0] > x1) x1 = pt[0];
      if (pt[1] > y1) y1 = pt[1];
    }
  }
  return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
}
