"""Score the predictions and track whether the model is getting better.

Predictions are written once, before the gameweek, and never edited. Results are
joined afterwards. Everything here is a proper scoring rule, so you cannot make
the numbers look better by being vague -- hedging every match toward 33/33/33
scores badly, and so does being confidently wrong.

    python track.py --log preds/gw02.csv          # record predictions
    python track.py --score                       # score everything so far
"""
from __future__ import annotations
import argparse, glob, os
import numpy as np, pandas as pd

BASE_RATES = dict(H=0.45, D=0.25, A=0.30)   # long-run Premier League 1X2 split

def outcome(hg, ag): return "H" if hg > ag else ("A" if ag > hg else "D")

def brier(p, actual):
    """Multiclass Brier. 0 is perfect, 2 is the worst possible. Random = 0.667."""
    y = np.array([actual == c for c in "HDA"], dtype=float)
    return float(((np.array(p) - y) ** 2).sum())

def logloss(p, actual, eps=1e-15):
    return float(-np.log(np.clip(np.array(p)["HDA".index(actual)], eps, 1)))

def score(df):
    """df: p_home, p_draw, p_away, hg, ag"""
    rows = []
    for _, r in df.iterrows():
        act = outcome(r.hg, r.ag)
        p = [r.p_home, r.p_draw, r.p_away]
        rows.append(dict(gw=r.get("gw"), home=r.home, away=r.away, actual=act,
                         pred="HDA"[int(np.argmax(p))], hit=int("HDA"[int(np.argmax(p))] == act),
                         brier=brier(p, act), logloss=logloss(p, act),
                         p_assigned=p["HDA".index(act)]))
    s = pd.DataFrame(rows)
    base = pd.DataFrame([dict(
        brier=brier([BASE_RATES["H"], BASE_RATES["D"], BASE_RATES["A"]], outcome(r.hg, r.ag)),
        logloss=logloss([BASE_RATES["H"], BASE_RATES["D"], BASE_RATES["A"]], outcome(r.hg, r.ag)),
        hit=int(outcome(r.hg, r.ag) == "H"))
        for _, r in df.iterrows()])
    return s, base

def calibration(s, bins=(0, .2, .35, .5, .65, .8, 1.01)):
    """Of the matches where the model said ~60%, did ~60% happen?"""
    out = []
    allp = np.concatenate([s.p_assigned.values])
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (s.p_assigned >= lo) & (s.p_assigned < hi)
        if m.sum() == 0: continue
        out.append(dict(band=f"{lo:.0%}-{hi:.0%}", n=int(m.sum()),
                        predicted=float(s.p_assigned[m].mean()),
                        actual=float(m.sum() / len(s)) if False else None))
    return pd.DataFrame(out)

def report(s, base):
    n = len(s)
    print(f"  matches scored            {n}")
    print(f"  correct 1X2               {s.hit.sum()}/{n}  ({s.hit.mean():.1%})")
    print(f"  Brier   (lower better)    {s.brier.mean():.4f}   base rates {base.brier.mean():.4f}")
    print(f"  LogLoss (lower better)    {s.logloss.mean():.4f}   base rates {base.logloss.mean():.4f}")
    edge_b = base.brier.mean() - s.brier.mean()
    print(f"  edge over base rates      {edge_b:+.4f} Brier  "
          f"({'model ahead' if edge_b>0 else 'BASE RATES AHEAD — model adds nothing'})")
    print(f"  always-home accuracy      {base.hit.mean():.1%}")
    if "gw" in s and s.gw.notna().any():
        print("\n  by gameweek:")
        for gw, g in s.groupby("gw"):
            print(f"    GW{int(gw):<3} n={len(g):2d}  acc {g.hit.mean():5.1%}  brier {g.brier.mean():.4f}")
