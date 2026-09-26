"""Build citywide LL97 compliance screening (Article 320 pathway) for all
LL84-covered properties.

Method (deliberately conservative and explicit):
  * Annual consumption per LL84 property from the CY2024 benchmarking slice
    (electricity kBtu, natural gas kBtu, #2/#4 fuel oils kBtu, district steam
    kBtu) — held FLAT across years as the no-action baseline.
  * Fuel GHG coefficients per LL97 §28-320.3.1.1 / DOB's published schedule:
      2024-2029: elec 0.000288962 t/kWh, gas 0.00005311 t/kBtu,
                 #2 0.00007421, #4 0.00007529, steam 0.00004493
      2030-2034: elec 0.000145 t/kWh, steam 0.0000432 (others unchanged)
      (2035+ excluded from the score card: Article 320.3.4 sets those limits
       by rule, not statute — noted, not charted.)
  * Occupancy group from MapPLUTO bldg_class first letter (the code's own
    §28-320.3.1/.3.2 items 1-10 limits, tCO2e/sf):
      2024-29: A .01074 B .00846 E/I-4 .00758 I-1 .01138 F .00574
               special{B-lab,B-ambulatory,H,I-2,I-3} .02381 M .01181
               R-1 .00987 R-2 .00675 S/U .00426
      2030-34: A .00420 B .00453 E/I-4 .00344 I-1 .00598 F .00167 special
               .01330 M .00403 R-1 .00526 R-2 .00407 S/U .00110
  * Covered building = GFA > 25,000 sf. Covered BLANK if GFA missing.
  * Penalty = $268/tCO2e over the limit (§28-320.6.1), accrued per year in
    period. NOT modeled: Article 321 (rent-regulated/worship) pathway,
    special-case adjustments, deductions (REC/PV), and per-space ESPM
    weighting (we hold lot-level class, not tenant-space composition).

Output: data/citywide/ll97_compliance.json — consumed by /api/ll97 and the
static LL97 analysis tab.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path("/mnt/e/OC_Projects/projects/signalnyc")
LL84 = ROOT / "data/citywide/ll84_citywide_2024_v2.raw.jsonl"
PLUTO_DB = ROOT / "data/citywide/mappluto_lots.sqlite"
OUT = ROOT / "data/citywide/ll97_compliance.json"

ELEC_KBTU_PER_KWH = 3.412142

COEF = {
    "electricity":   {"p1": 0.000288962, "p2": 0.000145},     # tCO2e / kWh
    "natural_gas":   {"p1": 0.00005311,  "p2": 0.00005311},   # tCO2e / kBtu
    "fuel_oil_2":    {"p1": 0.00007421,  "p2": 0.00007421},
    "fuel_oil_4":    {"p1": 0.00007529,  "p2": 0.00007529},
    "district_steam":{"p1": 0.00004493,  "p2": 0.0000432},
}
LIMITS = {
    "p1": {"A": 0.01074, "B": 0.00846, "E": 0.00758, "I4": 0.00758,
           "I1": 0.01138, "F": 0.00574, "SPECIAL": 0.02381, "M": 0.01181,
           "R1": 0.00987, "R2": 0.00675, "SU": 0.00426},
    "p2": {"A": 0.00420, "B": 0.00453, "E": 0.00344, "I4": 0.00344,
           "I1": 0.00598, "F": 0.00167, "SPECIAL": 0.01330, "M": 0.00403,
           "R1": 0.00526, "R2": 0.00407, "SU": 0.00110},
}
PENALTY_PER_T = 268.0
P1_YEARS, P2_YEARS = 6, 5

OCC = {
    "A0100": ("A", "Assembly"), "A": ("A", "Assembly"),
    "B": ("B", "Business"), "O": ("B", "Office (Business)"),
    "E": ("E", "Education"),
    "F": ("F", "Factory/Industrial"), "J": ("F", "Manufacturing"),
    "G": ("SU", "Garage/Storage"), "S": ("SU", "Storage"),
    "U": ("SU", "Utility/Misc"), "V": ("SU", "Vacant"),
    "P": ("SU", "Parking"),
    "H": ("R1", "Hotel"), "N": ("R1", "Accommodations"),
    "M": ("M", "Mercantile"),
    "I": ("SPECIAL", "Institutional/Hospital"),
    "K": ("B", "Mixed (bulk office-weighted)"),
    "R": ("R2", "Residential"), "L": ("R2", "Elevator residential"),
    "C": ("A", "Cultural/Assembly"), "D": ("SU", "Transitory (S/U-like)"),
    "T": ("A", "Transient misc"), "Q": ("B", "Other"),
    "W": ("SU", "Other"), "Y": ("R2", "Selected residential"),
    "Z": ("B", "Mixed commercial"),
}


def occupancy_of(bldg_class: str | None) -> tuple[str, str, str]:
    """-> (limit_key, occupancy label, note)"""
    if not bldg_class:
        return ("B", "Unclassified", "bldg_class missing; defaulted to office (B)")
    grp = bldg_class[:1].upper()
    key, label = OCC.get(grp, ("B", "Other (defaulted to B)"))
    note = ""
    if grp == "I":
        # I-1 (care) vs I-2/3 (hospitals) — conservative: use the higher
        # SPECIAL limit unless class suggests residential care.
        if bldg_class[:2].upper() in {"I1"}:
            key = "I1"
        label = "Institutional"
    return key, label, note


def parse_bbl(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 10:
        return digits
    return None


def main() -> None:
    t0 = time.time()
    # pluto bldg_class by bbl
    pcon = sqlite3.connect(f"file:{PLUTO_DB}?mode=ro", uri=True)
    pcon.row_factory = sqlite3.Row
    classes = dict(
        (r["bbl"], r["bldg_class"]) for r in pcon.execute(
            "SELECT bbl, bldg_class FROM lots WHERE bldg_class IS NOT NULL")
    )
    pcon.close()
    print(f"pluto classes: {len(classes):,} ({time.time()-t0:.1f}s)", flush=True)

    rows_out = []
    covered = 0
    with open(LL84) as fh:
        for line in fh:
            d = json.loads(line)
            gfa = d.get("property_gfa_self_reported")
            try:
                gfa = float(gfa) if gfa is not None else None
            except (TypeError, ValueError):
                gfa = None
            if gfa is None or gfa <= 0:
                continue
            def num(x):
                try:
                    return float(x) if x is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0
            elec_kwh = num(d.get("electricity_use_grid_purchase")) / ELEC_KBTU_PER_KWH
            gas = num(d.get("natural_gas_use_kbtu"))
            oil2 = num(d.get("fuel_oil_2_use_kbtu"))
            oil4 = num(d.get("fuel_oil_4_use_kbtu"))
            oi56 = num(d.get("fuel_oil_5_6_use_kbtu")) or num(d.get("fuel_oil_1_use_kbtu"))
            steam = num(d.get("district_steam_use_kbtu"))
            has_fuel = any(v > 0 for v in (elec_kwh, gas, oil2, oil4, oi56, steam))
            if not has_fuel:
                rows_out.append({
                    "property_id": d.get("property_id"),
                    "name": d.get("address_1"),
                    "bbl": parse_bbl(d.get("nyc_borough_block_and_lot")),
                    "gfa": gfa,
                    "status": "no-consumption-data",
                })
                continue

            bbl = parse_bbl(d.get("nyc_borough_block_and_lot"))
            bcls = classes.get(bbl or "")
            lkey, occ_label, occ_note = occupancy_of(bcls)

            def fuel_t(fuel_key: str, quantity: float, is_elec: bool):
                if not quantity:
                    return {"p1": 0.0, "p2": 0.0}
                c = COEF[fuel_key]
                return {
                    "p1": quantity * c["p1"],
                    "p2": quantity * c["p2"],
                }

            contrib = {
                "electricity": fuel_t("electricity", elec_kwh, True),
                "natural_gas": fuel_t("natural_gas", gas, False),
                "fuel_oil_2": fuel_t("fuel_oil_2", oil2, False),
                "fuel_oil_4": fuel_t("fuel_oil_4", oil4 + oi56, False),
                "district_steam": fuel_t("district_steam", steam, False),
            }
            e1 = sum(v["p1"] for v in contrib.values())
            e2 = sum(v["p2"] for v in contrib.values())

            is_covered = bool(gfa and gfa > 25000)
            lim1 = LIMITS["p1"][lkey] * gfa
            lim2 = LIMITS["p2"][lkey] * gfa
            pen1 = max(0.0, e1 - lim1) * PENALTY_PER_T * P1_YEARS if is_covered else None
            pen2 = max(0.0, e2 - lim2) * PENALTY_PER_T * P2_YEARS if is_covered else None
            rows_out.append({
                "property_id": d.get("property_id"),
                "name": d.get("address_1"),
                "bbl": bbl,
                "bldg_class": bcls,
                "occupancy_group": (lkey if lkey != "SU" else "S/U"),
                "occupancy_label": occ_label + (f" ({occ_note})" if occ_note else ""),
                "gfa": gfa,
                "covered": is_covered,
                "consumption": {
                    "electricity_kwh": round(elec_kwh),
                    "natural_gas_kbtu": round(gas),
                    "fuel_oil_2_kbtu": round(oil2),
                    "fuel_oil_4_56_kbtu": round(oil4 + oi56),
                    "district_steam_kbtu": round(steam),
                },
                "emissions_t": {"p1": round(e1, 1), "p2": round(e2, 1)},
                "emissions_intensity_t_sf": {
                    "p1": round(e1 / gfa, 6), "p2": round(e2 / gfa, 6)},
                "limit_t": {"p1": round(lim1, 1), "p2": round(lim2, 1)},
                "limit_intensity_t_sf": {
                    "p1": LIMITS["p1"][lkey], "p2": LIMITS["p2"][lkey]},
                "penalty_est": {"p1": None if pen1 is None else round(pen1),
                                "p2": None if pen2 is None else round(pen2),
                                "total": (None if (pen1 is None or pen2 is None)
                                          else round(pen1 + pen2))},
                "status": ("compliant" if (e1 <= lim1 and e2 <= lim2)
                           else ("breach-2030" if e1 <= lim1 else "breach-now")),
            })
            if is_covered:
                covered += 1
        # done
    payload = {
        "note": ("LL97 Article 320 screening. Annual GHG = CY2024 fuel use held "
                 "flat x law's fuel coefficient schedule; limit = occupancy-group "
                 "intensity (MapPLUTO building class → BC occupancy group) x GFA; "
                 "penalty = $268/tCO2e over, per year. NOT modeled: Article 321 "
                 "pathway (rent-regulated/worship), per-space ESPM weighting, "
                 "adjustments, deductions (REC/PV), electrification actions, "
                 "electricity coefficient changes 2035+. Source slice is the published "
                 "CY2024 LL84 dataset (39,090 rows); a property filing under a "
                 "different publication mechanism is not present here. "
                 "Screening only, not legal guidance."),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "coef_note": {"elec_t_per_kwh": COEF["electricity"],
                      "gas_t_per_kbtu": COEF["natural_gas"],
                      "steam_t_per_kbtu": COEF["district_steam"]},
        "counts": {
            "properties": len(rows_out),
            "covered_GFA_gt25k": covered,
        },
        "properties": rows_out,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB) {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
