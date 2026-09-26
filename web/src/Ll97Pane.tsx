// LL97 analysis pane: scorecard table + per-property pathway chart.
import { useMemo, useState } from "react";
import {
  type Ll97Row,
  pathway as ll97Pathway,
} from "./ll97Table";

const fmtT = (v: number | null | undefined) =>
  v == null ? "—" : Math.round(v).toLocaleString();
const fmtUsd = (v: number | null | undefined) =>
  v == null ? "—" : "$" + Math.round(v).toLocaleString();

const STATUS_LABEL: Record<string, string> = {
  "compliant": "compliant",
  "breach-now": "breaches 2024 limit",
  "breach-2030": "2024 ok · 2030 breach",
  "no-consumption-data": "no consumption data",
};

function StatusChip({ status }: { status: string }) {
  const cls =
    status === "compliant"
      ? "chip-ok"
      : status === "breach-now"
        ? "chip-bad"
        : status === "breach-2030"
          ? "chip-warn"
          : "chip-mut";
  return <span className={`tag ${cls}`}>{STATUS_LABEL[status] ?? status}</span>;
}

/** Compact SVG bar chart: year-by-year emissions vs limit, penalties shaded. */
function PathwayChart({ row }: { row: Ll97Row }) {
  const pts = useMemo(() => ll97Pathway(row), [row]);
  const maxV = Math.max(
    1,
    ...pts.map((p) => Math.max(p.emissions, p.limit)),
  );
  const W = 340, H = 120, padL = 6, padB = 16;
  const bw = (W - padL * 2) / pts.length;
  const yScale = (v: number) => H - padB - (v / maxV) * (H - padB - 4);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="LL97 pathway">
        {pts.map((p, i) => {
          const x = padL + i * bw;
          const yE = yScale(p.emissions);
          const yL = yScale(p.limit);
          const over = p.emissions > p.limit;
          const top = Math.min(yE, yL);
          const h = Math.abs(yE - yL);
          return (
            <g key={p.year}>
              {/* over-limit wedge */}
              {over && h > 0 && (
                <rect x={x} y={top} width={bw * 0.8} height={h} fill="#f87171" opacity="0.85" />
              )}
              {/* emissions bar */}
              <rect x={x} y={yE} width={bw * 0.55} height={H - padB - yE}
                fill={over ? "#b91c1c" : "#34d399"} opacity="0.9" />
              {/* limit line cap */}
              <rect x={x} y={yL} width={bw * 0.8} height={1.5} fill="#facc15" />
              {(i === 0 || p.year === 2030 || p.year === pts[pts.length - 1].year) && (
                <text x={x + 2} y={H - 4} fontSize="7.5" fill="var(--muted)">{p.year}</text>
              )}
            </g>
          );
        })}
      </svg>
      <p className="dossier-note" style={{ marginTop: 4 }}>
        Green = emissions under limit · red wedge = over-limit exposure.
        Yellow = statutory limit. Demand held at CY2024 actuals for every year.
      </p>
    </div>
  );
}

export function Ll97Pane({ rows, err }: { rows: Ll97Row[] | null; err: string | null }) {
  const [filter, setFilter] = useState<"all" | "breach-now" | "breach-2030" | "compliant">("all");
  const [selected, setSelected] = useState<Ll97Row | null>(null);
  const [q, setQ] = useState("");

  const shown = useMemo(() => {
    if (!rows) return [];
    let list = rows;
    if (filter !== "all") list = list.filter((r) => r.status === filter);
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      list = list.filter(
        (r) =>
          (r.name ?? "").toLowerCase().includes(needle) ||
          String(r.bbl ?? "").includes(needle),
      );
    }
    return list;
  }, [rows, filter, q]);

  if (err) return <p className="empty">LL97 load failed: {err}</p>;
  if (!rows) return <p className="empty">Loading LL97 screening…</p>;

  return (
    <div>
      <div className="ll97-filters">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by name or BBL…"
          className="ll97-search"
        />
        {(["all", "breach-now", "breach-2030", "compliant"] as const).map((f) => (
          <button
            key={f}
            className={filter === f ? "tab active" : "tab"}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : STATUS_LABEL[f]}
            {f !== "all" && (
              <span className="dossier-mini"> {rows.filter((r) => r.status === f).length}</span>
            )}
          </button>
        ))}
      </div>
      <div className="table-scroll ll97-pane">
        <table>
          <thead>
            <tr>
              <th>Property</th>
              <th>Group</th>
              <th className="num">GFA ft²</th>
              <th className="num">Emissions 24-29 t</th>
              <th className="num">Limit</th>
              <th className="num">Emissions 30-34 t</th>
              <th className="num">Limit</th>
              <th className="num">Penalty est.</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={(r.property_id ?? r.bbl ?? r.name) as string}
                  onClick={() => setSelected(r === selected ? null : r)}
                  className={r === selected ? "selected" : ""}>
                <td>{r.name ?? r.bbl ?? "(unnamed)"}</td>
                <td>{r.occupancy_group}{r.occupancy_group === "R2" ? " res" : ""}</td>
                <td className="num">{r.gfa ? Math.round(r.gfa).toLocaleString() : "—"}</td>
                <td className="num">{fmtT(r.emissions_t?.p1)}</td>
                <td className="num">{fmtT(r.limit_t?.p1)}</td>
                <td className="num">{fmtT(r.emissions_t?.p2)}</td>
                <td className="num">{fmtT(r.limit_t?.p2)}</td>
                <td className="num">{r.penalty_est ? fmtUsd(r.penalty_est.total) : "—"}</td>
                <td><StatusChip status={r.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="empty">No matching properties{filter !== "all" ? " with this status" : ""}.</p>
        )}
      </div>
      {selected && (
        <aside className="dossier" data-testid="ll97-pathway-dossier">
          <h2>{selected.name ?? selected.bbl ?? "Property"}</h2>
          <dl>
            <dt>BBL</dt><dd>{selected.bbl ?? "—"}</dd>
            <dt>Occupancy</dt><dd>{selected.occupancy_label}</dd>
            <dt>GFA</dt><dd>{Math.round(selected.gfa).toLocaleString()} ft²</dd>
            <dt>Status</dt><dd><StatusChip status={selected.status} /></dd>
            <dt>Penalty estimate</dt>
            <dd>{selected.penalty_est?.total ? fmtUsd(selected.penalty_est.total) : "none under flat demand"}</dd>
          </dl>
          <h3 className="dossier-sub">LL97 pathway (no-action)</h3>
          <PathwayChart row={selected} />
          <p className="dossier-note">{""}</p>
        </aside>
      )}
    </div>
  );
}
