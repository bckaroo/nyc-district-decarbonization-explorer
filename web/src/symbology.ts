// Symbology themes for the footprint layer —SignalNYC map switcher (DEV-157).
//
// Every theme in OBSERVED_THEMES colors an OBSERVED field from the joined
// LL84 snapshot (src: ENERGY_NUMERIC_FIELDS in scripts/build_footprints.py,
// dataset 5zyy-y8am). No theme here estimates heating or cooling demand:
// LL84 fuel totals are NOT end-use loads. Reported natural gas includes
// domestic hot water, cooking and other non-heating uses; purchased
// electricity covers all end uses (lighting, plug loads, cooling, fans...).
// See docs/map-symbology.md.

export interface ThemeDef {
  /** Stable id used by MapPanel state and tests. */
  id: string;
  /** Short UI label. Units are appended by MapPanel as a unit-bearing sublabel. */
  label: string;
  /** Footprint feature property key this theme colors (must exist on served GeoJSON). */
  field: string;
  /** Exact units string shown in the legend (verified vs LL84 metadata; see DEV-157). */
  units: string;
  /** Reporting grain for the disclosure line: what the colored row actually measures. */
  grain: string;
  /**
   * True when the field is an ANNUAL TOTAL that the campus-disaggregation step
   * distributes across footprints (DISAGG_FIELDS in build_footprints.py: GHG,
   * natural gas, purchased electricity). Intensity themes (per-ft² EUI, GHG
   * intensity) and self-reported GFA are property-level and NOT disaggregated.
   */
  disaggregated: boolean;
  /** Normalized metric (weather-normalized) — labeled as such in the legend. */
  weatherNormalized: boolean;
  /** <html-color> stops, low → high. Ramp colors may repeat across themes. */
  ramp: string[];
  /** Ascending numeric breakpoints; length = ramp.length - 1. */
  breaks: number[];
  /** Rounded examples for the legend ticks. */
  legendStops: string[];
  /** Gray color for null/missing values. */
  nullGray: string;
  /** Value mapped to ramp[0] (bottom of the ramp). Default 0; diverging themes set it below break[0]. */
  rampStart?: number;
}

/**
 * A modeled theme carries ESTIMATED end-use values, not observed fuel or
 * reported LL84 columns. Every grain must say so explicitly.
 */
export interface ModeledThemeDef extends Omit<ThemeDef, "grain"> {
  decidingField: string;
  grain: string;
  modelNote: string;
}

// Shared observed-energy ramp (skill-verified EUI ramp, reused elsewhere).
const OBS_RAMP = ["#22d3ee", "#34d399", "#fbbf24", "#fb923c", "#f87171"];
const NUM_GRAY = "#94a3b8";

// Site EUI ramp aligned with observed pilot distribution: median 74, p90 125,
// p99 562 kBtu/ft²·yr (LL84 Property-level intensity, NOT campus-disaggregated).
function euiTheme(
  id: string,
  label: string,
  field: "site_eui_kbtu_ft" | "weather_normalized_site_eui",
  weatherNormalized: boolean
): ThemeDef {
  return {
    id,
    label,
    field,
    units: "kBtu/ft²·yr",
    grain: "Per-LL84-property (EUI is a per-sqft ratio, not disaggregated to footprint)",
    disaggregated: false,
    weatherNormalized,
    ramp: OBS_RAMP,
    breaks: [40, 75, 125, 240],
    legendStops: ["0", "40", "75", "125", "240+"],
    nullGray: NUM_GRAY,
  };
}

// Annual totals (campus-disaggregated to footprint where the feature is part of
// a multi-BIN/child property; single-BIN features carry the property value as-is).

