import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import type { PropertySummary } from "./api";
import {
  OBSERVED_THEMES,
  MODELED_THEMES,
  UNAVAILABLE_THEMES,
  colorExpr,
  getTheme,
  legendSwatches,
  opacityExpr,
  type ThemeDef,
} from "./symbology";
import "maplibre-gl/dist/maplibre-gl.css";

const DEFAULT_THEME_ID = "site_eui_kbtu_ft";

// Minimal grey canvas basemap: geography only, no labels and no street-level
// detail, so the energy fills are the only thing competing for attention.
//
// Why a DARK grey canvas rather than a light one: the diverging net-thermal
// ramp puts WHITE at zero (heating/cooling in balance), so light-grey
// buildings would vanish into a light basemap and the neutral band would be
// unreadable. A neutral dark grey keeps white, blue and red all legible.
//
// Provider: Esri "World Dark Gray Canvas" — a genuine grey canvas with no
// labels, and key-free (no signup, no token). Carto's `_nolabels` endpoint
// was tried first but now serves a byte-identical placeholder tile at every
// coordinate (an API-key notice, not map data) — verified by md5-comparing
// tiles from five different z/x/y positions. Esri returns distinct real tiles
// per coordinate, so prefer it here.
//
// GOTCHA: Esri's tile template is {z}/{y}/{x} — y BEFORE x, the reverse of
// MapLibre's usual {z}/{x}/{y}. Writing {z}/{x}/{y} here silently requests
// the wrong tiles.
//
// Remote dependency (documented in README). If tiles fail the background
// grey still paints and every data layer renders on it, so failure degrades
// gracefully instead of blanking the map.
const PUBLIC_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    canvas: {
      type: "raster",
      tiles: [
        "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      maxzoom: 16,
      attribution: "Esri, HERE, Garmin, © OpenStreetMap contributors",
    },
  },
  layers: [
    // Fallback canvas colour, also tints slightly through the raster.
    { id: "bg", type: "background", paint: { "background-color": "#242830" } },
    {
      id: "canvas",
      type: "raster",
      source: "canvas",
      paint: {
        // Light muting only: this tile set is already near-monochrome, so
        // keep it legible and just settle it toward flat grey.
        "raster-opacity": 1.0,
        "raster-saturation": -0.4,
        "raster-contrast": -0.05,
      },
    },
  ],
};

// Footprint fill color comes from the switcher's active theme (see symbology.ts).
// Paint expressions are re-applied on theme change in a separate effect below.
// Modeled themes carry the real property in decidingField (field is a sentinel);
// route paint at the deciding property so the map reads actual feature data.
function themeFillColor(theme: ThemeDef): unknown {
  const deciding = (theme as { decidingField?: string }).decidingField;
  return colorExpr(deciding ? { ...theme, field: deciding } : theme);
}

const FP_HIGHLIGHT = (selectedId: string | null): unknown => [
  "case",
  ["==", ["get", "pid"], selectedId ?? "__none__"],
  0.92,
  0.55,
];
void FP_HIGHLIGHT;

