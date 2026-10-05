-- ============================================================================
-- PLsight — complete database setup. Run this ONCE, on an empty database.
-- Replaces schema.sql + migrate_01 + migrate_02 (delete those three).
--
-- DBeaver: open this file, then Alt+X (Execute script). Not Ctrl+Enter.
-- ============================================================================

-- ---------------------------------------------------------------- tables

CREATE TABLE IF NOT EXISTS team (
    team_id   SERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE            -- canonical, e.g. 'Manchester City'
);

-- One row per fixture. Source: football-data.co.uk (automated).
CREATE TABLE IF NOT EXISTS match (
    match_id       TEXT PRIMARY KEY,          -- 'E0_2026-08-21_ARSENA_COVENT'
    season         TEXT NOT NULL,             -- '2026-27'
    division       TEXT NOT NULL DEFAULT 'E0',-- E0 = Premier League, E1 = Championship
    match_date     DATE NOT NULL,
    gameweek       INT,
    home_team_id   INT NOT NULL REFERENCES team(team_id),
    away_team_id   INT NOT NULL REFERENCES team(team_id),
    home_goals     INT, away_goals   INT,
    home_shots     INT, away_shots   INT,
    home_sot       INT, away_sot     INT,     -- shots on target
    home_corners   INT, away_corners INT,
    home_fouls     INT, away_fouls   INT,
    home_yellow    INT, away_yellow  INT,
    home_red       INT, away_red     INT,
    updated_at     TIMESTAMPTZ DEFAULT now()
);

-- One row per team per match. Source: FotMob, transcribed by hand.
CREATE TABLE IF NOT EXISTS team_match_stats (
    match_id            TEXT NOT NULL REFERENCES match(match_id) ON DELETE CASCADE,
    team_id             INT  NOT NULL REFERENCES team(team_id),
    is_home             BOOLEAN NOT NULL,

    possession_pct      NUMERIC(5,2),
    touches_opp_box     INT,

    xg                  NUMERIC(6,3),
    xg_open_play        NUMERIC(6,3),
    xg_set_play         NUMERIC(6,3),
    xg_non_penalty      NUMERIC(6,3),
    xgot                NUMERIC(6,3),
    big_chances         INT,
    big_chances_missed  INT,

    shots               INT,
    shots_on_target     INT,
    shots_off_target    INT,
    blocked_shots       INT,
    woodwork            INT,
    shots_inside_box    INT,
    shots_outside_box   INT,

    passes              INT,
    accurate_passes     INT,
    pass_accuracy_pct   NUMERIC(5,2),
    passes_own_half     INT,
    passes_opp_half     INT,
    accurate_long_balls INT,
    long_ball_pct       NUMERIC(5,2),
    accurate_crosses    INT,
    cross_pct           NUMERIC(5,2),
    throws              INT,
    offsides            INT,

    duels_won           INT,
    ground_duels_won    INT,
    ground_duel_pct     NUMERIC(5,2),
    aerial_duels_won    INT,
    aerial_duel_pct     NUMERIC(5,2),
    successful_dribbles INT,
    dribble_pct         NUMERIC(5,2),

    tackles             INT,
    interceptions       INT,
    blocks              INT,
    clearances          INT,
    keeper_saves        INT,

    attack_left_pct     NUMERIC(5,2),
    attack_centre_pct   NUMERIC(5,2),
    attack_right_pct    NUMERIC(5,2),

    source              TEXT DEFAULT 'fotmob',
    loaded_at           TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (match_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_match_div_season ON match(division, season, match_date);
CREATE INDEX IF NOT EXISTS idx_tms_team         ON team_match_stats(team_id);

-- ----------------------------------------------------------------- views

-- Turns one row per fixture into two rows per fixture, one from each team's
-- point of view. Everything downstream reads this instead of `match`, so no
-- query ever needs to work out whether a club was home or away.
DROP VIEW IF EXISTS team_match_full;
DROP VIEW IF EXISTS team_match;

CREATE VIEW team_match AS
SELECT m.match_id, m.season, m.division, m.gameweek, m.match_date,
       m.home_team_id AS team_id, m.away_team_id AS opponent_id, TRUE AS is_home,
       m.home_goals AS gf, m.away_goals AS ga,
       m.home_shots AS shots, m.away_shots AS shots_against,
       m.home_sot   AS sot,   m.away_sot   AS sot_against,
       m.home_corners AS corners, m.away_corners AS corners_against,
       CASE WHEN m.home_goals > m.away_goals THEN 3
            WHEN m.home_goals = m.away_goals THEN 1 ELSE 0 END AS points
FROM match m WHERE m.home_goals IS NOT NULL
UNION ALL
SELECT m.match_id, m.season, m.division, m.gameweek, m.match_date,
       m.away_team_id, m.home_team_id, FALSE,
       m.away_goals, m.home_goals,
       m.away_shots, m.home_shots,
       m.away_sot,   m.home_sot,
       m.away_corners, m.home_corners,
       CASE WHEN m.away_goals > m.home_goals THEN 3
            WHEN m.away_goals = m.home_goals THEN 1 ELSE 0 END
FROM match m WHERE m.home_goals IS NOT NULL;

-- Results joined to the FotMob detail, plus the opponent's figures so that
-- xg_against and friends are available without writing a self-join every time.
CREATE VIEW team_match_full AS
SELECT tm.match_id, tm.season, tm.division, tm.gameweek, tm.match_date,
       tm.team_id, tm.opponent_id, tm.is_home,
       tm.gf, tm.ga, tm.points,
       tm.shots AS fd_shots, tm.sot AS fd_sot, tm.corners,
       s.possession_pct, s.touches_opp_box,
       s.xg, s.xg_open_play, s.xg_set_play, s.xg_non_penalty, s.xgot,
       s.big_chances, s.big_chances_missed,
       s.shots, s.shots_on_target, s.shots_off_target, s.blocked_shots,
       s.woodwork, s.shots_inside_box, s.shots_outside_box,
       s.passes, s.accurate_passes, s.pass_accuracy_pct,
       s.passes_own_half, s.passes_opp_half,
       s.accurate_long_balls, s.long_ball_pct, s.accurate_crosses, s.cross_pct,
       s.throws, s.offsides,
       s.duels_won, s.ground_duels_won, s.ground_duel_pct,
       s.aerial_duels_won, s.aerial_duel_pct, s.successful_dribbles, s.dribble_pct,
       s.tackles, s.interceptions, s.blocks, s.clearances, s.keeper_saves,
       s.attack_left_pct, s.attack_centre_pct, s.attack_right_pct,
       o.xg              AS xg_against,
       o.xgot            AS xgot_against,
       o.shots           AS shots_against,
       o.possession_pct  AS possession_against,
       o.accurate_passes AS accurate_passes_against,
       o.passes_opp_half AS passes_opp_half_against,
       o.touches_opp_box AS touches_opp_box_against
FROM team_match tm
LEFT JOIN team_match_stats s ON s.match_id = tm.match_id AND s.team_id = tm.team_id
LEFT JOIN team_match_stats o ON o.match_id = tm.match_id AND o.team_id = tm.opponent_id;

-- ---------------------------------------------------------------- check
-- Should return 3 tables and 2 views.
SELECT table_name, table_type FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_type, table_name;