// Annual purchased electricity from the grid, unit-confirmed kBtu in LL84.
// Socrata metadata for 5zyy-y8am (verified via live fetch 2026-09-25): the
// UNSUFFIXED electricity_use_grid_purchase column is "Grid Purchase (kBtu)";
// the kWh companion is …_1 (kWh) and …_3 is "Grid Purchase + Onsite Renewables
// (kBtu)" — a different total, not a unit sibling. This dataset carries only
// the unsuffixed column, so the served values are kBtu (1 kWh = 3.412 kBtu).
// Pilot distribution in kWh: median ~4.6M, p90 ~29M, p99 ~99M → kBtu roughly
// ×3.412, so breaks sit at 3.4M / 10M / 20M / 40M kBtu.
const ELEC_THEME: ThemeDef = {
  id: "electricity_use_grid_purchase",
  label: "Purchased electricity (annual)",
  field: "electricity_use_grid_purchase",
  units: "kBtu/yr",
  grain: "All-grid end uses (lighting, plug loads, cooling, fans…); reported property totals with estimated area-weighted footprint shares where allocated",
  disaggregated: true,
  weatherNormalized: false,
  ramp: OBS_RAMP,
  breaks: [3.4e6, 10e6, 20e6, 40e6],
  legendStops: ["0", "3.4M", "10M", "20M", "40M+"],
  nullGray: NUM_GRAY,
};

// Annual natural gas, reported in kBtu. NOTE: includes DHW/cooking/etc, not
// heating-only. Campus totals area-weighted; nulls stay gray (188/932 pilot).
const GAS_THEME: ThemeDef = {
  id: "natural_gas_use_kbtu",
  label: "Natural gas (annual)",
  field: "natural_gas_use_kbtu",
  units: "kBtu/yr",
  grain: "All reported gas end uses (heating + DHW + cooking + …); reported property totals with estimated area-weighted footprint shares where allocated",
  disaggregated: true,
  weatherNormalized: false,
  ramp: OBS_RAMP,
  breaks: [5e5, 2e6, 5e6, 12e6],
  legendStops: ["0", "0.5M", "2M", "5M", "12M+"],
  nullGray: NUM_GRAY,
};

// Annual total (location-based) GHG, metric tons CO2e. Campus totals
// area-weighted; NOT a per-sqft intensity (that's direct_ghg_emissions_intensity).
const GHG_THEME: ThemeDef = {
  id: "total_location_based_ghg",
  label: "Total GHG — location-based (annual)",
  field: "total_location_based_ghg",
  units: "tCO₂e/yr",
  grain: "Total annual emissions at reporting-property grain, with estimated area-weighted footprint shares where allocated",
  disaggregated: true,
  weatherNormalized: false,
  ramp: OBS_RAMP,
  breaks: [250, 1000, 3500, 10000],
  legendStops: ["0", "250", "1k", "3.5k", "10k+"],
  nullGray: NUM_GRAY,
};

// GFA (self-reported property sqft, campus area-weighted to footprint here).
const GFA_THEME: ThemeDef = {
  id: "property_gfa_self_reported",
  label: "Property floor area",
  field: "property_gfa_self_reported",
  units: "ft²",
  grain: "Self-reported LL84 property GFA — reported property-level total, not a measured footprint area (not in DISAGG_FIELDS, so no allocation)",
  disaggregated: false,
  weatherNormalized: false,
  ramp: OBS_RAMP,
  breaks: [10000, 50000, 150000, 500000],
  legendStops: ["0", "10k", "50k", "150k", "500k+"],
  nullGray: NUM_GRAY,
};

/**
 * MODELED annual end-use estimates (DEV-160). Source: v1.0 additive end-use
 * split model (scripts/build_annual_demand.py + annual_demand_params.json):
 * LL84 reported fuels → archetype end-use shares constrained by LL84 →
 * delivered end-use demand (heating efficiency η=0.80 gas, cooling COP=3.0).
 * NOT measured. Each feature carries an evidence_tier property; nulls = the
 * model could not estimate (never zero). Served from
 * data/snapshots/footprints_joined_demand.geojson via /api/footprints.
 */
