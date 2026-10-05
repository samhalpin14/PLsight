"""Dixon-Coles Poisson match prediction.

Fits attack and defence ratings per team plus a home advantage, on the match
history already in the database. Recent matches count for more; teams with few
matches are shrunk toward the league average so a hot start doesn't run away
with the ratings.

Where a match has xG (this season, from FotMob) the model can fit on xG instead
of goals. xG is a better estimate of what a side actually did than the scoreline
is, so it stabilises the ratings faster -- which matters enormously in August.

    python predict.py --fixtures gw02.csv                  # predict
    python predict.py --fixtures gw02.csv --goals-only     # ignore xG
    python predict.py --backtest 2025-26                   # score it on a past season
"""
from __future__ import annotations
import argparse, math, sys
from datetime import date
import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

HALF_LIFE_DAYS = 180.0     # a match this old counts half as much
RIDGE          = 2.0       # L2 penalty on ratings. With 20 teams there are 38 free
                           # parameters and a gameweek supplies 20 observations, so
                           # early in a season the likelihood alone is underdetermined
                           # and the optimiser wanders. The penalty makes it solvable
                           # and pulls unsupported ratings toward zero; as matches
                           # accumulate the data outweighs it and it fades away.
MAX_GOALS      = 10

# ---------------------------------------------------------------- the model
def _tau(h, a, lh, la, rho):
    """Dixon-Coles low-score correction. Independent Poisson gets 0-0, 1-0, 0-1
    and 1-1 wrong; this nudges those four cells."""
    out = np.ones_like(lh, dtype=float)
    m = (h == 0) & (a == 0); out[m] = 1 - lh[m]*la[m]*rho
    m = (h == 0) & (a == 1); out[m] = 1 + lh[m]*rho
    m = (h == 1) & (a == 0); out[m] = 1 + la[m]*rho
    m = (h == 1) & (a == 1); out[m] = 1 - rho
    return np.maximum(out, 1e-10)

def _unpack(p, n):
    att = np.concatenate([p[:n-1], [-p[:n-1].sum()]])   # sum-to-zero constraint
    dfn = np.concatenate([p[n-1:2*n-2], [-p[n-1:2*n-2].sum()]])
    return att, dfn, p[-3], p[-2], p[-1]                # home adv, intercept, rho

def _nll(p, n, hi, ai, hg, ag, w, ridge):
    att, dfn, home, mu, rho = _unpack(p, n)
    lh = np.exp(np.clip(mu + att[hi] - dfn[ai] + home, -8, 3))
    la = np.exp(np.clip(mu + att[ai] - dfn[hi], -8, 3))
    # xG is continuous, so use the Poisson kernel without the factorial term
    ll = (hg*np.log(lh) - lh) + (ag*np.log(la) - la) + np.log(_tau(hg, ag, lh, la, rho))
    val = -(w * ll).sum() + ridge * (np.sum(att**2) + np.sum(dfn**2))
    return val if np.isfinite(val) else 1e10

def fit(df, asof=None, half_life=HALF_LIFE_DAYS, ridge=RIDGE):
    """df needs: match_date, home, away, hg, ag (hg/ag may be xG)."""
    teams = sorted(set(df.home) | set(df.away))
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = df.home.map(idx).to_numpy(); ai = df.away.map(idx).to_numpy()
    hg = df.hg.to_numpy(); ag = df.ag.to_numpy()
    asof = asof or df.match_date.max()
    age = (pd.to_datetime(asof) - pd.to_datetime(df.match_date)).dt.days.to_numpy()
    w = 0.5 ** (age / half_life)

    x0 = np.concatenate([np.zeros(n-1), np.zeros(n-1), [0.25, 0.0, -0.05]])
    res = minimize(_nll, x0, args=(n, hi, ai, hg, ag, w, ridge), method="L-BFGS-B",
                   options=dict(maxiter=2000))
    att, dfn, home, mu, rho = _unpack(res.x, n)
    return dict(teams=teams, idx=idx, att=att, dfn=dfn, n_matches=len(df),
                home=home, mu=mu, rho=float(np.clip(rho, -0.2, 0.2)), converged=res.success)

