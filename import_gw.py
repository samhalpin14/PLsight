"""Import a gameweek CSV of FotMob stats into team_match_stats.

The CSV has one row per team per match (so 2 rows per fixture, 20 per gameweek).
Column spec is in fotmob_template.csv.

It validates before writing, and cross-checks shots / shots on target / corners
against the football-data.co.uk rows already in `match`. That second source is
what catches transcription errors — use it.

Usage
-----
    python import_gw.py gw01.csv                # validate, then load
    python import_gw.py gw01.csv --dry-run      # validate only, write nothing
    python import_gw.py gw01.csv --force        # load despite warnings
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd
import psycopg2

from ingest import DB, make_match_id
from teams import canonical, check_frame

NUMERIC_INT = [
    "touches_opp_box", "big_chances", "big_chances_missed",
    "shots", "shots_on_target", "shots_off_target", "blocked_shots", "woodwork",
    "shots_inside_box", "shots_outside_box",
    "passes", "accurate_passes", "passes_own_half", "passes_opp_half",
    "accurate_long_balls", "accurate_crosses", "throws", "offsides",
    "duels_won", "ground_duels_won", "aerial_duels_won", "successful_dribbles",
    "tackles", "interceptions", "blocks", "clearances", "keeper_saves",
]
NUMERIC_DEC = [
    "possession_pct", "xg", "xg_open_play", "xg_set_play", "xg_non_penalty", "xgot",
    "pass_accuracy_pct", "long_ball_pct", "cross_pct",
    "attack_left_pct", "attack_centre_pct", "attack_right_pct",
    "ground_duel_pct", "aerial_duel_pct", "dribble_pct",
]
STAT_COLS = NUMERIC_INT + NUMERIC_DEC
KEY_COLS = ["date", "home_team", "away_team", "team"]


def blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or v == ""


def check_row(r, errors, warnings, label):
    """Internal consistency. Errors block the load; warnings don't."""
    def g(c):
        v = r.get(c)
        return None if blank(v) else float(v)

    shots, sot, off, blk = g("shots"), g("shots_on_target"), g("shots_off_target"), g("blocked_shots")
    if None not in (shots, sot, off, blk) and abs((sot + off + blk) - shots) > 0.5:
        errors.append(f"{label}: shots {shots:.0f} != on {sot:.0f} + off {off:.0f} + blocked {blk:.0f}")

    ins, out = g("shots_inside_box"), g("shots_outside_box")
    if None not in (shots, ins, out) and abs((ins + out) - shots) > 0.5:
        errors.append(f"{label}: shots {shots:.0f} != inside {ins:.0f} + outside {out:.0f}")

    xg, npxg, op, sp = g("xg"), g("xg_non_penalty"), g("xg_open_play"), g("xg_set_play")
    if xg is not None and not (0 <= xg <= 8):
        errors.append(f"{label}: xg {xg} outside 0-8, likely a typo")
    if None not in (xg, npxg) and npxg > xg + 0.01:
        errors.append(f"{label}: non-penalty xg {npxg} > xg {xg}")
    if None not in (xg, op, sp) and abs((op + sp) - xg) > 0.05:
        warnings.append(f"{label}: open play {op} + set play {sp} != xg {xg} (penalties?)")

    bc, bcm = g("big_chances"), g("big_chances_missed")
    if None not in (bc, bcm) and bcm > bc:
        errors.append(f"{label}: big chances missed {bcm:.0f} > big chances {bc:.0f}")

    p, ap = g("passes"), g("accurate_passes")
    if None not in (p, ap) and ap > p:
        errors.append(f"{label}: accurate passes {ap:.0f} > passes {p:.0f}")

    # The passing block gets misread as a unit and stays internally consistent,
    # so own+opp==accurate is not enough. FotMob prints the accuracy percentage
    # too; that is the only independent tie on this block. It caught Hull in GW1.
    acc_pct = g("pass_accuracy_pct")
    if None not in (p, ap, acc_pct) and abs(100 * ap / p - acc_pct) > 1.0:
        errors.append(f"{label}: {ap:.0f}/{p:.0f} = {100*ap/p:.1f}% "
                      f"but FotMob states {acc_pct:.0f}%")

    oh, ph = g("passes_own_half"), g("passes_opp_half")
    if None not in (ap, oh, ph) and abs((oh + ph) - ap) > max(3, ap * 0.03):
        warnings.append(f"{label}: own {oh:.0f} + opp {ph:.0f} half != accurate passes {ap:.0f}")

    zl, zc, zr = g("attack_left_pct"), g("attack_centre_pct"), g("attack_right_pct")
    if None not in (zl, zc, zr) and abs((zl + zc + zr) - 100) > 1.5:
        errors.append(f"{label}: attacking zones sum to {zl + zc + zr:.0f}, not 100")

    gd, ad, dw = g("ground_duels_won"), g("aerial_duels_won"), g("duels_won")
    if None not in (gd, ad, dw) and abs((gd + ad) - dw) > 0.5:
        warnings.append(f"{label}: ground {gd:.0f} + aerial {ad:.0f} != duels won {dw:.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--division", default="E0")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="load despite validation errors")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    missing = [c for c in KEY_COLS if c not in df.columns]
    if missing:
        sys.exit(f"CSV is missing required columns: {missing}")

    # --- names and shape, before anything else ----------------------------
    # A name that resolves to nothing still imports as "19 rows loaded" with one
    # line of warning buried in the output, so stop here instead. check_frame
    # also catches wrong row counts and malformed fixtures.
    structural = check_frame(df, source="fotmob")
    if structural:
        sys.exit("Not loading — fix these first:\n  " + "\n  ".join(structural))

    errors, warnings = [], []

    # --- no two rows may carry an identical stat vector --------------------
    # The GW5 source had the Leeds v Palace block duplicated from Bournemouth v
    # Liverpool. Every per-row and per-fixture check passed, because the block
    # was internally valid; it was just the wrong block.
    stat_cols = [c for c in STAT_COLS if c in df.columns]
    if stat_cols:
        seen = {}
        for i, r in df.iterrows():
            key = tuple(None if blank(r.get(c)) else float(r[c]) for c in stat_cols)
            if key in seen:
                errors.append(f"{r['team']} has stats identical to {seen[key]} — "
                              f"a duplicated block in the source?")
            else:
                seen[key] = r["team"]

    # --- pair checks: possession must sum to 100 --------------------------
    for (d, h, a), grp in df.groupby(["date", "home_team", "away_team"]):
        label = f"{d} {h} v {a}"
        if len(grp) != 2:
            errors.append(f"{label}: {len(grp)} rows, expected 2")
            continue
        poss = [x for x in grp.get("possession_pct", pd.Series(dtype=float)) if not blank(x)]
        if len(poss) == 2 and abs(sum(float(p) for p in poss) - 100) > 1.5:
            errors.append(f"{label}: possession sums to {sum(float(p) for p in poss):.1f}, not 100")

        A, B = grp.iloc[0], grp.iloc[1]
        # Duels are contested, so each side's share must complete the other's.
        for name, col in [("ground", "ground_duel_pct"), ("aerial", "aerial_duel_pct")]:
            a, b = A.get(col), B.get(col)
            if not blank(a) and not blank(b) and abs(float(a) + float(b) - 100) > 2.5:
                errors.append(f"{label}: {name} duel % sums to {float(a)+float(b):.0f}, not 100")
        # My blocked shots are their blocks. Transcribed from different panels,
        # so this cross-checks the shots block against the defensive block.
        for X, Y in [(A, B), (B, A)]:
            bs, bl = X.get("blocked_shots"), Y.get("blocks")
            if not blank(bs) and not blank(bl) and abs(float(bs) - float(bl)) > 2:
                warnings.append(f"{label}: {X['team']} blocked shots {float(bs):.0f} "
                                f"vs {Y['team']} blocks {float(bl):.0f}")

    for _, r in df.iterrows():
        check_row(r, errors, warnings, f"{r['date']} {r['team']}")

    # --- cross-check against football-data --------------------------------
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    for _, r in df.iterrows():
        date = datetime.strptime(str(r["date"]), "%Y-%m-%d").date()
        home = canonical(str(r["home_team"]).strip(), "footballdata")
        away = canonical(str(r["away_team"]).strip(), "footballdata")
        team = canonical(str(r["team"]).strip(), "footballdata")
        mid = make_match_id(date, home, away, args.division)
        is_home = team == home

        cur.execute("SELECT home_shots, away_shots, home_sot, away_sot FROM match WHERE match_id=%s", (mid,))
        row = cur.fetchone()
        if row is None:
            warnings.append(f"{mid}: no football-data row yet — cannot cross-check "
                            f"(run ingest.py first, or the CSV isn't published yet)")
            continue
        fd_shots = row[0] if is_home else row[1]
        fd_sot = row[2] if is_home else row[3]
        if fd_shots is not None and not blank(r.get("shots")) and int(r["shots"]) != fd_shots:
            warnings.append(f"{date} {team}: shots {int(r['shots'])} (FotMob) vs {fd_shots} (football-data)")
        if fd_sot is not None and not blank(r.get("shots_on_target")) and int(r["shots_on_target"]) != fd_sot:
            warnings.append(f"{date} {team}: SoT {int(r['shots_on_target'])} (FotMob) vs {fd_sot} (football-data)")

    # --- report ------------------------------------------------------------
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    print(f"\n{len(df)} rows · {len(errors)} errors · {len(warnings)} warnings")

    if errors and not args.force:
        sys.exit("\nNot loading. Fix the errors above, or rerun with --force.")
    if args.dry_run:
        print("Dry run — nothing written.")
        return

    # --- load --------------------------------------------------------------
    cols = [c for c in STAT_COLS if c in df.columns]
    loaded = 0
    for _, r in df.iterrows():
        date = datetime.strptime(str(r["date"]), "%Y-%m-%d").date()
        home = canonical(str(r["home_team"]).strip(), "footballdata")
        away = canonical(str(r["away_team"]).strip(), "footballdata")
        team = canonical(str(r["team"]).strip(), "footballdata")
        mid = make_match_id(date, home, away, args.division)
        is_home = team == home

        cur.execute("SELECT team_id FROM team WHERE name=%s", (team,))
        got = cur.fetchone()
        if got is None:
            print(f"  ! unknown team {team!r} — add it to teams.py")
            continue
        tid = got[0]

        cur.execute("SELECT 1 FROM match WHERE match_id=%s", (mid,))
        if cur.fetchone() is None:
            print(f"  ! no match row for {mid} — run ingest.py first")
            continue

        vals = [None if blank(r.get(c)) else r[c] for c in cols]
        setters = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
        cur.execute(f"""
            INSERT INTO team_match_stats (match_id, team_id, is_home, {", ".join(cols)})
            VALUES (%s,%s,%s,{", ".join(["%s"] * len(cols))})
            ON CONFLICT (match_id, team_id) DO UPDATE SET
                is_home=EXCLUDED.is_home, {setters}, loaded_at=now()
        """, [mid, tid, is_home] + vals)
        loaded += 1

    conn.commit()
    cur.close()
    conn.close()

    if loaded != len(df):
        print(f"\n  WARNING: {len(df) - loaded} of {len(df)} rows did not load. "
              f"The gameweek is incomplete in the database.")
        print("  Scroll up for the '! unknown team' or '! no match row' lines.")
        sys.exit(f"Loaded {loaded} of {len(df)} rows — not a complete gameweek.")
    print(f"Loaded {loaded} team-match rows.")


if __name__ == "__main__":
    main()