const MODEL_GRAIN =
  "Modeled/estimated per footprint: annual end-use demand derived from LL84 reported fuels via archetype shares constrained by LL84 (heating efficiency eta=0.8 gas, cooling COP=3.0) — NOT measured or reported; nulls = model could not estimate (never zero); evidence_tier property on each footprint labels its input quality.";

/** Net thermal distribution (computed by scripts/build_annual_demand.py):
 * p5 −163, p25 −104, median −72, p75 −37, p90 −11, p95 −1, p99 +23 kBtu/ft²·yr —
 * most of this pilot cluster is cooling-dominated, so the diverging ramp's
 * neutral band sits in ±10 and the outer breaks follow the pilot quantiles. */
const NET_THERMAL_THEME: ModeledThemeDef = {
  id: "net_thermal_kbtu_ft2_yr",
  label: "Net thermal demand (modeled)",
  field: "NET_THERMAL_SENTINEL",
  decidingField: "net_thermal_kbtu_ft2_yr",
  units: "kBtu/ft²·yr",
  grain: MODEL_GRAIN,
  modelNote:
    "net = modeled space heating + DHW − modeled cooling per ft²·yr; positive = net heating demand (red), negative = net cooling demand (blue). Diverging breaks chosen from the pilot's net-thermal quantiles, not a fixed ULI palette.",
  disaggregated: false,
  weatherNormalized: false,
  // Deep blue (net cooling, negative) → white/neutral exactly at 0 → deep red
  // (net heating, positive), ULI-style annual net thermal demand ramp.
  // Stops are symmetric so 0 evaluates to the exact neutral stop.
  ramp: ["#1d4ed8", "#3b82f6", "#93c5fd", "#f1f5f9", "#fca5a5", "#ef4444", "#b91c1c"],
  breaks: [-100, -20, 20, 100, 300],
  rampStart: -300,
  legendStops: ["−300", "−100", "−20", "0", "+20", "+100", "+300+"],
  nullGray: NUM_GRAY,
};

function modeledSingleRampTheme(
  id: string,
  label: string,
  field: "space_heating_kbtu_ft2_yr" | "dhw_kbtu_ft2_yr" | "cooling_kbtu_ft2_yr",
  p50: number,
  p90: number,
  p99: number
): ModeledThemeDef {
  const breaks = [p50, p90, p99, p99 * 3].map((b) => Math.round(b * 10) / 10);
  return {
    id,
    label,
    field,
    decidingField: field,
    units: "kBtu/ft²·yr",
    grain: MODEL_GRAIN,
    modelNote: `modeled/estimated per archetype split constrained by LL84, eta=0.8 gas, COP cooling — NOT measured; pilot quantiles: p50 ${p50}, p90 ${p90}, p99 ${Math.round(p99)}; evidence_tier property provided per footprint`,
    disaggregated: false,
    weatherNormalized: false,
    ramp: ["#dbeafe", "#93c5fd", "#60a5fa", "#2563eb", "#1e40af"],
    breaks,
    legendStops: ["0", `${breaks[0]}`, `${breaks[1]}`, `${breaks[2]}`, `${breaks[3]}+`],
    nullGray: NUM_GRAY,
  };
}

export const MODELED_THEMES: ModeledThemeDef[] = [
  NET_THERMAL_THEME,
  modeledSingleRampTheme("heating_demand", "Space-heating demand (modeled)", "space_heating_kbtu_ft2_yr", 12.6, 45.2, 299.9),
  modeledSingleRampTheme("dhw_demand", "Domestic hot water demand (modeled)", "dhw_kbtu_ft2_yr", 4.7, 18.6, 124.8),
  modeledSingleRampTheme("cooling_demand", "Space-cooling demand (modeled)", "cooling_kbtu_ft2_yr", 88.5, 158.2, 286.7),
];

/**
 * Themes NOT yet available — candidate map modes still lacking reliable data
 * or a reviewed model. Currently empty: DEV-160 enabled the modeled set above.
 */
