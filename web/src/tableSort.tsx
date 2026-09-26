// Reusable clickable-th sorting for the districts and LL97 tables.
import { useMemo } from "react";

export interface SortState {
  key: string;
  asc: boolean;
}

/** Sort rows by `state.key`; numbers numerically, everything else as text; nulls last. */
export function useSorted<T>(rows: T[] | null, state: SortState | null, pick: (row: T, key: string) => unknown): T[] {
  return useMemo(() => {
    if (!rows) return [];
    if (!state) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const va = pick(a, state.key);
      const vb = pick(b, state.key);
      let cmp: number;
      if (va == null && vb == null) cmp = 0;
      else if (va == null) cmp = 1;
      else if (vb == null) cmp = -1;
      else if (typeof va === "number" && typeof vb === "number") cmp = va - vb;
      else cmp = String(va).localeCompare(String(vb));
      return state.asc ? cmp : -cmp;
    });
    return copy;
  }, [rows, state, pick]);
}

/** Clickable header cell; toggles asc/desc, first click is asc (desc for numeric columns if pass descFirst). */
export function SortHead({
  label,
  sortKey,
  state,
  onToggle,
  num,
}: {
  label: string;
  sortKey: string;
  state: SortState | null;
  onToggle: (key: string) => void;
  num?: boolean;
}) {
  const active = state?.key === sortKey;
  return (
    <th
      className={`${num ? "num" : ""} th-sort`}
      onClick={() => onToggle(sortKey)}
      role="button"
      aria-sort={active ? (state.asc ? "ascending" : "descending") : "none"}
      title="Click to sort"
    >
      {label}
      <span className={`sort-arrow ${active ? "on" : ""}`}>
        {active ? (state.asc ? " ▲" : " ▼") : ""}
      </span>
    </th>
  );
}