def scoreline_matrix(m, h, a):
    if h not in m["idx"] or a not in m["idx"]: return None
    i, j = m["idx"][h], m["idx"][a]
    lh = math.exp(m["mu"] + m["att"][i] - m["dfn"][j] + m["home"])
    la = math.exp(m["mu"] + m["att"][j] - m["dfn"][i])
    g = np.arange(MAX_GOALS + 1)
    M = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    H, A = np.meshgrid(g, g, indexing="ij")
    M *= _tau(H.ravel(), A.ravel(), np.full(M.size, lh), np.full(M.size, la), m["rho"]).reshape(M.shape)
    return M / M.sum(), lh, la

def predict(m, h, a):
    out = scoreline_matrix(m, h, a)
    if out is None: return None
    M, lh, la = out
    return dict(home=h, away=a, xg_home=lh, xg_away=la,
                p_home=float(np.tril(M, -1).sum()), p_draw=float(np.trace(M)),
                p_away=float(np.triu(M, 1).sum()),
                p_over25=float(M[np.add.outer(np.arange(MAX_GOALS+1), np.arange(MAX_GOALS+1)) > 2].sum()),
                p_btts=float(M[1:, 1:].sum()),
                top=[(f"{i}-{j}", float(M[i, j])) for i, j in
                     zip(*np.unravel_index(np.argsort(M, axis=None)[::-1][:3], M.shape))])


# ------------------------------------------------------------------ loading
def load_history(conn, use_xg=True, division="E0"):
    """All completed matches. Where FotMob xG exists we use it in place of the
    scoreline: xG describes what a side actually did far better than a 1-0 does,
    so ratings settle in weeks rather than months."""
    q = """
    SELECT m.match_date, th.name AS home, ta.name AS away,
           m.home_goals::float AS hg_goals, m.away_goals::float AS ag_goals,
           sh.xg AS hg_xg, sa.xg AS ag_xg
    FROM match m
    JOIN team th ON th.team_id = m.home_team_id
    JOIN team ta ON ta.team_id = m.away_team_id
    LEFT JOIN team_match_stats sh ON sh.match_id = m.match_id AND sh.team_id = m.home_team_id
    LEFT JOIN team_match_stats sa ON sa.match_id = m.match_id AND sa.team_id = m.away_team_id
    WHERE m.division = %s AND m.home_goals IS NOT NULL
    ORDER BY m.match_date
    """
    df = pd.read_sql(q, conn, params=(division,))
    if use_xg:
        df["hg"] = df.hg_xg.astype(float).fillna(df.hg_goals)
        df["ag"] = df.ag_xg.astype(float).fillna(df.ag_goals)
        n = df.hg_xg.notna().sum()
        print(f"  {len(df)} matches, {n} of them using xG rather than goals")
    else:
        df["hg"], df["ag"] = df.hg_goals, df.ag_goals
        print(f"  {len(df)} matches, goals only")
    return df[["match_date", "home", "away", "hg", "ag"]]

# ---------------------------------------------------------------------------
# Prediction chart. Same palette and layout as the published tiles.
# ---------------------------------------------------------------------------
BG     = "#0F1315"   # background
PAPER  = "#F1EFEA"   # home bar, headline text
MUTED  = "#9BA1A4"   # secondary text
ACCENT = "#E8933A"   # away bar, brand amber
SLATE  = "#4E5559"   # draw bar
LINE   = "#242B2E"   # hairline rules

# Display-only shortening. The database stores canonical names, which are too
# long for the label gutter — "Wolverhampton Wanderers v Nottingham Forest"
# runs off the tile. Nothing downstream sees these.
SHORT = {
    "Manchester United": "Man United",   "Manchester City": "Man City",
    "Newcastle United": "Newcastle",     "Nottingham Forest": "Forest",
    "Wolverhampton Wanderers": "Wolves", "Crystal Palace": "Palace",
    "Brighton & Hove Albion": "Brighton","West Bromwich Albion": "West Brom",
    "Sheffield United": "Sheffield Utd", "Aston Villa": "Villa",
}


