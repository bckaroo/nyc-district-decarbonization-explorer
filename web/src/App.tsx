import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  fmtInt,
  MISSING_FIELD_OPTIONS,
  type BuildingDetail,
  type Counters,
  type PropertyDetail,
  type PropertySummary,
  type SnapshotInfo,
} from "./api";
import MapPanel, { DEFAULT_THEME_ID } from "./MapPanel";
import Charts, { type FootFeature } from "./charts";
import { getTheme, OBSERVED_THEMES } from "./symbology";
import {
  buildingByBbl,
  geomBbox,
  isStaticMode,
  loadDistricts as loadStaticDistricts,
  loadNetThermal,
  loadTable,
  staticModeReady,
  staticModeSync,
} from "./staticData";
import "./app.css";
import {
  type DistrictPortfolioRow,
  fetchDistrictsPortfolio,
} from "./districtsTable";
import { loadDistrictsPortfolio as loadStaticDistrictsPortfolio } from "./staticData";
import { loadLl97 as loadStaticLl97 } from "./staticData";
import {
  type Ll97Row,
  fetchLl97,
} from "./ll97Table";
import { Ll97Pane } from "./Ll97Pane";
import { SortHead, useSorted, type SortState } from "./tableSort";

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



const PLUTO_LAND_USE: Record<string, string> = {
  "1": "1–2 family", "2": "multi-family walk-up", "3": "multi-family elevator",
  "4": "mixed residential/commercial", "5": "commercial/office",
  "6": "industrial/manufacturing", "7": "transportation/utility",
  "8": "public facilities/institutions", "9": "open space/recreation",
  "10": "parking", "11": "vacant",
};
const PLUTO_OWNER_TYPE: Record<string, string> = {
  P: "private", C: "city", X: "other public", O: "public authority",
  M: "mixed city/private", U: "state", F: "federal",
  R: "private (housing dev co)", N: "private (condo)", H: "private (co-op rented)",
};
function plutoLandUse(code: string | null): string {
  if (!code) return "—";
  const k = code.replace(/^0/, "") || code;
  return `${PLUTO_LAND_USE[k] ?? code} (${k})`;
}
function plutoOwnerType(code: string | null): string {
  if (!code) return "—";
  return PLUTO_OWNER_TYPE[code] ?? code;
}

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
  // Predefined study boundary clicked on the map (BID / campus). Kept at App
  // level so the rail can summarize the district alongside the property dossier.
  const [district, setDistrict] = useState<{
    properties: Record<string, unknown>;
    footprints: FootFeature[];
    footprintsInBbox: number;
    basis: string;
  } | null>(null);
  // Collapsing the table lets the map take the full work area.
  const [tableHidden, setTableHidden] = useState(false);
  // Table tabs: LL84 properties vs. study-district portfolios (BIDs/campuses).
  const [tableTab, setTableTab] = useState<"properties" | "districts" | "ll97">("properties");
  const [districtRows, setDistrictRows] = useState<DistrictPortfolioRow[] | null>(null);
  const [districtRowsErr, setDistrictRowsErr] = useState<string | null>(null);
  const [districtSort, setDistrictSort] = useState<SortState | null>(null);
  // LL97 tab state: one row per property + a selected property's pathway chart.
  const [ll97Rows, setLl97Rows] = useState<Ll97Row[] | null>(null);
  const [ll97Err, setLl97Err] = useState<string | null>(null);
  // Set when a map click resolves to a building, so the table can scroll its row
  // into view and flash it. Changing the value re-triggers the effect even when
  // the same row is clicked twice.
  const [rowFocus, setRowFocus] = useState<{ id: string; n: number } | null>(null);
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});
  // True on the GitHub Pages build (no backend). Drives the data source choice
  // and a visible notice so a visitor is never misled about which build this is.
  const [staticBuild, setStaticBuild] = useState(false);

  // Detect the static (GitHub Pages) build before any data load, so the loaders
  // know whether to hit the API or the baked JSON.
  useEffect(() => {
    isStaticMode().then((yes) => setStaticBuild(yes));
  }, []);

  useEffect(() => {
    api.snapshot().then(setSnapshot).catch(() => setSnapshot(null));
    api.counters().then(setCounters).catch(() => setCounters(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      // Static build: filter the baked table client-side with the same predicate
      // the API applies, so the table behaves identically without a backend.
      // staticModeReady, not staticModeSync: this effect runs before the async
      // mode probe resolves, and a sync check saw "API mode", requested
      // /api/properties, got 404 and showed "Error: 404 / 0 matching" — the
      // symptom visible on the published page screenshot.
      if (await staticModeReady()) {
        try {
          const t = await loadTable();
          if (cancelled) return;
          let list = (t.properties ?? []) as PropertySummary[];
          if (query.trim()) {
            const q = query.trim().toLowerCase();
            list = list.filter((p) =>
              (p.address_1 ?? "").toLowerCase().includes(q) ||
              (p.bbl ?? "").includes(q) ||
              (p.bin ?? "").includes(q),
            );
          }
          if (missingField) {
            list = list.filter((p) =>
              (p.missing_fields ?? []).includes(missingField),
            );
          }
          setRows(list);
          setTotal(list.length);
          setLoading(false);
        } catch (e) {
          if (cancelled) return;
          setError((e as Error).message);
          setLoading(false);
        }
        return;
      }
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
    })();
    return () => {
      cancelled = true;
    };
  }, [query, missingField]);

  // LL97 rows load on demand (tab switch), once.
  useEffect(() => {
    if (tableTab !== "ll97" || ll97Rows || ll97Err) return;
    let cancelled = false;
    (async () => {
      try {
        if (await staticModeReady()) {
          const d = await loadStaticLl97();
          if (cancelled) return;
          setLl97Rows(d.properties as Ll97Row[]);
        } else {
          const d = await fetchLl97();
          if (cancelled) return;
          setLl97Rows(d.properties);
        }
      } catch (e) {
        if (cancelled) return;
        setLl97Err((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tableTab, ll97Rows, ll97Err]);

  // Sorting for the districts table (clickable column headers).
  const districtRowsSorted = useSorted<DistrictPortfolioRow>(
    districtRows,
    districtSort,
    (row, key) => {
      switch (key) {
        case "name": return row.name ?? null;
        case "kind": return row.kind;
        case "borough": return row.borough ?? null;
        case "members": return row.members;
        case "ll84_props": return row.ll84_props;
        case "gfa_total": return row.gfa_total ?? null;
        case "site_eui_median": return row.site_eui_median ?? null;
        case "net_thermal_median": return row.net_thermal_median ?? null;
        case "hcd": return (row.heating_dominant ?? 0) + (row.cooling_dominant ?? 0);
        default: return null;
      }
    },
  );
  function toggleDistrictSort(key: string) {
    setDistrictSort((prev) =>
      prev && prev.key === key ? { key, asc: !prev.asc } : { key, asc: false },
    );
  }

  // Districts portfolio rows load on demand (tab switch), once.
  const districtRowsVisible = tableTab === "districts";
  useEffect(() => {
    if (!districtRowsVisible || districtRows || districtRowsErr) return;
    let cancelled = false;
    (async () => {
      try {
        if (await staticModeReady()) {
          const d = await loadStaticDistrictsPortfolio();
          if (cancelled) return;
          setDistrictRows(d.districts as DistrictPortfolioRow[]);
        } else {
          const d = await fetchDistrictsPortfolio();
          if (cancelled) return;
          setDistrictRows(d.districts);
        }
      } catch (e) {
        if (cancelled) return;
        setDistrictRowsErr((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [districtRowsVisible, districtRows, districtRowsErr]);

  async function selectRow(id: string) {
    setSelectedId(id);
    try {
      setSelected(await api.detail(id));
    } catch (e) {
      setSelected(null);
      setError((e as Error).message);
    }
  }

  // A map click on a footprint. Two outcomes:
  //  (a) the BBL is in the loaded table -> select that row, scroll it into view
  //      and flash it, which is the "click a building, see its row" behaviour;
  //  (b) it is NOT in the table (the normal case — the table holds an LL84
  //      slice, the map draws all 1,083,047 footprints) -> fetch the building
  //      from /api/building/{bbl} so its identity, observed LL84 join and
  //      modelled demand still resolve.
  const [building, setBuilding] = useState<BuildingDetail | null>(null);

  function selectBuildingByBbl(bbl: string) {
    const inTable = rows.find((r) => r.bbl === bbl);
    if (inTable) {
      selectRow(inTable.property_id);
      setRowFocus({ id: inTable.property_id, n: Date.now() });
      return;
    }
    fetch(`/api/building/${encodeURIComponent(bbl)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        setBuilding(d);
        setRowFocus(null);
      })
      .catch((err: unknown) => {
        // The static Pages build has no backend. Same payload shape, so the
        // dossier renders identically; only the source differs.
        return buildingByBbl(bbl).then((d) => {
          if (d) {
            setBuilding(d as BuildingDetail);
            setRowFocus(null);
          } else {
            console.error("[app] building fetch failed:", err);
            setBuilding(null);
          }
        });
      });
  }

  // Scroll the clicked row into view and flash it. Runs on rowFocus changes so a
  // repeat click on the same building re-scrolls (the counter makes it a new
  // value, so the effect is not skipped as a no-op update).
  useEffect(() => {
    if (!rowFocus) return;
    const el = rowRefs.current[rowFocus.id];
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.classList.add("row-flash");
    const t = setTimeout(() => el.classList.remove("row-flash"), 1400);
    return () => clearTimeout(t);
  }, [rowFocus]);

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

  // Open a predefined study boundary: fetches the district's footprints so the
  // rail can summarize it. The API labels the count as bounding-box based
  // rather than true polygon containment, and that label rides through.
  function selectDistrict(districtId: string) {
    fetch(`/api/districts/${encodeURIComponent(districtId)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(
        (d: {
          district?: Record<string, unknown>;
          footprints?: FootFeature[];
          footprints_in_bbox?: number;
          basis?: string;
        }) =>
          setDistrict({
            properties: d.district ?? {},
            footprints: d.footprints ?? [],
            footprintsInBbox: d.footprints_in_bbox ?? 0,
            basis: d.basis ?? "",
          })
      )
      .catch((err: unknown) => {
        // Static build: summarize the district from the baked layers instead of
        // a per-district endpoint. Same shape, so the rail is unchanged.
        // By the time a user-triggered fetch has failed, the probe has long
        // since resolved, so the sync check is safe here.
        if (!staticModeSync()) {
          console.error("[app] district fetch failed:", err);
          setDistrict(null);
          return;
        }
        loadStaticDistricts()
          .then(async (fc) => {
            const feat = fc.features.find(
              (f) =>
                (f.properties as Record<string, unknown>)?.district_id === districtId ||
                (f.properties as Record<string, unknown>)?.id === districtId,
            );
            if (!feat) {
              setDistrict(null);
              return;
            }
            const props = (feat.properties ?? {}) as Record<string, unknown>;
            const box = geomBbox(feat.geometry as never);
            const thermal = await loadNetThermal();
            const inBox = box
              ? thermal.features.filter((f) => {
                  const b = geomBbox(f.geometry as never);
                  return (
                    b && !(b[0] > box[2] || b[2] < box[0] || b[1] > box[3] || b[3] < box[1])
                  );
                })
              : [];
            setDistrict({
              properties: props,
              footprints: inBox.slice(0, 12000) as unknown as FootFeature[],
              footprintsInBbox: inBox.length,
              // The static count is bounding-box based, exactly like the API's,
              // and says so rather than implying polygon containment.
              basis: "bounding_box",
            });
          })
          .catch(() => setDistrict(null));
      });
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-top">
          <h1>
            NYC <span className="accent">District Decarbonization</span> Explorer
          </h1>
          <span className="subtitle">
            Citywide · LL84 CY2024 observed + modeled annual demand
          </span>
        </div>
        <p className="disclaimer">
          Preliminary explorer — annual observed data. One point = one reporting property
          (may be a campus). Property-level reporting; not a building-level, compliance,
          savings, or LL97 assessment.
        </p>
        {staticBuild && (
          <p
            className="disclaimer static-notice"
            data-testid="static-notice"
          >
            <strong>Static demo build.</strong> This GitHub Pages copy has no
            server, so it shows every footprint with a modeled net-thermal value
            (41,161) plus all 273 study boundaries, rather than the full
            1,083,047-footprint citywide layer. Values, caveats, and the
            modeled-vs-measured distinction are identical to the live app.
          </p>
        )}
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
        <div className={`work-area${tableHidden ? " table-hidden" : ""}`}>
          <div className="map-pane">
            <MapPanel
              properties={sorted}
              selectedId={selectedId}
              onSelect={selectRow}
              themeId={themeId}
              onThemeChange={setThemeId}
              onFeaturesChange={setFeatures}
              onDistrictSelect={selectDistrict}
              onBuildingSelect={selectBuildingByBbl}
            />
          </div>

          {/* The collapse bar is its own grid child, NOT a child of
              .table-pane: when the pane collapses to 0 height it would clip the
              control needed to bring the table back, making hide a one-way trip. */}
          <div
            className="table-collapse-bar"
            data-testid="table-collapse-toggle"
            role="button"
            tabIndex={0}
            aria-expanded={!tableHidden}
            aria-controls="properties-table-pane"
            title={tableHidden ? "Show the properties table" : "Hide the table to expand the map"}
            onClick={() => setTableHidden((v) => !v)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setTableHidden((v) => !v);
              }
            }}
          >
            <span className="chev" aria-hidden="true">
              {tableHidden ? "▲" : "▼"}
            </span>
            <span>
              {tableHidden
                ? "Properties table hidden — click to show"
                : "Hide table (expand map)"}
            </span>
            <span className="chev" aria-hidden="true">
              {tableHidden ? "▲" : "▼"}
            </span>
          </div>

          <div className="table-pane" id="properties-table-pane">
            <div className="pane-head">
              <div className="table-tabs" role="tablist" aria-label="Table datasets">
                <button
                  role="tab"
                  aria-selected={tableTab === "properties"}
                  className={tableTab === "properties" ? "tab active" : "tab"}
                  onClick={() => setTableTab("properties")}
                >
                  Properties
                </button>
                <button
                  role="tab"
                  aria-selected={tableTab === "districts"}
                  className={tableTab === "districts" ? "tab active" : "tab"}
                  onClick={() => setTableTab("districts")}
                >
                  Districts &amp; Campuses
                </button>
                <button
                  role="tab"
                  aria-selected={tableTab === "ll97"}
                  className={tableTab === "ll97" ? "tab active" : "tab"}
                  onClick={() => setTableTab("ll97")}
                >
                  LL97 Compliance
                </button>
              </div>
              <span className="pane-sub">
                {tableTab === "properties"
                  ? "one row = one reporting property (may be a campus)"
                  : tableTab === "districts"
                    ? "one row = one study district (BID or campus)"
                    : "one row = one LL84 property; Article 320 screening"}
              </span>
            </div>
            {tableTab === "properties" ? (
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
                      ref={(el) => {
                        rowRefs.current[p.property_id] = el;
                      }}
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
            ) : tableTab === "districts" ? (
            <div className="table-scroll">
              {districtRowsErr && <p className="empty">Districts load failed: {districtRowsErr}</p>}
              {!districtRows && !districtRowsErr && <p className="empty">Loading districts…</p>}
              {districtRows && (
                <table>
                  <thead>
                    <tr>
                      <SortHead label="District" sortKey="name" state={districtSort} onToggle={toggleDistrictSort} />
                      <SortHead label="Type" sortKey="kind" state={districtSort} onToggle={toggleDistrictSort} />
                      <SortHead label="Borough" sortKey="borough" state={districtSort} onToggle={toggleDistrictSort} />
                      <SortHead label="Buildings" sortKey="members" state={districtSort} onToggle={toggleDistrictSort} num />
                      <SortHead label="LL84 props" sortKey="ll84_props" state={districtSort} onToggle={toggleDistrictSort} num />
                      <SortHead label="GFA ft²" sortKey="gfa_total" state={districtSort} onToggle={toggleDistrictSort} num />
                      <SortHead label="Median EUI" sortKey="site_eui_median" state={districtSort} onToggle={toggleDistrictSort} num />
                      <SortHead label="Median net thermal" sortKey="net_thermal_median" state={districtSort} onToggle={toggleDistrictSort} num />
                      <SortHead label="Heat / Cool" sortKey="hcd" state={districtSort} onToggle={toggleDistrictSort} num />
                    </tr>
                  </thead>
                  <tbody>
                    {districtRowsSorted.map((d) => (
                      <tr key={d.district_id}>
                        <td>{d.name}</td>
                        <td>
                          <span className={`tag ${d.kind === "bid" ? "bid" : "campus"}`}>
                            {d.kind === "bid" ? "BID" : "Campus"}
                          </span>
                        </td>
                        <td>{d.borough ?? "—"}</td>
                        <td className="num">{d.members.toLocaleString()}</td>
                        <td className="num">{d.ll84_props.toLocaleString()}</td>
                        <td className="num">{d.gfa_total == null ? "—" : d.gfa_total.toLocaleString()}</td>
                        <td className="num">{d.site_eui_median == null ? "—" : d.site_eui_median.toFixed(1)}</td>
                        <td className="num">{d.net_thermal_median == null ? "—" : d.net_thermal_median.toFixed(1)}</td>
                        <td className="num">{d.heating_dominant} / {d.cooling_dominant}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {districtRows && districtRows.length === 0 && (
                <p className="empty">No study districts.</p>
              )}
            </div>
            ) : (
            <Ll97Pane
              rows={ll97Rows}
              err={ll97Err}
            />
            )}
          </div>
        </div>

        <div className="viz-pane">
          <Charts
            features={features}
            theme={theme}
            selectedId={selectedId}
            onSelect={selectRow}
          />
          {district && (
            <aside className="dossier" aria-label="District study summary" data-testid="district-dossier">
              <h2>{String(district.properties.campus_name ?? district.properties.bid_name ?? "Study boundary")}</h2>
              <dl>
                <dt>Boundary type</dt>
                <dd>{String(district.properties.kind ?? "—")}</dd>
                <dt>Borough</dt>
                <dd>{String(district.properties.borough ?? "—")}</dd>
                {district.properties.owner_label ? (
                  <>
                    <dt>Owner</dt>
                    <dd>{String(district.properties.owner_label)}</dd>
                    <dt>Provenance</dt>
                    <dd>{String(district.properties.provenance ?? "—")}</dd>
                  </>
                ) : null}
                <dt>Footprints (bbox)</dt>
                <dd>{district.footprintsInBbox.toLocaleString("en-US")}</dd>
                <dt>Modeled in set</dt>
                <dd>
                  {district.footprints
                    .filter(
                      (f) =>
                        (f.properties as Record<string, unknown>)?.net_thermal_kbtu_ft2_yr !=
                        null
                    )
                    .length.toLocaleString("en-US")}
                </dd>
              </dl>
              <p className="dossier-note">
                {String(district.properties.disclaimer ?? "")}
              </p>
              {district.basis === "bounding_box" && (
                <p className="dossier-note">
                  Count is bounding-box based, not polygon containment — a
                  footprint straddling the boundary is included.
                </p>
              )}
            </aside>
          )}
          {building && (
            <aside
              className="dossier"
              aria-label="Building dossier"
              data-testid="building-dossier"
            >
              <h2>{building.footprint.name ?? building.bbl ?? "Building"}</h2>
              <dl>
                <dt>BBL</dt>
                <dd>{building.bbl}</dd>
                <dt>BIN</dt>
                <dd>{building.bin ?? "—"}</dd>
                <dt>Construction year</dt>
                <dd>{fmtCell(building.footprint.construction_year)}</dd>
                <dt>Roof height</dt>
                <dd>{fmtCell(building.footprint.height_roof)} ft</dd>
                <dt>Footprint area</dt>
                <dd>{fmtCell(building.footprint.shape_area)} ft²</dd>
              </dl>
              <h3 className="dossier-sub">Building profile (MapPLUTO)</h3>
              {building.pluto ? (
                <dl>
                  <dt>Land use</dt><dd>{plutoLandUse(building.pluto.land_use)}</dd>
                  <dt>Building class</dt><dd>{building.pluto.bldg_class ?? "—"}</dd>
                  <dt>Owner type</dt><dd>{plutoOwnerType(building.pluto.ownertype)}</dd>
                  <dt>Owner</dt><dd>{building.pluto.ownername ?? "—"}</dd>
                  <dt>Assessed value</dt>
                  <dd>{building.pluto.assess_total == null ? "—" : "$" + Math.round(building.pluto.assess_total).toLocaleString()}
                    {building.pluto.assess_land != null && (
                      <span className="dossier-mini"> · land ${Math.round(building.pluto.assess_land).toLocaleString()}</span>
                    )}
                  </dd>
                  <dt>Exempt value</dt>
                  <dd>{building.pluto.exempt_total == null ? "—" : "$" + Math.round(building.pluto.exempt_total).toLocaleString()}</dd>
                  <dt>Zoning</dt>
                  <dd>{[building.pluto.zone_dist1, building.pluto.zone_dist2].filter(Boolean).join(" · ") || "—"}</dd>
                  <dt>Lot / bldg area</dt>
                  <dd>
                    {building.pluto.lot_area == null ? "—" : Math.round(building.pluto.lot_area).toLocaleString()} ft² /{" "}
                    {building.pluto.bldg_area == null ? "—" : Math.round(building.pluto.bldg_area).toLocaleString()} ft²
                    {building.pluto.built_far != null && (
                      <span className="dossier-mini"> · FAR {building.pluto.built_far.toFixed(2)}</span>
                    )}
                  </dd>
                  <dt>Structure</dt>
                  <dd>
                    {building.pluto.num_bldgs ?? "—"} bldg(s) · {building.pluto.num_floors ?? "—"} floors
                  </dd>
                  <dt>Community district</dt><dd>{building.pluto.cd ?? "—"}</dd>
                  <dt>Address</dt><dd>{building.pluto.address ?? "—"} {(building.pluto.zip_code) && <span className="dossier-mini">{building.pluto.zip_code}</span>}</dd>
                  <dt>Landmark</dt><dd>{building.pluto.landmark ?? "—"}</dd>
                  <dt>Condo flag</dt><dd>{building.pluto.condo_no ?? "—"}</dd>
                </dl>
              ) : (
                <p className="dossier-note">
                  No MapPLUTO record for this BBL (condo lot or unmapped parcel).
                </p>
              )}
              <h3 className="dossier-sub">Observed (LL84)</h3>
              {building.observed ? (
                <dl>
                  <dt>Property ID</dt>
                  <dd>{String(building.observed.property_id ?? "—")}</dd>
                  <dt>Site EUI</dt>
                  <dd>
                    {fmtCell(building.observed.site_eui_kbtu_ft as number | null)}{" "}
                    kBtu/ft²·yr
                  </dd>
                  <dt>GFA (self-reported)</dt>
                  <dd>
                    {fmtCell(building.observed.gfa_sqft as number | null)} ft²
                  </dd>
                  <dt>GHG (location-based)</dt>
                  <dd>
                    {fmtCell(building.observed.total_ghg_tco2e as number | null)}{" "}
                    tCO2e
                  </dd>
                </dl>
              ) : (
                <p className="dossier-note">{building.evidence.ll84_note}</p>
              )}

              <h3 className="dossier-sub">Modeled annual demand</h3>
              {building.modeled?.has_end_uses ? (
                <dl>
                  <dt>Space heating</dt>
                  <dd>
                    {fmtCell(building.modeled.space_heating_kbtu_ft2_yr)}{" "}
                    kBtu/ft²·yr
                  </dd>
                  <dt>Domestic hot water</dt>
                  <dd>{fmtCell(building.modeled.dhw_kbtu_ft2_yr)} kBtu/ft²·yr</dd>
                  <dt>Cooling</dt>
                  <dd>{fmtCell(building.modeled.cooling_kbtu_ft2_yr)} kBtu/ft²·yr</dd>
                  <dt>Evidence tier</dt>
                  <dd>{building.modeled.evidence_tier ?? "—"}</dd>
                  <dt>Archetype</dt>
                  <dd>{building.modeled.archetype ?? "—"}</dd>
                </dl>
              ) : (
                <p className="dossier-note">
                  {building.modeled?.note ?? building.evidence.modeled_note}
                </p>
              )}
            </aside>
          )}
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
