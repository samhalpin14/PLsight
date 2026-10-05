"""Ingest English league match data into Postgres.

Source
------
football-data.co.uk — results, shots, shots on target, corners, fouls, cards.
Free to use, direct CSV, one file per division per season.

This is the results spine. xG and the detailed stats come from FotMob via
import_gw.py — see the README.

Idempotent — rerun it every Monday and it upserts.

Usage
-----
    python ingest.py --seasons 2025-26                      # weekly refresh
    python ingest.py --seasons 2024-25 2025-26 --check-names
    python ingest.py --seasons 2024-25 2025-26 --division E1   # Championship
"""
from __future__ import annotations

import argparse
import io
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2
import requests

from teams import canonical

# ---- database connection ---------------------------------------------------
# Credentials live in .env next to this file, which .gitignore excludes.
# .env.example shows the format. Nothing secret belongs in this file.
def _load_env_file() -> None:
    envfile = Path(__file__).resolve().parent / ".env"
    if not envfile.exists():
        return
    for line in envfile.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _db_config() -> dict:
    _load_env_file()
    password = os.environ.get("PLSIGHT_DB_PASSWORD")
    if not password:
        raise SystemExit(
            "No database password found.\n"
            "Create a file called .env next to ingest.py containing:\n"
            "    PLSIGHT_DB_PASSWORD=yourpassword\n"
            "See .env.example. .gitignore already excludes .env."
        )
    return dict(
        dbname=os.environ.get("PLSIGHT_DB_NAME", "PLsight"),
        user=os.environ.get("PLSIGHT_DB_USER", "postgres"),
        password=password,
        host=os.environ.get("PLSIGHT_DB_HOST", "localhost"),
        port=int(os.environ.get("PLSIGHT_DB_PORT", "5432")),
    )


DB = _db_config()
# ----------------------------------------------------------------------------

FD_URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; plsight/1.0)"}
PAUSE_BETWEEN_SEASONS = 3.0     # football-data rate-limits; be polite
MAX_ATTEMPTS = 4


def season_to_code(season: str) -> str:
    """'2025-26' -> '2526'"""
    start, end = season.split("-")
    return start[2:] + end


def make_match_id(date, home: str, away: str, division: str) -> str:
    """Full club name, letters only. Truncating to 6 characters collided
    Manchester City with Manchester United, and the two Sheffield clubs."""
    slug = lambda s: re.sub(r"[^A-Za-z]", "", s).upper()
    return f"{division}_{date:%Y-%m-%d}_{slug(home)}_{slug(away)}"


def parse_date(raw):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(str(raw), fmt).date()
        except ValueError:
            continue
    return None


