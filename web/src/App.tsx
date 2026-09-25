import { useEffect, useMemo, useState } from "react";
import {
  api,
  fmtInt,
  MISSING_FIELD_OPTIONS,
  type Counters,
  type PropertyDetail,
  type PropertySummary,
  type SnapshotInfo,
} from "./api";
import MapPanel, { DEFAULT_THEME_ID } from "./MapPanel";
import Charts, { type FootFeature } from "./charts";
import { getTheme, OBSERVED_THEMES } from "./symbology";
import "./app.css";

type SortKey =
  | "address_1"
  | "site_eui_kbtu_ft"
  | "total_ghg_tco2e"
  | "gfa_sqft"
  | "electricity_kbtu"
  | "natural_gas_kbtu";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "address_1", label: "Address" },
  { key: "site_eui_kbtu_ft", label: "Site EUI (kBtu/ft²·yr)" },
  { key: "total_ghg_tco2e", label: "Total GHG (tCO2e)" },
  { key: "gfa_sqft", label: "GFA (ft²)" },
  { key: "electricity_kbtu", label: "Electricity (kBtu)" },
  { key: "natural_gas_kbtu", label: "Natural gas (kBtu)" },
];

function fmtCell(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", {
    maximumFractionDigits: v < 100 ? 1 : 0,
  });
}

