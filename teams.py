"""Canonical team names and the aliases each source uses.

Name mismatches between sources are the single most common cause of silently
wrong football data. Everything goes through here.

When a season brings promoted clubs in, add them to CURRENT_SEASON and to the
alias maps, then run `python teams.py` to self-check.

Two things worth knowing before you change anything in this file:

1. `canonical()` falls back to the raw name when it recognises nothing. That is
   deliberate and the pipeline depends on it — `ingest.py` builds the `team`
   table from whatever football-data publishes, so a club that is not listed
   here can still resolve if football-data already created its row. But it is
   NOT loud. A name that neither resolves nor exists in `team` makes
   `import_gw.py` print one line and skip the row, so a gameweek can import
   nineteen teams and look fine. Use `unresolved()` / `check_frame()` below to
   catch that before loading.

2. The canonical spelling of United here is "Manchester United", because that
   is what the database holds. The gameweek CSVs say "Man United" and the alias
   maps convert it. Do not "fix" the CSVs to match — the mapping is the fix.
"""

# --------------------------------------------------------------------------
# Canonical spellings
# --------------------------------------------------------------------------

# 2026-27 Premier League
CURRENT_SEASON = [
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton",
    "Chelsea", "Coventry", "Crystal Palace", "Everton", "Fulham",
    "Hull", "Ipswich", "Leeds", "Liverpool", "Manchester City",
    "Manchester United", "Newcastle United", "Nottingham Forest",
    "Sunderland", "Tottenham",
]

# Clubs from earlier seasons, kept so the football-data backfill resolves.
HISTORICAL = [
    "Burnley", "Cardiff", "Huddersfield", "Leicester", "Luton",
    "Middlesbrough", "Norwich", "Sheffield United", "Southampton",
    "Stoke", "Swansea", "Watford", "West Bromwich Albion", "West Ham",
    "Wolverhampton Wanderers",
]

CANONICAL = CURRENT_SEASON + HISTORICAL

# football-data.co.uk spellings -> canonical
FOOTBALLDATA_ALIASES = {
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Tottenham": "Tottenham",
    "Brighton": "Brighton",
    "West Brom": "West Bromwich Albion",
    "Sheffield United": "Sheffield United",
    "Leeds": "Leeds",
    "Leicester": "Leicester",
    "Ipswich": "Ipswich",
    "Luton": "Luton",
    "Southampton": "Southampton",
    "Bournemouth": "Bournemouth",
    "Coventry": "Coventry",
    "Hull": "Hull",
}

# Understat spellings -> canonical
UNDERSTAT_ALIASES = {
    "Manchester City": "Manchester City",
    "Manchester United": "Manchester United",
    "Newcastle United": "Newcastle United",
    "Nottingham Forest": "Nottingham Forest",
    "Wolverhampton Wanderers": "Wolverhampton Wanderers",
    "Tottenham": "Tottenham",
    "Brighton": "Brighton",
    "West Bromwich Albion": "West Bromwich Albion",
    "Sheffield United": "Sheffield United",
    "Leeds": "Leeds",
    "Leicester": "Leicester",
    "Ipswich": "Ipswich",
    "Luton": "Luton",
    "Southampton": "Southampton",
    "Bournemouth": "Bournemouth",
    "Coventry": "Coventry",
    "Hull": "Hull",
}

# FotMob, as the hand-captured sheets spell it. The exports are inconsistent
# about capitals and short forms, so this table is matched case-insensitively.
FOTMOB_ALIASES = {
    "man united": "Manchester United",
    "man utd": "Manchester United",
    "manchester utd": "Manchester United",
    "man city": "Manchester City",
    "spurs": "Tottenham",
    "tottenham hotspur": "Tottenham",
    "villa": "Aston Villa",
    "forest": "Nottingham Forest",
    "nott'm forest": "Nottingham Forest",
    "palace": "Crystal Palace",
    "newcastle": "Newcastle United",
    "wolves": "Wolverhampton Wanderers",
    "west brom": "West Bromwich Albion",
    "brighton & hove albion": "Brighton",
    "brighton and hove albion": "Brighton",
    "sunderland afc": "Sunderland",
    "leeds united": "Leeds",
    "hull city": "Hull",
    "coventry city": "Coventry",
    "ipswich town": "Ipswich",
    "leicester city": "Leicester",
    "afc bournemouth": "Bournemouth",
    "west ham united": "West Ham",
}

_SOURCES = {
    "footballdata": FOOTBALLDATA_ALIASES,
    "understat": UNDERSTAT_ALIASES,
    "fotmob": FOTMOB_ALIASES,
}


