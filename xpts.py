"""Expected points — what each side's performances were worth.

    python xpts.py                                  # table to the terminal
    python xpts.py --chart preds/xpts_gw03.png      # and the tile
    python xpts.py --through 3                      # only up to gameweek 3

METHODOLOGY
-----------
The league table tells you who has points. It doesn't tell you who earned them.
Expected points asks a different question: given the chances both sides created
in a match, how often would that performance win?

For each match:

  1. Take the team's xG as one average (lambda_for) and the opponent's xG as the
     other (lambda_against).
  2. Assume each side's goals land at random around its own average, following a
     Poisson distribution:  P(k goals) = e^-lambda * lambda^k / k!
  3. Multiply the two to get the probability of every scoreline, then sum the
     cells where the team wins, draws and loses.
  4. xPts = 3 * P(win) + 1 * P(draw).

Then add those up across a season.

Worked example — Fulham 2-3 Crystal Palace, GW3. Fulham made 3.14 xG, Palace
1.97. That gives Fulham P(win) 0.614, P(draw) 0.161, P(loss) 0.226, so the
afternoon was worth 2.00 points. They got none.

WHAT IT ASSUMES, AND WHERE THAT BITES
-------------------------------------
Two assumptions, and both are wrong in the same direction.

  * The two sides' scoring is INDEPENDENT. It isn't. Low-scoring matches are
    correlated — which is exactly what the rho term in predict.py exists to
    correct. Real football produces more 0-0s and 1-1s than independent Poisson
    says it should.

  * Goals are POISSON around xG. They aren't. Match xG is the sum of many small
    independent shot probabilities, which produces LESS spread than a Poisson
    with the same mean.

Both errors reduce the modelled chance of a draw. So this method understates
draws, which makes it very slightly too generous to the side that was on top and
too harsh on the side that was outplayed. State that if you publish the number.

The proper version simulates every individual chance rather than the match
total. FotMob gives us match totals only, so this is the version available.

A NOTE ON WHAT IT IS NOT
------------------------
xPts is not a prediction. It is a description of matches already played, with
the finishing and goalkeeping taken out. A side well above their xPts has been
finishing well or being saved by their keeper; a side below has not. Neither
tells you what happens next on its own — that is what predict.py is for.
"""
from __future__ import annotations

import argparse
import math

import pandas as pd
import psycopg2

from ingest import DB

# --------------------------------------------------------------- brand
BG     = "#0F1315"   # background
PAPER  = "#F1EFEA"   # primary text
MUTED  = "#9BA1A4"   # secondary text
ACCENT = "#E8933A"   # over-performance, brand amber
SLATE  = "#4E5559"   # under-performance
LINE   = "#242B2E"   # hairlines
GREEN  = "#4FB477"   # better than the other column suggests
RED    = "#E0574B"   # worse

SHORT = {
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Newcastle United": "Newcastle",   "Nottingham Forest": "Forest",
    "Wolverhampton Wanderers": "Wolves", "Crystal Palace": "Palace",
    "Brighton & Hove Albion": "Brighton", "West Bromwich Albion": "West Brom",
    "Sheffield United": "Sheffield Utd", "Aston Villa": "Villa",
}


# --------------------------------------------------------------- maths
def poisson(k: int, lam: float) -> float:
    """Probability of exactly k goals when the average is lam."""
    return math.exp(-lam) * lam ** k / math.factorial(k)


def match_xpts(lam_for: float, lam_against: float, cap: int = 15):
    """Win/draw/loss probabilities and expected points for one match.

    `cap` is where we stop counting scorelines. It has to be generous: at ten
    the probabilities still only sum to 0.998 and the missing tail is all in the
    win column, which flatters the stronger side by about 0.1 of a point. At
    fifteen they sum to 1.000.
    """
    win = draw = loss = 0.0
    for i in range(cap):
        for j in range(cap):
            p = poisson(i, lam_for) * poisson(j, lam_against)
            if i > j:
                win += p
            elif i == j:
                draw += p
            else:
                loss += p
    return win, draw, loss, 3 * win + draw


# --------------------------------------------------------------- data
def load(conn, season: str, division: str, through: int | None):
    """One row per team per match, with both sides' xG.

    Rows without FotMob xG are dropped rather than filled: a match with no xG
    can't be given an expected-points value, and quietly substituting goals
    would make the metric measure the thing it exists to look past.
    """
    q = """
    SELECT f.gameweek, t.name AS team, f.points, f.gf, f.ga,
           f.xg, f.xg_against
    FROM team_match_full f
    JOIN team t ON t.team_id = f.team_id
    WHERE f.season = %s AND f.division = %s
      AND f.xg IS NOT NULL AND f.xg_against IS NOT NULL
    """
    params = [season, division]
    if through is not None:
        q += " AND f.gameweek <= %s"
        params.append(through)
    df = pd.read_sql(q, conn, params=params)
    if df.empty:
        raise SystemExit("No matches with xG. Has import_gw.py been run for this season?")
    print(f"  {len(df)} team-matches, gameweeks {df.gameweek.min()}-{df.gameweek.max()}")
    return df