def chart(rows, gw, path, note=None):
    """Render the prediction table as a 1080x1350 PNG.

    rows : the list of dicts the fixture loop builds — needs home, away,
           p_home, p_draw, p_away as 0-1 floats.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import fontManager

    have = {f.name for f in fontManager.ttflist}
    for fam in ("Poppins", "Segoe UI", "DejaVu Sans"):
        if fam in have:
            plt.rcParams["font.family"] = fam
            break

    n = len(rows)
    fig = plt.figure(figsize=(10.8, 13.5), dpi=100, facecolor=BG)

    # ---- header ----------------------------------------------------------
    fig.text(0.074, 0.930, "FIELD TILT", color=PAPER, size=19, weight="semibold")
    fig.text(0.074, 0.910, f"Gameweek {gw}", color=MUTED, size=11, weight="light")
    fig.text(0.074, 0.855, f"THE MODEL  ·  GAMEWEEK {gw}", color=ACCENT, size=14, weight="medium")
    fig.text(0.074, 0.815, "Published before kick-off.", color=PAPER, size=27, weight="bold")
    fig.text(0.074, 0.784, "Marked afterwards.", color=ACCENT, size=27, weight="bold")
    if note:
        fig.text(0.074, 0.745, note, color=MUTED, size=12, weight="light")

    # ---- bars ------------------------------------------------------------
    # Stacked by hand with `left=` — matplotlib has no stacked-barh helper.
    ax = fig.add_axes([0.435, 0.115, 0.49, 0.565])
    ax.set_facecolor(BG)
    y = range(n)

    ph  = [r["p_home"] * 100 for r in rows]
    pd_ = [r["p_draw"] * 100 for r in rows]
    pa  = [r["p_away"] * 100 for r in rows]

    ax.barh(y, ph,  color=PAPER,  height=0.5)
    ax.barh(y, pd_, color=SLATE,  height=0.5, left=ph)
    ax.barh(y, pa,  color=ACCENT, height=0.5,
            left=[a + b for a, b in zip(ph, pd_)])

    # percentage labels, only where the segment is wide enough to hold one
    for i, (h, d, a) in enumerate(zip(ph, pd_, pa)):
        for val, start, fg in ((h, 0, BG), (d, h, PAPER), (a, h + d, BG)):
            if val >= 12:
                ax.text(start + val / 2, i, f"{val:.0f}%", ha="center",
                        va="center", color=fg, size=13, weight="bold")

    ax.set_xlim(0, 100)
    ax.set_ylim(n - 0.5, -0.5)          # first fixture at the top
    ax.axis("off")
    MIN_INSIDE = 13     # below this a segment can't hold its own label

    for i, (h, d, a) in enumerate(zip(ph, pd_, pa)):
        # home
        if h >= MIN_INSIDE:
            ax.text(h/2, i, f"{h:.0f}%", ha="center", va="center",
                    color=BG, size=13, weight="bold")
        else:
            ax.text(-1.5, i, f"{h:.0f}%", ha="right", va="center",
                    color=PAPER, size=12, weight="bold")
        # draw
        if d >= MIN_INSIDE:
            ax.text(h + d/2, i, f"{d:.0f}%", ha="center", va="center",
                    color=PAPER, size=13, weight="bold")
        else:
            ax.text(h + d/2, i + .42, f"{d:.0f}%", ha="center", va="bottom",
                    color=SLATE, size=11, weight="bold")
        # away — narrow ones sit just outside the right end of the bar
        if a >= MIN_INSIDE:
            ax.text(h + d + a/2, i, f"{a:.0f}%", ha="center", va="center",
                    color=BG, size=13, weight="bold")
        else:
            ax.text(101.5, i, f"{a:.0f}%", ha="left", va="center",
                    color=ACCENT, size=12, weight="bold")
    # fixture names, right-aligned into the gutter left of the bars
    for i, r in enumerate(rows):
        label = f"{SHORT.get(r['home'], r['home'])}  v  {SHORT.get(r['away'], r['away'])}"
        ax.text(-3, i, label, ha="right", va="center",
                color=PAPER, size=14, weight="medium")

    # ---- column heads ----------------------------------------------------
    ax.text(0,   -0.85, "HOME", ha="left",   va="center", color=PAPER,  size=11, weight="semibold")
    ax.text(50,  -0.85, "DRAW", ha="center", va="center", color=MUTED,  size=11, weight="semibold")
    ax.text(100, -0.85, "AWAY", ha="right",  va="center", color=ACCENT, size=11, weight="semibold")

    # ---- footer ----------------------------------------------------------
    fig.add_artist(plt.Line2D([0.074, 0.926], [0.088, 0.088], color=LINE, lw=1))
    fig.text(0.074, 0.045, "@FieldTiltFella", color=PAPER, size=15, weight="medium")
    fig.text(0.926, 0.045, "Scored next week", color=MUTED, size=13, weight="light", ha="right")

    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    print(f"  chart written to {path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="CSV with columns: home_team,away_team")
    ap.add_argument("--force-chart", action="store_true",
                    help="render the tile even if some fixtures were skipped")
    ap.add_argument("--goals-only", action="store_true", help="ignore xG, fit on scorelines")
    ap.add_argument("--half-life", type=float, default=HALF_LIFE_DAYS)
    ap.add_argument("--out", help="write predictions here so they can be scored later")
    ap.add_argument("--chart", help="write a chart of the predictions here")
    ap.add_argument("--gw", type=int, default=0, help="gameweek number for the header")
    args = ap.parse_args()

    import psycopg2
    from ingest import DB
    with psycopg2.connect(**DB) as conn:
        hist = load_history(conn, use_xg=not args.goals_only)

    m = fit(hist, half_life=args.half_life)
    if not m["converged"]:
        print("  ! optimiser did not converge — treat the ratings with suspicion")

    # A higher attack rating means more goals scored; a higher defence rating
    # means fewer conceded (it enters the model with a minus sign). So overall
    # strength is att + dfn, not att - dfn.
    strength = m["att"] + m["dfn"]

    # Only show sides in the current fixture list plus anyone who has played
    # recently — the fit uses twelve seasons, so the raw list is full of clubs
    # who left the division years ago and sit at zero.
    recent = set()
    if args.fixtures:
        _fx = pd.read_csv(args.fixtures)
        from teams import canonical as _c
        recent = {_c(str(x).strip(), "footballdata")
                  for x in list(_fx.home_team) + list(_fx.away_team)}
    show = [i for i in np.argsort(-strength) if not recent or m["teams"][i] in recent]

    print(f"\n  home advantage {m['home']:+.3f}   rho {m['rho']:+.3f}")
    print(f"  fitted on {m['n_matches']} matches")
    print(f"\n  {'team':24}{'attack':>9}{'defence':>9}{'strength':>10}")
    for i in show:
        print(f"  {m['teams'][i]:24}{m['att'][i]:+9.3f}{m['dfn'][i]:+9.3f}{strength[i]:+10.3f}")

    if not args.fixtures: return
    fx = pd.read_csv(args.fixtures)
    # Fixture files get typed by hand, so run the names through the same alias
    # map the importer uses. Without this "Man United" fails to match the
    # canonical "Manchester United" and the fixture is silently skipped.
    from teams import canonical
    fx["home_team"] = fx.home_team.astype(str).str.strip().map(lambda x: canonical(x, "footballdata"))
    fx["away_team"] = fx.away_team.astype(str).str.strip().map(lambda x: canonical(x, "footballdata"))
    rows = []
    print(f"\n  {'fixture':44}{'H':>7}{'D':>7}{'A':>7}{'  xG':>12}   likeliest")
    for _, f in fx.iterrows():
        p = predict(m, f.home_team, f.away_team)
        if p is None:
            missing = [t for t in (f.home_team, f.away_team) if t not in m["idx"]]
            print(f"  {f.home_team} v {f.away_team}: SKIPPED — not in the fitted history: "
                  f"{', '.join(missing)}")
            print(f"      the model only knows teams with a completed E0 match. Check the "
                  f"spelling against `SELECT name FROM team ORDER BY name;`")
            continue
        top = ", ".join(f"{s} {q:.0%}" for s, q in p["top"])
        print(f"  {f.home_team+' v '+f.away_team:44}{p['p_home']:6.1%}{p['p_draw']:7.1%}"
              f"{p['p_away']:7.1%}   {p['xg_home']:.2f}-{p['xg_away']:.2f}   {top}")
        rows.append(dict(home=f.home_team, away=f.away_team, **{k: p[k] for k in
                    ("p_home", "p_draw", "p_away", "p_over25", "p_btts", "xg_home", "xg_away")}))
    # A short fixture list is the failure that gets published. GW4 went out with
    # nine fixtures because two names did not match and the per-line SKIPPED
    # notices scrolled past unread.
    if len(rows) != len(fx):
        print(f"\n  WARNING: {len(fx) - len(rows)} of {len(fx)} fixtures were skipped. "
              f"Only {len(rows)} predictions exist.")
        print("  Fix the spellings flagged above and re-run before publishing.")
        if args.chart and not args.force_chart:
            sys.exit("  Refusing to render a tile from an incomplete fixture list. "
                     "Pass --force-chart if you really want it.")

    if args.out:
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print(f"\n  written to {args.out} — do not edit it after kick-off")

    if args.chart:
        chart(rows, args.gw, args.chart,
              note=f"Dixon-Coles fitted on {m['n_matches']:,} matches, "
                   "using xG in place of scorelines where it exists.")

if __name__ == "__main__":
    main()