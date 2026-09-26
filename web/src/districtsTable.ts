// Districts portfolio table: one row per study district (BID or campus) with
// the aggregate stats from /api/districts_portfolio (static: baked
// districts_portfolio.json). Aggregate-only — joining 273 district polygons
// against 1.08M footprints per request is not viable, so the summary build
// (scripts/build_districts_summary.py) computes it once, centroid-in-polygon.

export interface DistrictPortfolioRow {
  district_id: string;
  kind: "bid" | "campus";
  name: string;
  borough: string | null;
  year_found: number | null;
  members: number;
  ll84_props: number;
  gfa_total: number | null;
  site_eui_median: number | null;
  wn_eui_median: number | null;
  net_thermal_median: number | null;
  heating_kbtu_total: number | null;
  cooling_kbtu_total: number | null;
  dhw_kbtu_total: number | null;
  tier1: number;
  tier2: number;
  tier4: number;
  heating_dominant: number;
  cooling_dominant: number;
}

export interface DistrictPortfolioPayload {
  note: string;
  generated: string;
  districts: DistrictPortfolioRow[];
  returned: number;
}

const API_BASE = "";

export async function fetchDistrictsPortfolio(): Promise<DistrictPortfolioPayload> {
  const r = await fetch(`${API_BASE}/api/districts_portfolio`, {
    headers: { Accept: "application/json" },
  });
  if (!r.ok) throw new Error(`/api/districts_portfolio: ${r.status}`);
  return r.json();
}