def build_table(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["xpts"] = [match_xpts(f, a)[3] for f, a in zip(df.xg, df.xg_against)]
    g = df.groupby("team").agg(
        played=("gameweek", "count"), pts=("points", "sum"),
        gf=("gf", "sum"), ga=("ga", "sum"),
        xg=("xg", "sum"), xga=("xg_against", "sum"), xpts=("xpts", "sum"),
    ).reset_index()
    g["diff"] = g.pts - g.xpts
    g["xgd"] = g.xg - g.xga
    return g.sort_values(["pts", "xgd"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------- chart
def chart(tbl: pd.DataFrame, path: str, subtitle: str,
          line1: str = "Is the table lying?", line2: str = "In two places."):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import fontManager

    have = {f.name for f in fontManager.ttflist}
    for fam in ("Poppins", "Segoe UI", "DejaVu Sans"):
        if fam in have:
            plt.rcParams["font.family"] = fam
            break

    n = len(tbl)
    fig = plt.figure(figsize=(10.8, 13.5), dpi=100, facecolor=BG)

    fig.text(.074, .944, "FIELD TILT", color=PAPER, size=18, weight="semibold")
    fig.text(.074, .925, subtitle, color=MUTED, size=10.5, weight="light")
    fig.text(.074, .878, "EXPECTED POINTS", color=ACCENT, size=13, weight="medium")
    fig.text(.074, .840, line1, color=PAPER, size=25, weight="bold")
    fig.text(.074, .806, line2, color=ACCENT, size=25, weight="bold")
    fig.text(.074, .770,
             "What each side's performances were worth, from the chances both teams created.",
             color=MUTED, size=10.5, weight="light")

    ax = fig.add_axes([.074, .095, .852, .645])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(n - 0.5, -1.9)
    ax.axis("off")

    # Where each side would sit if the table were sorted by expected points
    # instead of actual ones.
    xp_rank = [int(v) for v in tbl.xpts.rank(ascending=False, method="min")]

    heads = [(5, "POS", "right"), (14, "xPOS", "right"), (19, "TEAM", "left"),
             (48, "PTS", "right"), (60, "xPTS", "right"), (72, "DIFF", "right"),
             (85, "xG", "right"), (97, "xGA", "right")]
    for x, lab, ha in heads:
        ax.text(x, -1.25, lab, ha=ha, va="center", color=MUTED, size=9, weight="semibold")

    for i, r in tbl.iterrows():
        if i % 2 == 0:
            ax.axhspan(i - .5, i + .5, color="#141A1C", zorder=0)

        # Green and red read as good and bad FOR THE CLUB, so the two coloured
        # columns can disagree — and where they do is the point. Hull are green
        # on points and red on position: excellent results, poor performances.
        xp = xp_rank[i]
        xp_col = RED if xp > i + 1 else (GREEN if xp < i + 1 else MUTED)

        d = r["diff"]
        ax.text(5, i, f"{i+1}", ha="right", va="center", color=MUTED, size=11)
        ax.text(14, i, f"{xp}", ha="right", va="center", color=xp_col,
                size=12, weight="bold")
        ax.text(19, i, SHORT.get(r.team, r.team), ha="left", va="center",
                color=PAPER, size=12.5, weight="medium")
        ax.text(48, i, f"{r.pts:.0f}", ha="right", va="center", color=PAPER,
                size=12.5, weight="bold")
        ax.text(60, i, f"{r.xpts:.1f}", ha="right", va="center", color=MUTED, size=12)
        ax.text(72, i, f"{d:+.1f}", ha="right", va="center",
                color=GREEN if d >= 0 else RED, size=12, weight="bold")
        ax.text(85, i, f"{r.xg:.2f}", ha="right", va="center", color=MUTED, size=11.5)
        ax.text(97, i, f"{r.xga:.2f}", ha="right", va="center", color=MUTED, size=11.5)


    fig.text(.074, .071,
             "xPOS = where a side would sit if the table were sorted by expected "
             "points rather than actual ones.",
             color=MUTED, size=9.5, weight="light")
    fig.add_artist(plt.Line2D([.074, .926], [.055, .055], color=LINE, lw=1))
    fig.text(.074, .024, "@FieldTiltFella", color=PAPER, size=14, weight="medium")
    fig.text(.926, .024, "Green: better than the other column says. Red: worse.",
             color=MUTED, size=10, weight="light", ha="right")

    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    print(f"  chart written to {path}")


# --------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2026-27")
    ap.add_argument("--division", default="E0")
    ap.add_argument("--through", type=int, help="only gameweeks up to this one")
    ap.add_argument("--chart", help="write the tile here")
    ap.add_argument("--line1", default="Is the table lying?")
    ap.add_argument("--line2", default="In two places.")
    ap.add_argument("--out", help="write the table as CSV here")
    args = ap.parse_args()

    with psycopg2.connect(**DB) as conn:
        df = load(conn, args.season, args.division, args.through)

    tbl = build_table(df)
    played = int(tbl.played.max())

    print(f"\n  {'#':>3} {'team':<20}{'pts':>5}{'xPts':>7}{'diff':>7}{'xG':>7}{'xGA':>7}{'xGD':>7}")
    for i, r in tbl.iterrows():
        print(f"  {i+1:>3} {r.team:<20}{r.pts:>5.0f}{r.xpts:>7.1f}{r['diff']:>+7.1f}"
              f"{r.xg:>7.2f}{r.xga:>7.2f}{r.xgd:>+7.2f}")

    over = tbl.loc[tbl["diff"].idxmax()]
    under = tbl.loc[tbl["diff"].idxmin()]
    print(f"\n  most points above the performances: {over.team} {over['diff']:+.1f}")
    print(f"  most below:                         {under.team} {under['diff']:+.1f}")

    if args.out:
        tbl.to_csv(args.out, index=False)
        print(f"  table written to {args.out}")
    if args.chart:
        chart(tbl, args.chart, f"After {played} games", args.line1, args.line2)


if __name__ == "__main__":
    main()