interface Props {
  properties: PropertySummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function MapPanel({ properties, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  // Latest props, so async layer-attachment uses current data (stale-closure fix)
  const propsRef = useRef<{
    properties: PropertySummary[];
    selectedId: string | null;
    theme: ThemeDef;
  }>({ properties, selectedId, theme: OBSERVED_THEMES[0] });
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"points" | "footprints">("points");
  const [parcelState, setParcelState] = useState<
    { loaded: number; truncated: boolean } | null
  >(null);
  const [themeId, setThemeId] = useState<string>(DEFAULT_THEME_ID);
  const theme = getTheme(themeId) ?? OBSERVED_THEMES[0];
  propsRef.current = { properties, selectedId, theme };

  // Reapply the active theme's paint expressions when the user switches layers.
  // Guarded on layer existence so it's a no-op until footprints have attached.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("fp-fill")) return;
    map.setPaintProperty("fp-fill", "fill-color", colorExpr(theme) as never);
    map.setPaintProperty(
      "fp-fill",
      "fill-opacity",
      opacityExpr(theme, propsRef.current.selectedId) as never
    );
  }, [themeId, theme]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let tileErrorCount = 0;
    let dataLayersAdded = false;
    try {
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: PUBLIC_STYLE,
        center: [-73.979, 40.7565],
        zoom: 13.2,
        attributionControl: {},
      });
      mapRef.current = map;
      map.on("error", (e) => {
        // Tile fetch failures arrive here; on mobile networks they can be slow
        // or blocked entirely. The style still activates (background layer),
        // so degrade gracefully: banner after repeated failures, but data
        // layers still load — we no longer gate everything on pristine tiles.
        if (!cancelled && e?.error) {
          tileErrorCount += 1;
          if (tileErrorCount >= 4) {
            setError("Map tiles are slow or blocked — data layers may still render.");
          }
        }
      });
      // Style 'load' can be delayed by slow raster tiles on mobile networks.
      // Add data sources as soon as the style is USABLE, not fully loaded.
      const addDataLayers = () => {
        if (cancelled) return;
        // Idempotence: both the 'load' handler and the 2.5s fallback call this;
        // a second run would throw "source already exists" and break rendering.
        if (dataLayersAdded) return;
        if (!map.isStyleLoaded()) return; // retry via the pending timer/load
        dataLayersAdded = true;
        // --- footprint polygons (fetched from /api/footprints, joined w/ energy) ---
        fetch("/api/footprints")
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
          .then((fc: { features?: Array<{ geometry?: { type?: string } }> }) => {
            if (cancelled) return;
            const feats = (fc?.features ?? []).filter(
              (f) => f?.geometry?.type === "Polygon" || f?.geometry?.type === "MultiPolygon"
            );
            if (!feats.length) return;
            setMode("footprints");
            map.addSource("footprints", {
              type: "geojson",
              data: {
                type: "FeatureCollection",
                features: feats as unknown as Array<import("geojson").Feature>,
              },
            });
            map.addLayer({
              id: "fp-fill",
              type: "fill",
              source: "footprints",
              paint: {
                "fill-color": themeFillColor(propsRef.current.theme) as never,
                "fill-opacity": opacityExpr(
                  propsRef.current.theme,
                  propsRef.current.selectedId
                ) as never,
              },
            });
            map.addLayer({
              id: "fp-line",
              type: "line",
              source: "footprints",
              paint: {
                // Slightly soft outline: enough to separate adjoining
                // footprints, not a hard black grid over the data.
                "line-color": "#3a4048",
                "line-width": 0.7,
                "line-opacity": 0.6,
              },
            });
            map.on("click", "fp-fill", (e) => {
              const f = e.features?.[0];
              if (f && typeof f.properties?.pid === "string") onSelect(f.properties.pid);
            });
            map.on("mouseenter", "fp-fill", () => {
              map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", "fp-fill", () => {
              map.getCanvas().style.cursor = "";
            });
          })
          .catch((err: unknown) => {
            if (cancelled) return;
            console.error("[map] footprints layer failed:", err);
            setError("Building footprints could not load — table remains available.");
          });
        // --- property points (marker for properties w/o a footprint match) ---
        map.addSource("props", {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });
        map.addLayer({
          id: "prop-points",
          type: "circle",
          source: "props",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 2.5, 15, 5],
            "circle-color": "#f1f5f9",
            "circle-opacity": 0.85,
            "circle-stroke-width": 0.8,
            "circle-stroke-color": "#334155",
          },
        });
        map.on("click", "prop-points", (e) => {
          const f = e.features?.[0];
          if (f && typeof f.properties?.id === "string") {
            onSelect(f.properties.id);
          }
        });
        // --- citywide MapPLUTO parcels (DEV-158): refetch over visible bbox ---
        map.addSource("parcels", {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });
        map.addLayer({
          id: "parcel-fill",
          type: "fill",
          source: "parcels",
          paint: {
            "fill-color": "#6b7280",
            "fill-opacity": [
              "case",
              ["get", "has_ll84"],
              0.45,
              0.18,
            ],
          },
        });
        map.addLayer({
          id: "parcel-line",
          type: "line",
          source: "parcels",
          paint: {
            "line-color": "#9fb8c8",
            "line-width": 0.5,
            "line-opacity": 0.7,
          },
        });
        map.on("click", "parcel-fill", (e) => {
          const f = e.features?.[0];
          const p = f?.properties as Record<string, unknown> | undefined;
          if (typeof p?.bbl === "string") {
            const match = propsRef.current.properties.find((q) => q.bbl === p.bbl);
            if (match) onSelect(match.property_id);
          }
        });
        setFeatures(propsRef.current.properties);
      };

      // Prefetch-on-move with response stamping so a slow older response can
      // never overwrite a newer one (async stale-closure fix).
      let parcelReqSeq = 0;
      const loadParcels = () => {
        const map = mapRef.current;
        if (!map || !map.getLayer("parcel-fill")) return;
        const b = map.getBounds().toArray() as number[][]; // [[sw],[ne]]
        const seq = ++parcelReqSeq;
        const qs = new URLSearchParams({
          min_x: String(b[0][0]),
          min_y: String(b[0][1]),
          max_x: String(b[1][0]),
          max_y: String(b[1][1]),
          limit: "8000",
        });
        fetch(`/api/parcels?${qs.toString()}`)
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
          .then(
            (d: {
              bbox?: number[];
              features?: Array<{
                geometry?: { type?: string };
                properties?: Record<string, unknown>;
              }>;
            }) => {
              if (cancelled) return;
              const cur = mapRef.current;
              if (!cur || !cur.getLayer("parcel-fill")) return;
              const now = cur.getBounds().toArray() as number[][];
              const curBox = [now[0][0], now[0][1], now[1][0], now[1][1]];
              const stale =
                d.bbox &&
                d.bbox.some(
                  (v, i) =>
                    Math.abs(v - curBox[i]) >
                    Math.max(Math.abs(curBox[i]) * 1e-6, 1e-9)
                );
              if (stale || seq !== parcelReqSeq) return;
              const feats = (d.features ?? []).filter(
                (f) =>
                  (f?.geometry?.type === "Polygon" ||
                    f?.geometry?.type === "MultiPolygon") &&
                  f?.properties?.bbl
              );
              const src = cur.getSource("parcels") as
                | maplibregl.GeoJSONSource
                | undefined;
              if (!src) return;
              src.setData({
                type: "FeatureCollection",
                features: feats.map((f) => ({
                  type: "Feature" as const,
                  geometry: f.geometry as NonNullable<
                    import("geojson").Feature["geometry"]
                  >,
                  properties: {
                    bbl: f.properties?.bbl,
                    has_ll84: !!f.properties?.ll84,
                    year_built: f.properties?.year_built ?? null,
                    bldg_area_sqft: f.properties?.bldg_area_sqft ?? null,
                  },
                })),
              });
              setParcelState({
                loaded: feats.length,
                truncated: Boolean((d as { truncated?: boolean }).truncated),
              });
            }
          )
          .catch((err: unknown) => {
            if (!cancelled)
              console.error("[map] citywide parcels fetch failed:", err);
          });
      };
      let parcelTimer: ReturnType<typeof setTimeout> | null = null;
      const scheduleParcels = () => {
        if (cancelled) return;
        if (parcelTimer) clearTimeout(parcelTimer);
        parcelTimer = setTimeout(loadParcels, 350);
      };
      // First load once the parcels source exists, then on map move end.
      const parcelsReady = () =>
        !!mapRef.current && !!mapRef.current.getLayer("parcel-fill");
      const startParcelWiring = () => {
        if (cancelled) return;
        if (!parcelsReady()) {
          setTimeout(startParcelWiring, 500);
          return;
        }
        loadParcels();
        map.on("moveend", scheduleParcels);
      };
      startParcelWiring();
      // Prefer the standard load event, but don't depend on it: if tiles hang,
      // the style is still structurally ready and data layers can attach.
      if (map.isStyleLoaded()) {
        addDataLayers();
      } else {
        map.once("load", addDataLayers);
        // Safety net: attach after 2.5s even if 'load' hasn't fired (slow tiles).
        const t = setTimeout(() => {
          if (!cancelled && !map.isStyleLoaded()) return; // style still building
          addDataLayers();
        }, 2500);
        map.on("error", (e) => {
          // a style-level error also unblocks us; tile errors don't invalidate the style
          if (e?.error && !cancelled) {
            // no-op: handled by the general error handler above
          }
        });
        void t;
      }
    } catch (err) {
      console.error("[map] init failed:", err);
      setError("Map could not initialize — table remains available.");
    }
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
    // mount once; props update via second effect below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setFeatures(props = propsRef.current.properties) {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("props") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: props
        .filter((p) => p.latitude !== null && p.longitude !== null)
        .map((p) => ({
          type: "Feature" as const,
          geometry: {
            type: "Point" as const,
            coordinates: [p.longitude as number, p.latitude as number],
          },
          properties: { id: p.property_id },
        })),
    });
  }

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => setFeatures(properties);
    if (map.isStyleLoaded()) apply();
    else map.once("load", apply);
  }, [properties]);

  // highlight selection (polygons + points), fly to selection
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !selectedId) return;
    if (map.getLayer("fp-fill")) {
      map.setPaintProperty("fp-fill", "fill-opacity", opacityExpr(theme, selectedId) as never);
    }
    if (map.getLayer("prop-points")) {
      map.setPaintProperty("prop-points", "circle-stroke-color", [
        "case",
        ["==", ["get", "id"], selectedId],
        "#e01e5a",
        "#334155",
      ]);
      map.setPaintProperty("prop-points", "circle-stroke-width", [
        "case",
        ["==", ["get", "id"], selectedId],
        2.5,
        0.8,
      ]);
    }
    const sel = properties.find((p) => p.property_id === selectedId);
    if (sel?.longitude != null && sel?.latitude != null) {
      map.flyTo({
        center: [sel.longitude, sel.latitude],
        zoom: Math.max(map.getZoom(), 15),
        duration: 400,
      });
    }
  }, [selectedId, properties]);

  return (
    <div className="map-wrap">
      {error && <div className="map-fallback">{error}</div>}
      {parcelState && !error && (
        <div className="map-parcel-status" data-testid="parcel-status">
          Citywide parcels: {parcelState.loaded.toLocaleString("en-US")}
          {parcelState.truncated ? " (view truncated — zoom in)" : ""}
        </div>
      )}
      {mode === "footprints" && !error && (
        <div className="map-symbology-panel" data-testid="map-symbology-panel">
          <label className="symbology-select-label" htmlFor="symbology-select">
            Color footprints by
          </label>
          <select
            id="symbology-select"
            data-testid="symbology-select"
            value={themeId}
            onChange={(e) => setThemeId(e.target.value)}
          >
            <optgroup label="Observed (LL84 reported)">
              {OBSERVED_THEMES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label} — {t.units}
                </option>
              ))}
            </optgroup>
            <optgroup label="Modeled (estimated, not measured)">
              {MODELED_THEMES.map((t) => (
                <option key={t.id} value={t.id} title={t.modelNote}>
                  {t.label} — {t.units}
                </option>
              ))}
            </optgroup>
            {UNAVAILABLE_THEMES.length > 0 && (
              <optgroup label="Not yet available">
                {UNAVAILABLE_THEMES.map((u) => (
                  <option key={u.id} value={u.id} disabled title={u.reason}>
                    {u.label} — requires modeled end-use data
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          <div className="symbology-legend" data-testid="symbology-legend">
            <span className="legend-title">
              {theme.label} <span className="legend-units">({theme.units})</span>
            </span>
            <div className="legend-row">
              {legendSwatches(theme).map((s) => (
                <span key={s.text} className="legend-stop">
                  <span className="swatch" style={{ background: s.color }} />
                  {s.text}
                </span>
              ))}
              <span className="legend-stop">
                <span className="swatch" style={{ background: theme.nullGray }} />
                no data
              </span>
            </div>
            {"modelNote" in theme && (theme as { modelNote?: string }).modelNote && (
              <span className="legend-modelnote">
                {(theme as { modelNote: string }).modelNote}
              </span>
            )}
          </div>
          <div className="symbology-grain">
            {theme.grain}
            {theme.disaggregated
              ? " · multi-building property totals already area-weighted to each footprint"
              : ""}
            {theme.weatherNormalized ? " · weather-normalized" : ""}
          </div>
        </div>
      )}
      <div ref={containerRef} className="map-canvas" data-testid="map" />
    </div>
  );
}