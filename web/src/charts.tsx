/**
 * Right-rail visualizations for the SignalNYC explorer.
 *
 * These are deliberately dependency-free inline SVG: the bundle stays small
 * and there is no chart library to keep in sync with the palette.
 *
 * HONESTY RULES baked in here, because a chart implies more authority than a
 * table does:
 *  - Every card states the grain it summarizes and that it covers only the
 *    currently loaded/visible set, not the whole city.
 *  - Missing values are counted and reported, never treated as zero.
 *  - Modeled (estimated) themes are visually marked as modeled.
 *  - Totals are annual observed fuel totals; end-use splits are modeled and
 *    are never presented as measured.
 */
import type { ThemeDef } from "./symbology";

export interface FootFeature {
  type: string;
  geometry: unknown;
  properties: Record<string, unknown>;
}

interface Props {
  features: FootFeature[];
  theme: ThemeDef;
  selectedId: string | null;
  onSelect: (pid: string) => void;
}

/** Diverging themes carry their real key in `decidingField`; observed ones use `field`. */
function decidingField(theme: ThemeDef): string {
  return (theme as { decidingField?: string }).decidingField ?? theme.field;
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmt(v: number): string {
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 100 ? 1 : 2;
  return v.toLocaleString("en-US", { maximumFractionDigits: d });
}

function fmtCompact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return `${v.toFixed(a < 10 ? 1 : 0)}`;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** Themes whose values can legitimately be negative (net thermal demand). */
function isDiverging(theme: ThemeDef): boolean {
  return (theme as { rampStart?: number }).rampStart !== undefined;
}

function isModeled(theme: ThemeDef): boolean {
  return theme.id === "net_thermal_kbtu_ft2_yr" ||
    /space_heating|dhw|cooling/.test(theme.id);
}

/** Colors reused from the diverging convention so charts match the map. */
const NEG = "#3b82f6";
const POS = "#ef4444";
const NEUTRAL = "#cbd5e1";
const ACCENT = "#38bdf8";
const GRAY = "#94a3b8";

export default function Charts({ features, theme, selectedId, onSelect }: Props) {
  const field = decidingField(theme);

  const values: { pid: string; label: string; v: number }[] = [];
  let nullCount = 0;
  for (const f of features) {
    const v = num(f.properties[field]);
    if (v === null) {
      nullCount += 1;
      continue;
    }
    values.push({
      pid: String(f.properties.pid ?? f.properties.id ?? ""),
      label: String(f.properties.address_1 ?? "(no address)"),
      v,
    });
  }
  const sorted = [...values.map((d) => d.v)].sort((a, b) => a - b);
  const median = quantile(sorted, 0.5);
  const p90 = quantile(sorted, 0.9);
  const p10 = quantile(sorted, 0.1);

  return (
    <div className="viz-rail" data-testid="viz-rail">
      <div className="viz-head">
        <h2>Analysis</h2>
        <span className="viz-scope">
          {values.length.toLocaleString("en-US")} with data ·{" "}
          {nullCount.toLocaleString("en-US")} no data
        </span>
      </div>

      {/* ---------- Summary strip ---------- */}
      <section className="viz-card">
        <h3>{theme.label}</h3>
        <div className="viz-units">{theme.units}</div>
        <div className="stat-strip">
          <div className="stat">
            <span className="stat-k">median</span>
            <span className="stat-v">{Number.isFinite(median) ? fmt(median) : "—"}</span>
          </div>
          <div className="stat">
            <span className="stat-k">p10</span>
            <span className="stat-v">{Number.isFinite(p10) ? fmt(p10) : "—"}</span>
          </div>
          <div className="stat">
            <span className="stat-k">p90</span>
            <span className="stat-v">{Number.isFinite(p90) ? fmt(p90) : "—"}</span>
          </div>
        </div>
        <p className="viz-note">
          {theme.grain}
          {isModeled(theme) ? " · modeled estimate, not measured" : ""}
        </p>
      </section>

      {/* ---------- Distribution ---------- */}
      <Distribution values={sorted} theme={theme} nullCount={nullCount} />

      {/* ---------- Net thermal split (the sign convention at a glance) ---------- */}
      <NetSplit features={features} />

      {/* ---------- Fuel mix ---------- */}
      <FuelMix features={features} />

      {/* ---------- Top contributors (clickable) ---------- */}
      <TopList values={values} theme={theme} selectedId={selectedId} onSelect={onSelect} />
    </div>
  );
}

/**
 * Histogram over the active theme's own breaks, so the bars line up with the
 * legend. Nulls are shown as a separate gray bar rather than dropped, so the
 * chart never implies full coverage.
 */
function Distribution({
  values,
  theme,
  nullCount,
}: {
  values: number[];
  theme: ThemeDef;
  nullCount: number;
}) {
  if (values.length === 0) {
    return (
      <section className="viz-card">
        <h3>Distribution</h3>
        <p className="viz-note">No values for this layer in the loaded set.</p>
      </section>
    );
  }
  const lo = values[0];
  const hi = values[values.length - 1];
  const diverging = isDiverging(theme);

  // Outlier handling: a handful of extreme records (e.g. one property whose
  // self-reported fuel total implies ~10,000 kBtu/ft²) is 17x the p99 and
  // would flatten every other bin into a single bar. Clip the AXIS to p1..p99
  // and count the records beyond it into explicit overflow bins, so the tail
  // is visible and its size is stated rather than silently hidden.
  const axisLo = quantile(values, 0.01);
  const axisHi = quantile(values, 0.99);
  const below = values.filter((v) => v < axisLo).length;
  const above = values.filter((v) => v > axisHi).length;

  // Bin edges start from the theme's breaks (aligned with the legend), then
  // extend to cover the axis range.
  const core = diverging
    ? theme.breaks.filter((b) => b !== 0)
    : [0, ...theme.breaks];
  const edges = Array.from(new Set([axisLo, ...core, axisHi]))
    .filter((e) => e >= axisLo && e <= axisHi)
    .sort((a, b) => a - b);
  if (edges.length < 2) edges.push(axisHi === axisLo ? axisLo + 1 : axisHi);

  const counts = new Array(edges.length - 1).fill(0);
  for (const v of values) {
    if (v < axisLo || v > axisHi) continue; // counted as overflow, not binned
    let placed = false;
    for (let i = 0; i < edges.length - 1; i += 1) {
      const isLast = i === edges.length - 2;
      if (v >= edges[i] && (isLast ? v <= edges[i + 1] : v < edges[i + 1])) {
        counts[i] += 1;
        placed = true;
        break;
      }
    }
    if (!placed) counts[counts.length - 1] += 1;
  }
  const max = Math.max(...counts, 1);
  const W = 320;
  const H = 116;
  const barW = W / counts.length;
  const mid = quantile(values, 0.5);

  const center = (v: number) => {
    const t = (v - edges[0]) / (edges[edges.length - 1] - edges[0] || 1);
    return 26 + t * (W - 40);
  };

  return (
    <section className="viz-card">
      <h3>Distribution</h3>
      <svg
        className="viz-svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Distribution of the active layer's values"
      >
        {counts.map((c, i) => {
          const h = Math.max(1, (c / max) * (H - 42));
          const binCenter = (edges[i] + edges[i + 1]) / 2;
          let fill = ACCENT;
          if (diverging) {
            fill = binCenter < 0 ? NEG : binCenter > 0 ? POS : NEUTRAL;
          }
          return (
            <rect
              key={i}
              x={26 + i * barW + 0.6}
              y={H - 22 - h}
              width={Math.max(1, barW - 1.2)}
              height={h}
              fill={fill}
              opacity={0.9}
            />
          );
        })}
        {/* median marker */}
        <line
          x1={center(mid)}
          x2={center(mid)}
          y1={6}
          y2={H - 22}
          stroke="#e8edf4"
          strokeWidth={1}
          strokeDasharray="3 2"
        />
        <text x={center(mid) + 3} y={12} className="viz-tick" fill="#e8edf4">
          med {fmt(mid)}
        </text>
        {/* zero line for diverging layers */}
        {diverging && edges[0] < 0 && edges[edges.length - 1] > 0 && (
          <line
            x1={center(0)}
            x2={center(0)}
            y1={4}
            y2={H - 22}
            stroke="#647083"
            strokeWidth={0.8}
          />
        )}
        <text x={26} y={H - 8} className="viz-tick" fill="#94a0b4">
          {fmtCompact(edges[0])}
        </text>
        <text
          x={W - 14}
          y={H - 8}
          textAnchor="end"
          className="viz-tick"
          fill="#94a0b4"
        >
          {fmtCompact(edges[edges.length - 1])}
        </text>
      </svg>
      <div className="viz-legend">
        {diverging && (
          <>
            <span><i style={{ background: NEG }} /> cooling side (&lt;0)</span>
            <span><i style={{ background: POS }} /> heating side (&gt;0)</span>
          </>
        )}
        {nullCount > 0 && (
          <span><i style={{ background: GRAY }} /> {nullCount} no data (excluded)</span>
        )}
      </div>
      {(below > 0 || above > 0) && (
        <p className="viz-note">
          Axis clipped to p1–p99 ({fmt(axisLo)} … {fmt(axisHi)}) to keep the
          shape readable; <b>{below}</b> below and <b>{above}</b> above range
          are excluded from the bars. Actual range {fmt(lo)} … {fmt(hi)}.
        </p>
      )}
      <p className="viz-note">
        Bins follow the layer's own legend breaks. Covers the loaded set only.
      </p>
    </section>
  );
}

/**
 * Heating vs cooling split. This is the check that the diverging layer is
 * actually centered: a nearly one-sided split means a model artifact, not a
 * finding.
 */
function NetSplit({ features }: { features: FootFeature[] }) {
  const nets: number[] = [];
  for (const f of features) {
    const v = num(f.properties.net_thermal_kbtu_ft2_yr);
    if (v !== null) nets.push(v);
  }
  if (nets.length === 0) return null;
  const cooling = nets.filter((v) => v < 0).length;
  const heating = nets.filter((v) => v > 0).length;
  const total = nets.length;
  const s = [...nets].sort((a, b) => a - b);
  const mid = quantile(s, 0.5);
  const cPct = (cooling / total) * 100;

  return (
    <section className="viz-card">
      <h3>Net thermal split</h3>
      <div className="split-bar" role="img" aria-label="Cooling vs heating share">
        <div className="split-cool" style={{ width: `${cPct}%` }} />
        <div className="split-heat" style={{ width: `${100 - cPct}%` }} />
      </div>
      <div className="split-labels">
        <span style={{ color: NEG }}>
          <b>{cooling}</b> cooling-dominant
        </span>
        <span style={{ color: POS }}>
          <b>{heating}</b> heating-dominant
        </span>
      </div>
      <div className="stat-strip">
        <div className="stat">
          <span className="stat-k">median net</span>
          <span className="stat-v">{fmt(mid)}</span>
        </div>
        <div className="stat">
          <span className="stat-k">min</span>
          <span className="stat-v">{fmt(s[0])}</span>
        </div>
        <div className="stat">
          <span className="stat-k">max</span>
          <span className="stat-v">{fmt(s[s.length - 1])}</span>
        </div>
      </div>
      <p className="viz-note">
        Modeled net annual thermal demand (heating + DHW − cooling), kBtu/ft²·yr.
        A lopsided split usually signals a modeling artifact rather than a real
        finding.
      </p>
    </section>
  );
}

/** Annual fuel totals across the loaded set, grouped by energy carrier. */
function FuelMix({ features }: { features: FootFeature[] }) {
  const groups: { key: string; label: string; keys: string[]; heat: boolean }[] = [
    {
      key: "elec",
      label: "Electricity (grid)",
      keys: ["electricity_use_grid_purchase"],
      heat: false,
    },
    {
      key: "gas",
      label: "Natural gas",
      keys: ["natural_gas_use_kbtu"],
      heat: true,
    },
    {
      key: "steam",
      label: "District steam",
      keys: ["district_steam_use_kbtu"],
      heat: true,
    },
    {
      key: "dhw",
      label: "District hot water",
      keys: ["district_hot_water_use_kbtu"],
      heat: true,
    },
    {
      key: "oil",
      label: "Fuel oil / diesel / propane",
      keys: [
        "fuel_oil_1_use_kbtu",
        "fuel_oil_2_use_kbtu",
        "fuel_oil_4_use_kbtu",
        "fuel_oil_5_6_use_kbtu",
        "diesel_2_use_kbtu",
        "propane_use_kbtu",
      ],
      heat: true,
    },
  ];

  const totals = groups.map((g) => ({
    ...g,
    sum: features.reduce(
      (acc, f) =>
        acc + g.keys.reduce((a, k) => a + (num(f.properties[k]) ?? 0), 0),
      0
    ),
  }));
  const grand = totals.reduce((a, t) => a + t.sum, 0);
  if (grand <= 0) return null;
  const max = Math.max(...totals.map((t) => t.sum), 1);

  return (
    <section className="viz-card">
      <h3>Annual fuel mix</h3>
      <div className="mix-rows">
        {totals.map((t) => (
          <div className="mix-row" key={t.key}>
            <span className="mix-label">{t.label}</span>
            <span className="mix-track">
              <span
                className="mix-fill"
                style={{
                  width: `${(t.sum / max) * 100}%`,
                  background: t.heat ? POS : NEG,
                }}
              />
            </span>
            <span className="mix-val">
              {(t.sum / 1e9).toFixed(2)} TBtu
            </span>
          </div>
        ))}
      </div>
      <p className="viz-note">
        Reported annual fuel totals, summed over the loaded set only — not a
        citywide figure. Carriers are grouped as reported; end-use splits are
        modeled separately. Total {fmtCompact(grand)} kBtu.
      </p>
    </section>
  );
}

/** Largest values for the active layer; clicking a row selects it on the map. */
function TopList({
  values,
  theme,
  selectedId,
  onSelect,
}: {
  values: { pid: string; label: string; v: number }[];
  theme: ThemeDef;
  selectedId: string | null;
  onSelect: (pid: string) => void;
}) {
  if (values.length === 0) return null;
  const top = [...values].sort((a, b) => b.v - a.v).slice(0, 10);

  return (
    <section className="viz-card">
      <h3>Top 10 by {theme.label.toLowerCase()}</h3>
      <ol className="top-list">
        {top.map((t) => (
          <li key={t.pid}>
            <button
              type="button"
              className={`top-btn${t.pid === selectedId ? " active" : ""}`}
              onClick={() => onSelect(t.pid)}
              title={t.label}
            >
              <span className="top-name">{t.label}</span>
              <span className="top-val">{fmt(t.v)}</span>
            </button>
          </li>
        ))}
      </ol>
      <p className="viz-note">
        Click a row to locate it on the map. Ranked within the loaded set only.
      </p>
    </section>
  );
}