export interface UnavailableMode {
  id: string;
  label: string;
  reason: string;
}
export const UNAVAILABLE_THEMES: UnavailableMode[] = [];


/** Observed themes, in selector order. */
export const OBSERVED_THEMES: ThemeDef[] = [
  euiTheme("site_eui_kbtu_ft", "Site EUI (weather-actual)", "site_eui_kbtu_ft", false),
  euiTheme("weather_normalized_site_eui", "Site EUI (weather-normalized)", "weather_normalized_site_eui", true),
  ELEC_THEME,
  GAS_THEME,
  GHG_THEME,
  GFA_THEME,
];

export function getTheme(id: string): ThemeDef | undefined {
  return OBSERVED_THEMES.find((t) => t.id === id) ?? MODELED_THEMES.find((t) => t.id === id);
}

/** Resolution helper: searched across all modeable themes incl. modeled. */
export function getModeledTheme(id: string): ModeledThemeDef | undefined {
  return MODELED_THEMES.find((t) => t.id === id);
}

export const ALL_SELECTABLE_THEMES = [...OBSERVED_THEMES, ...MODELED_THEMES];

/**
 * MapLibre paint expression for a theme: `case`-guarded interpolate ramp.
 * Null/missing → theme.nullGray (not zero, not dropped). Non-numeric junk
 * (if any slips into the geojson) also routes to gray via to-number guard.
 */
export function colorExpr(theme: ThemeDef): unknown {
  // Ascending stops: rampStart (default 0) -> breaks[0..n-1] -> topStop
  // (default: breaks[last] * 2 − rampStart, so the last color holds the tail).
  const start = theme.rampStart ?? 0;
  const top = start >= 0 ? theme.breaks[theme.breaks.length - 1] * 2 : theme.breaks[theme.breaks.length - 1] + 200;
  const stops: Array<[number, string]> = [];
  for (let i = 0; i < theme.ramp.length; i++) {
    const v = i === 0 ? start : i <= theme.breaks.length ? theme.breaks[i - 1] : top;
    stops.push([Math.round(v * 10) / 10, theme.ramp[i]]);
  }
  stops.sort((a, b) => a[0] - b[0]);
  const ramp: unknown[] = stops.flatMap(([v, c]) => [v, c]);
  return [
    "case",
    // typeof guard: anything not a plain number (null, "N/A", missing, NaN)
    // routes to theme.nullGray so the interpolate never receives a bad input.
    ["!=", ["typeof", ["get", theme.field]], "number"],
    theme.nullGray,
    ["interpolate", ["linear"], ["get", theme.field], ...ramp] as never,
  ];
}

/**
 * Legend color swatches matching the colorExpr order (0 to break[0] =
 * ramp[0], break[i-1] to break[i] = ramp[i], break[last]+ = ramp[last]).
 */
export function legendSwatches(theme: ThemeDef): { color: string; text: string }[] {
  return theme.legendStops.map((text, i) => ({ color: theme.ramp[i], text }));
}

/** Helper for tests: the exact breaks used in the color ramp. */
export function themeBreaks(theme: ThemeDef): number[] {
  return [...theme.breaks];
}

/**
 * `fill-opacity` expression: features with no value are dimmed so that gray
 * reads as "no data, not zero"; selected features get full opacity and a hot
 * outline-stroke hint (handled in MapPanel via line layer).
 */
export function opacityExpr(theme: ThemeDef, selectedPid: string | null): unknown {
  // Modeled themes carry the real key in decidingField (field is a sentinel);
  // check value presence on the deciding property the same way color does.
  const deciding = (theme as { decidingField?: string }).decidingField;
  const valueKey = deciding ?? theme.field;
  return [
    "case",
    ["==", ["get", "pid"], selectedPid ?? "__none__"],
    0.92, // selected
    ["!=", ["typeof", ["get", valueKey]], "number"],
    0.16, // null / no data — dimmed gray, NOT zero
    0.55, // normal
  ];
}