def upsert_team(cur, name: str) -> int:
    cur.execute("INSERT INTO team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    cur.execute("SELECT team_id FROM team WHERE name = %s", (name,))
    return cur.fetchone()[0]


def fetch_csv(season: str, division: str) -> str | None:
    """Fetch with backoff. football-data returns a soft error page under load,
    so check for a real column name rather than trusting the status code."""
    url = FD_URL.format(code=season_to_code(season), div=division)
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = requests.get(url, headers=UA, timeout=30)
        except requests.RequestException as e:
            print(f"  … {season}: {type(e).__name__}, retrying")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200 and "HomeTeam" in r.text[:2000]:
            return r.text
        wait = 5 * (attempt + 1)
        print(f"  … {season} {division}: HTTP {r.status_code}, retrying in {wait}s")
        time.sleep(wait)
    print(f"  ! {season} {division}: unavailable after {MAX_ATTEMPTS} tries — skipping")
    return None


def load_season(cur, season: str, division: str, check_names: bool = False) -> int:
    text = fetch_csv(season, division)
    if text is None:
        return 0

    df = pd.read_csv(io.StringIO(text))
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "Date"])

    if check_names:
        raw = set(df["HomeTeam"]) | set(df["AwayTeam"])
        unmapped = []
        print(f"  raw names ({season} {division}):")
        for n in sorted(raw):
            c = canonical(n, "footballdata")
            flag = "" if c != n else "   <- maps to itself"
            print(f"    {n!r:28} -> {c!r}{flag}")
            if c == n:
                unmapped.append(n)
        print(f"  {len(raw)} clubs, {len(unmapped)} mapping to themselves "
              f"(fine if that IS the canonical name)")
        return 0

    def num(row, col):
        v = row.get(col)
        return None if pd.isna(v) else int(v)

    rows = 0
    for _, row in df.iterrows():
        date = parse_date(row["Date"])
        if date is None:
            continue
        home = canonical(str(row["HomeTeam"]).strip(), "footballdata")
        away = canonical(str(row["AwayTeam"]).strip(), "footballdata")
        hid, aid = upsert_team(cur, home), upsert_team(cur, away)
        mid = make_match_id(date, home, away, division)

        cur.execute("""
            INSERT INTO match (match_id, season, division, match_date,
                               home_team_id, away_team_id,
                               home_goals, away_goals, home_shots, away_shots,
                               home_sot, away_sot, home_corners, away_corners,
                               home_fouls, away_fouls, home_yellow, away_yellow,
                               home_red, away_red)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (match_id) DO UPDATE SET
                home_goals=EXCLUDED.home_goals,   away_goals=EXCLUDED.away_goals,
                home_shots=EXCLUDED.home_shots,   away_shots=EXCLUDED.away_shots,
                home_sot=EXCLUDED.home_sot,       away_sot=EXCLUDED.away_sot,
                home_corners=EXCLUDED.home_corners, away_corners=EXCLUDED.away_corners,
                home_fouls=EXCLUDED.home_fouls,   away_fouls=EXCLUDED.away_fouls,
                home_yellow=EXCLUDED.home_yellow, away_yellow=EXCLUDED.away_yellow,
                home_red=EXCLUDED.home_red,       away_red=EXCLUDED.away_red,
                updated_at=now()
        """, (mid, season, division, date, hid, aid,
              num(row, "FTHG"), num(row, "FTAG"), num(row, "HS"), num(row, "AS"),
              num(row, "HST"), num(row, "AST"), num(row, "HC"), num(row, "AC"),
              num(row, "HF"), num(row, "AF"), num(row, "HY"), num(row, "AY"),
              num(row, "HR"), num(row, "AR")))
        rows += 1
    return rows


def refresh_gameweeks(cur, season: str, division: str) -> None:
    """Number each team's matches in date order; the fixture takes the lower
    number. Postponements make this drift — load a real fixture list if it
    ever matters more than it does now."""
    cur.execute("""
        WITH ordered AS (
            SELECT match_id, team_id,
                   ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY match_date, match_id) AS n
            FROM team_match WHERE season = %s AND division = %s
        )
        UPDATE match m SET gameweek = sub.gw
        FROM (SELECT match_id, MIN(n) AS gw FROM ordered GROUP BY match_id) sub
        WHERE m.match_id = sub.match_id
    """, (season, division))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", required=True, help="e.g. 2024-25 2025-26")
    ap.add_argument("--division", default="E0",
                    help="E0 = Premier League (default), E1 = Championship")
    ap.add_argument("--check-names", action="store_true",
                    help="print name mapping and exit without writing")
    args = ap.parse_args()

    with psycopg2.connect(**DB) as conn:
        with conn.cursor() as cur:
            for i, season in enumerate(args.seasons):
                print(f"[{season} {args.division}]")
                n = load_season(cur, season, args.division, check_names=args.check_names)
                if not args.check_names:
                    print(f"  loaded {n} matches")
                    refresh_gameweeks(cur, season, args.division)
                    conn.commit()
                if i < len(args.seasons) - 1:
                    time.sleep(PAUSE_BETWEEN_SEASONS)
    print("done")


if __name__ == "__main__":
    main()
