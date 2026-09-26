// LL97 Article 320 screening rows and the per-property pathway chart helper.

export interface Ll97Row {
  property_id: string | null;
  name: string | null;
  bbl: string | null;
  bldg_class: string | null;
  occupancy_group: string;
  occupancy_label: string;
  gfa: number;
  covered: boolean;
  status: "compliant" | "breach-2030" | "breach-now" | string;
  consumption?: {
    electricity_kwh: number;
    natural_gas_kbtu: number;
    fuel_oil_2_kbtu: number;
    fuel_oil_4_56_kbtu: number;
    district_steam_kbtu: number;
  };
  emissions_t?: { p1: number; p2: number };
  limit_t?: { p1: number; p2: number };
  emissions_intensity_t_sf?: { p1: number; p2: number };
  limit_intensity_t_sf?: { p1: number; p2: number };
  penalty_est?: { p1: number | null; p2: number | null; total: number | null };
}

export interface Ll97Payload {
  note: string;
  generated: string;
  coef_note: Record<string, unknown>;
  counts: Record<string, number>;
  properties: Ll97Row[];
  truncated?: boolean;
}

const API_BASE = "";

export async function fetchLl97(): Promise<Ll97Payload> {
  const r = await fetch(`${API_BASE}/api/ll97`, {
    headers: { Accept: "application/json" },
  });
  if (!r.ok) throw new Error(`/api/ll97: ${r.status}`);
  return r.json();
}

/** Years the scorecard covers, with each year's emissions and limit. */
export function pathway(row: Ll97Row): {
  year: number;
  emissions: number;
  limit: number;
  penalty: number | null;
}[] {
  const out: { year: number; emissions: number; limit: number; penalty: number | null }[] = [];
  for (let y = 2024; y <= 2029; y++) {
    out.push({
      year: y,
      emissions: row.emissions_t?.p1 ?? 0,
      limit: row.limit_t?.p1 ?? 0,
      penalty: row.emissions_t && row.limit_t
        ? Math.max(0, (row.emissions_t.p1 - row.limit_t.p1)) * 268 : null,
    });
  }
  for (let y = 2030; y <= 2034; y++) {
    out.push({
      year: y,
      emissions: row.emissions_t?.p2 ?? 0,
      limit: row.limit_t?.p2 ?? 0,
      penalty: row.emissions_t && row.limit_t
        ? Math.max(0, (row.emissions_t.p2 - row.limit_t.p2)) * 268 : null,
    });
  }
  return out;
}