export default function App() {
  const [snapshot, setSnapshot] = useState<SnapshotInfo | null>(null);
  const [counters, setCounters] = useState<Counters | null>(null);
  const [query, setQuery] = useState("");
  const [missingField, setMissingField] = useState("");
  const [rows, setRows] = useState<PropertySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("address_1");
  const [sortAsc, setSortAsc] = useState(true);
  const [selected, setSelected] = useState<PropertyDetail | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Theme lives here (not in MapPanel) so the map and the analysis rail
  // describe the same layer at all times.
  const [themeId, setThemeId] = useState<string>(DEFAULT_THEME_ID);
  const theme = getTheme(themeId) ?? OBSERVED_THEMES[0];
  const [features, setFeatures] = useState<FootFeature[]>([]);

  useEffect(() => {
    api.snapshot().then(setSnapshot).catch(() => setSnapshot(null));
    api.counters().then(setCounters).catch(() => setCounters(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .search({ q: query, missing_field: missingField, limit: 400 })
      .then((res) => {
        if (cancelled) return;
        setRows(res.properties);
        setTotal(res.total);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [query, missingField]);

  async function selectRow(id: string) {
    setSelectedId(id);
    try {
      setSelected(await api.detail(id));
    } catch (e) {
      setSelected(null);
      setError((e as Error).message);
    }
  }

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      let cmp: number;
      if (va === null && vb === null) cmp = 0;
      else if (va === null) cmp = 1; // missing always last
      else if (vb === null) cmp = -1;
      else if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
      else cmp = String(va).localeCompare(String(vb));
      return sortAsc ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortAsc]);

  function clickSort(key: SortKey) {
    if (key === sortKey) setSortAsc((v) => !v);
    else {
      setSortKey(key);
      setSortAsc(key === "address_1");
    }
  }

  const truncated = total > rows.length;

  return (
    <div className="app">
      <header className="header">
        <div className="header-top">
          <h1>Signal<span className="accent">NYC</span></h1>
          <span className="subtitle">LL84 Midtown Core · CY2024 pilot explorer</span>
        </div>
        <p className="disclaimer">
          Preliminary explorer — annual observed data. One point = one reporting property
          (may be a campus). Property-level reporting; not a building-level, compliance,
          savings, or LL97 assessment.
        </p>
        {snapshot && counters && (
          <div className="chips">
            <span className="chip accent"><b>{fmtInt(snapshot.rows)}</b> properties</span>
            <span className="chip"><b>{fmtInt(counters.with_coordinates)}</b> geocoded</span>
            <span className="chip"><b>{counters.multi_bin_properties}</b> campuses</span>
            <span className="chip">EUI <b>{fmtInt(counters.has_eui)}</b> / GHG <b>{fmtInt(counters.has_ghg)}</b> / gas <b>{fmtInt(counters.has_gas)}</b></span>
            <span className="chip">snapshot {snapshot.snapshot_utc?.slice(0, 10)}</span>
            <span className="chip" title={snapshot.sha256 ?? ""}>sha {snapshot.sha256?.slice(0, 8) ?? "—"}</span>
          </div>
        )}
      </header>

      <section className="controls">
        <input
          type="search"
          placeholder="Search address, property ID, raw BBL, or BIN…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search"
        />
        <select
          value={missingField}
          onChange={(e) => setMissingField(e.target.value)}
          aria-label="Missing-value filter"
        >
          {MISSING_FIELD_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="result-count" aria-live="polite">
          {loading ? "Loading…" : `${fmtInt(total)} matching`}
          {truncated && ` (showing first ${rows.length})`}
        </span>
        {error && <span className="error">Error: {error}</span>}
      </section>

      {/*
        Layout: map across the top, table along the bottom, analysis rail
        pinned on the right spanning both. The rail is what makes this
        readable at a glance instead of table-first.
      */}
      <main className="main">
        <div className="work-area">
          <div className="map-pane">
            <MapPanel
              properties={sorted}
              selectedId={selectedId}
              onSelect={selectRow}
              themeId={themeId}
              onThemeChange={setThemeId}
              onFeaturesChange={setFeatures}
            />
          </div>

          <div className="table-pane">
            <div className="pane-head">
              <h2>Properties</h2>
              <span className="pane-sub">
                one row = one reporting property (may be a campus)
              </span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {COLUMNS.map((c) => (
                      <th
                        key={c.key}
                        onClick={() => clickSort(c.key)}
                        className={c.key === sortKey ? (sortAsc ? "sorted-asc" : "sorted-desc") : ""}
                      >
                        {c.label}
                        {c.key === sortKey && (sortAsc ? " ▲" : " ▼")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((p) => (
                    <tr
                      key={p.property_id}
                      onClick={() => selectRow(p.property_id)}
                      className={p.property_id === selectedId ? "selected" : ""}
                    >
                      <td>
                        {p.address_1 ?? "—"}
                        {p.bin_count > 1 && (
                          <span className="tag campus" title={`${p.bin_count} buildings under this campus property`}>
                            {" "}· {p.bin_count} BINs
                          </span>
                        )}
                        {p.missing_fields.length > 0 && (
                          <span className="tag miss" title={`Missing in source: ${p.missing_fields.join(", ")}`}>
                            {" "}· partial
                          </span>
                        )}
                      </td>
                      <td className="num">{fmtCell(p.site_eui_kbtu_ft)}</td>
                      <td className="num">{fmtCell(p.total_ghg_tco2e)}</td>
                      <td className="num">{fmtCell(p.gfa_sqft)}</td>
                      <td className="num">{fmtCell(p.electricity_kbtu)}</td>
                      <td className="num">{fmtCell(p.natural_gas_kbtu)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sorted.length === 0 && !loading && <p className="empty">No matching properties.</p>}
            </div>
          </div>
        </div>

        <div className="viz-pane">
          <Charts
            features={features}
            theme={theme}
            selectedId={selectedId}
            onSelect={selectRow}
          />
          {selected && (
            <aside className="dossier" aria-label="Property dossier">
              <h2>{selected.address_1 ?? "(address not reported)"}</h2>
              <dl>
                <dt>Property ID</dt><dd>{selected.property_id}</dd>
                <dt>Parent property</dt><dd>{selected.parent_property_id ?? "—"}</dd>
                <dt>Raw BBL field</dt><dd>{selected.borough_block_lot_raw ?? "—"}</dd>
                <dt>Canonical BBL</dt><dd>{selected.bbl ?? "—"}</dd>
                <dt>First BIN (of {selected.bin_count})</dt><dd>{selected.bin ?? "—"}</dd>
                <dt>Postal code</dt><dd>{selected.postal_code ?? "—"}</dd>
                <dt>Report year</dt><dd>{selected.report_year ?? "—"}</dd>
                <dt>GFA (self-reported)</dt><dd>{fmtCell(selected.gfa_sqft)} ft²</dd>
                <dt>Site EUI</dt><dd>{fmtCell(selected.site_eui_kbtu_ft)} kBtu/ft²·yr</dd>
                <dt>WN EUI</dt><dd>{fmtCell(selected.weather_normalized_site_eui)} kBtu/ft²·yr</dd>
                <dt>GHG (location-based)</dt><dd>{fmtCell(selected.total_ghg_tco2e)} tCO2e</dd>
                <dt>Direct GHG</dt><dd>{fmtCell(selected.direct_ghg_tco2e)} tCO2e</dd>
                <dt>Grid electricity</dt><dd>{fmtCell(selected.electricity_kbtu)} kBtu</dd>
                <dt>Natural gas</dt><dd>{fmtCell(selected.natural_gas_kbtu)} kBtu</dd>
              </dl>
              {selected.bin_count > 1 && (
                <p className="note">
                  Campus property: {selected.bin_count} building Identification Numbers
                  reported under one property. Metrics are property-level and not
                  disaggregated per building.
                </p>
              )}
              {selected.missing_fields.length > 0 && (
                <p className="note">Missing in source: {selected.missing_fields.join(", ")}.</p>
              )}
              <button className="close" onClick={() => { setSelected(null); setSelectedId(null); }}>
                Close
              </button>
            </aside>
          )}
        </div>
      </main>

      <footer className="footer">
        <p>
          Source: NYC Local Law 84 energy benchmarking (Socrata {snapshot?.socrata_dataset ?? "n/a"}),
          snapshot captured {snapshot?.snapshot_utc ?? "unknown"}, sha256 {snapshot?.sha256 ?? "unknown"}.
          Read-only observed annual data · Preliminary explorer.
        </p>
      </footer>
    </div>
  );
}
