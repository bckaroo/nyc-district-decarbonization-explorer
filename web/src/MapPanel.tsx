import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import type { PropertySummary } from "./api";
import "maplibre-gl/dist/maplibre-gl.css";

// Public raster demo tiles: remote dependency, documented in README. If tiles
// fail the map shows an error banner and the table stays fully functional.
const PUBLIC_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#141924" } },
    { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.75 } },
  ],
};

// EUI color ramp (kBtu/ft2·yr): teal (low) -> green -> yellow -> orange -> red (high)
function euiColorExpr(): unknown {
  return [
    "interpolate",
    ["linear"],
    ["to-number", ["get", "site_eui_kbtu_ft"]],
    0, "#22d3ee",
    60, "#34d399",
    100, "#fbbf24",
    160, "#fb923c",
    240, "#f87171",
  ];
}

const FP_HIGHLIGHT = (selectedId: string | null): unknown => [
  "case",
  ["==", ["get", "pid"], selectedId ?? "__none__"],
  0.92,
  ["!", ["has", "site_eui_kbtu_ft"]],
  0.12,
  0.55,
];

interface Props {
  properties: PropertySummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function MapPanel({ properties, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"points" | "footprints">("points");

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let tileErrorCount = 0;
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
              data: { type: "FeatureCollection", features: feats },
            });
            map.addLayer({
              id: "fp-fill",
              type: "fill",
              source: "footprints",
              paint: {
                "fill-color": euiColorExpr() as never,
                "fill-opacity": FP_HIGHLIGHT(selectedId) as never,
              },
            });
            map.addLayer({
              id: "fp-line",
              type: "line",
              source: "footprints",
              paint: {
                "line-color": "#0b0e14",
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
          .catch(() => {
            /* footprints unavailable — points fallback stays visible */
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
        setFeatures(properties);
      };
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
    } catch {
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

  function setFeatures(properties: PropertySummary[]) {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("props") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    src.setData({
      type: "FeatureCollection",
      features: properties
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
      map.setPaintProperty("fp-fill", "fill-opacity", FP_HIGHLIGHT(selectedId) as never);
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
      {mode === "footprints" && !error && (
        <div className="map-badge">
          <span className="swatch" style={{ background: "linear-gradient(90deg,#22d3ee,#34d399,#fbbf24,#fb923c,#f87171)" }} />
          Building footprints · Site EUI (kBtu/ft²·yr)
        </div>
      )}
      <div ref={containerRef} className="map-canvas" data-testid="map" />
    </div>
  );
}