def canonical(name: str, source: str) -> str:
    """Map a source-specific team name to the canonical one.

    Falls back to the raw name so an unknown club surfaces in the data rather
    than being silently renamed. Unknown sources fall back to the Understat
    table, matching the previous behaviour.

    FotMob matching is case-insensitive and tolerates stray whitespace, because
    the stat exports are not consistent about either.
    """
    table = _SOURCES.get(source, UNDERSTAT_ALIASES)
    if name in table:
        return table[name]
    key = " ".join(str(name).split())
    if key in table:
        return table[key]
    lowered = key.lower()
    for alias, target in FOTMOB_ALIASES.items():
        if lowered == alias:
            return target
    for club in CANONICAL:
        if lowered == club.lower():
            return club
    return name


def unresolved(names, source="fotmob"):
    """Names that would not land on a canonical club. Sorted, de-duplicated.

    An empty list means every name maps onto something in CANONICAL. A
    non-empty one means `import_gw.py` will depend on the `team` table already
    holding that exact spelling, and will skip the row if it doesn't.
    """
    out = set()
    for n in names:
        if n is None:
            out.add("<missing>")
        elif canonical(str(n), source) not in CANONICAL:
            out.add(str(n))
    return sorted(out)


def check_frame(df, source="fotmob",
                columns=("team", "home_team", "away_team")):
    """Validate a gameweek frame before import. Returns a list of problems.

    Empty list means clean. Checks names resolve, 20 rows, 20 distinct teams,
    10 fixtures of two rows each, and that every row's team is one of its own
    fixture's two sides.
    """
    problems = []
    cols = [c for c in columns if c in df.columns]

    for col in cols:
        bad = unresolved(df[col], source)
        if bad:
            problems.append(f"{col}: will not resolve to a canonical club -> {bad}")

    work = df.copy()
    for col in cols:
        work[col] = work[col].map(lambda v: canonical(str(v), source))

    if "team" in work.columns:
        if len(work) != 20:
            problems.append(f"expected 20 rows, found {len(work)}")
        if work["team"].nunique() != 20:
            problems.append(f"expected 20 distinct teams, found {work['team'].nunique()}")
        counts = work["team"].value_counts()
        dupes = list(counts[counts != 1].index)
        if dupes:
            problems.append(f"teams appearing more than once: {dupes}")

    if {"home_team", "away_team"} <= set(work.columns):
        fixtures = work.groupby(["home_team", "away_team"]).size()
        if len(fixtures) != 10:
            problems.append(f"expected 10 fixtures, found {len(fixtures)}")
        odd = fixtures[fixtures != 2]
        if len(odd):
            problems.append(f"fixtures without exactly 2 rows: {list(odd.index)}")
        if "team" in work.columns:
            stray = work[(work["team"] != work["home_team"])
                         & (work["team"] != work["away_team"])]
            if len(stray):
                problems.append("rows whose team is not in their own fixture: "
                                f"{stray['team'].tolist()}")

    return problems


# --------------------------------------------------------------------------

if __name__ == "__main__":
    assert len(CURRENT_SEASON) == 20, f"{len(CURRENT_SEASON)} clubs, expected 20"
    assert len(set(CANONICAL)) == len(CANONICAL), "duplicate entry in CANONICAL"

    for label, table in _SOURCES.items():
        for alias, target in table.items():
            assert target in CANONICAL, \
                f"{label}: {alias!r} points at {target!r}, which is not canonical"

    for probe, source, want in [
        ("Man United", "footballdata", "Manchester United"),
        ("Man United", "fotmob", "Manchester United"),
        ("Manchester United", "fotmob", "Manchester United"),
        ("  MAN UTD ", "fotmob", "Manchester United"),
        ("Spurs", "fotmob", "Tottenham"),
        ("Villa", "fotmob", "Aston Villa"),
        ("forest", "fotmob", "Nottingham Forest"),
        ("Palace", "fotmob", "Crystal Palace"),
        ("Man city", "fotmob", "Manchester City"),
        ("liverpool", "fotmob", "Liverpool"),
        ("Coventry", "footballdata", "Coventry"),
        ("Nott'm Forest", "footballdata", "Nottingham Forest"),
    ]:
        got = canonical(probe, source)
        assert got == want, f"canonical({probe!r},{source!r}) = {got!r}, wanted {want!r}"

    assert unresolved(["Spurs", "Villa", "Man United"]) == []
    assert unresolved(["Real Madrid"]) == ["Real Madrid"]

    print(f"teams.py OK — {len(CURRENT_SEASON)} current clubs, "
          f"{len(CANONICAL)} canonical, {len(_SOURCES)} sources")
