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
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

interface Props {
  properties: PropertySummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function MapPanel({ properties, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    try {
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: PUBLIC_STYLE,
        center: [-73.979, 40.7565],
        zoom: 12.6,
        attributionControl: {},
      });
      mapRef.current = map;
      map.on("error", (e) => {
        // tile/status errors: degrade, don't crash
        if (!cancelled && e?.error) setError("Map tiles failed to load — table remains available.");
      });
      map.on("load", () => {
        if (cancelled) return;
        map.addSource("props", {
          type: "geojson",
          data: { type: "FeatureCollection", features: [] },
        });
        map.addLayer({
          id: "prop-points",
          type: "circle",
          source: "props",
          paint: {
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              12,
              4,
              15,
              8,
            ],
            "circle-color": "#1a5fb4",
            "circle-opacity": 0.75,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#ffffff",
          },
        });
        map.on("click", "prop-points", (e) => {
          const f = e.features?.[0];
          if (f && typeof f.properties?.id === "string") {
            onSelect(f.properties.id);
          }
        });
        setFeatures(properties);
      });
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

  // highlight selection
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded() || !selectedId) return;
    map.setPaintProperty("prop-points", "circle-color", [
      "case",
      ["==", ["get", "id"], selectedId],
      "#e01e5a",
      "#1a5fb4",
    ]);
    map.flyTo({
      center: [
        properties.find((p) => p.property_id === selectedId)?.longitude ?? -73.979,
        properties.find((p) => p.property_id === selectedId)?.latitude ?? 40.7565,
      ],
      zoom: Math.max(map.getZoom(), 14),
      duration: 400,
    });
  }, [selectedId, properties]);

  return (
    <div className="map-wrap">
      {error && <div className="map-fallback">{error}</div>}
      <div ref={containerRef} className="map-canvas" data-testid="map" />
    </div>
  );
}
