// Typed client for the SignalNYC explorer API. All optional values must be
// treated as nullable — missing source data arrives as explicit null.

export interface PropertySummary {
  property_id: string;
  address_1: string | null;
  bbl: string | null;
  bin: string | null;
  bin_count: number;
  latitude: number | null;
  longitude: number | null;
  gfa_sqft: number | null;
  site_eui_kbtu_ft: number | null;
  total_ghg_tco2e: number | null;
  electricity_kbtu: number | null;
  natural_gas_kbtu: number | null;
  missing_fields: string[];
}

export interface PropertyDetail extends PropertySummary {
  parent_property_id: string | null;
  postal_code: string | null;
  borough_block_lot_raw: string | null;
  report_year: string | null;
  weather_normalized_site_eui: number | null;
  direct_ghg_tco2e: number | null;
}

export interface SearchResponse {
  query: string | null;
  missing_field: string | null;
  total: number;
  returned: number;
  offset: number;
  limit: number;
  properties: PropertySummary[];
}

/** Modelled annual demand for one lot. Nulls mean "not modelled", never zero. */
export interface ModeledDemandSummary {
  bbl: string;
  borough: string | null;
  land_use: string | null;
  bldg_area_sqft: number | null;
  space_heating_kbtu: number | null;
  dhw_kbtu: number | null;
  cooling_kbtu: number | null;
  space_heating_kbtu_ft2_yr: number | null;
  dhw_kbtu_ft2_yr: number | null;
  cooling_kbtu_ft2_yr: number | null;
  evidence_tier: string | null;
  archetype: string | null;
  /** False for T4 coverage-only rows: present, but without end-use numbers. */
  has_end_uses: boolean;
  note?: string;
}

/** Everything known about one building, keyed by BBL (map-click entry point). */
export interface PlutoProfile {
  borough: string | null;
  block: string | null;
  lot: string | null;
  lot_area: number | null;
  bldg_area: number | null;
  built_far: number | null;
  num_bldgs: number | null;
  num_floors: number | null;
  year_built: number | null;
  land_use: string | null;
  bldg_class: string | null;
  zone_dist1: string | null;
  zone_dist2: string | null;
  ownertype: string | null;
  ownername: string | null;
  assess_land: number | null;
  assess_total: number | null;
  exempt_total: number | null;
  landmark: string | null;
  condo_no: string | null;
  cd: number | null;
  zip_code: string | null;
  address: string | null;
}

export interface BuildingDetail {
  bbl: string;
  bin: string | null;
  pluto: PlutoProfile | null;
  footprint: {
    name: string | null;
    height_roof: number | null;
    construction_year: number | null;
    shape_area: number | null;
    has_ll84: boolean | null;
  };
  observed: Record<string, unknown> | null;
  modeled: ModeledDemandSummary | null;
  evidence: {
    has_footprint: boolean;
    has_ll84_join: boolean;
    ll84_note: string;
    has_modeled_demand: boolean;
    modeled_note: string;
  };
  geometry?: unknown;
}

export interface SnapshotInfo {
  snapshot_utc: string | null;
  sha256: string | null;
  rows: number;
  slice_name: string | null;
  socrata_dataset: string | null;
}

export interface Counters {
  total: number;
  with_coordinates: number;
  has_eui: number;
  has_ghg: number;
  has_gas: number;
  has_electricity: number;
  multi_bin_properties: number;
}

export const MISSING_FIELD_OPTIONS = [
  { value: "", label: "No missing-value filter" },
  { value: "site_eui_kbtu_ft", label: "Site EUI missing" },
  { value: "total_location_based_ghg", label: "Total GHG missing" },
  { value: "natural_gas_use_kbtu", label: "Natural gas missing" },
  { value: "electricity_use_grid_purchase", label: "Electricity missing" },
] as const;

async function getJson<T>(url: string, attempt = 1): Promise<T> {
  try {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = String(body.detail);
      } catch {
        /* keep status text */
      }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  } catch (e) {
    // One retry for transient network failures (e.g. tailnet reconnects).
    if (attempt < 2) {
      await new Promise((r) => setTimeout(r, 600));
      return getJson<T>(url, attempt + 1);
    }
    throw e;
  }
}

export const api = {
  snapshot: () => getJson<SnapshotInfo>("/api/snapshot"),
  counters: () => getJson<Counters>("/api/counters"),
  footprints: () => getJson<unknown>("/api/footprints"),
  search: (params: {
    q?: string;
    missing_field?: string;
    limit?: number;
    offset?: number;
  }) => {
    const usp = new URLSearchParams();
    if (params.q) usp.set("q", params.q);
    if (params.missing_field) usp.set("missing_field", params.missing_field);
    if (params.limit) usp.set("limit", String(params.limit));
    if (params.offset) usp.set("offset", String(params.offset));
    const s = usp.toString();
    return getJson<SearchResponse>(`/api/properties${s ? `?${s}` : ""}`);
  },
  detail: (propertyId: string) =>
    getJson<PropertyDetail>(`/api/properties/${encodeURIComponent(propertyId)}`),
};

export function fmtInt(v: number | null, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}
