-- ============================================================================
-- Brings an older PLsight database up to the current schema.
--
-- Safe to run more than once: every ADD COLUMN is IF NOT EXISTS, and the views
-- are definitions rather than data, so dropping and recreating them costs
-- nothing. No table is dropped and no row is touched — your backfill survives.
--
-- DBeaver: open this file, then Alt+X (Execute script). Not Ctrl+Enter.
-- ============================================================================

ALTER TABLE team_match_stats
    ADD COLUMN IF NOT EXISTS possession_pct      NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS touches_opp_box     INT,
    ADD COLUMN IF NOT EXISTS xg                  NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS xg_open_play        NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS xg_set_play         NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS xg_non_penalty      NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS xgot                NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS big_chances         INT,
    ADD COLUMN IF NOT EXISTS big_chances_missed  INT,
    ADD COLUMN IF NOT EXISTS shots               INT,
    ADD COLUMN IF NOT EXISTS shots_on_target     INT,
    ADD COLUMN IF NOT EXISTS shots_off_target    INT,
    ADD COLUMN IF NOT EXISTS blocked_shots       INT,
    ADD COLUMN IF NOT EXISTS woodwork            INT,
    ADD COLUMN IF NOT EXISTS shots_inside_box    INT,
    ADD COLUMN IF NOT EXISTS shots_outside_box   INT,
    ADD COLUMN IF NOT EXISTS passes              INT,
    ADD COLUMN IF NOT EXISTS accurate_passes     INT,
    ADD COLUMN IF NOT EXISTS pass_accuracy_pct   NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS passes_own_half     INT,
    ADD COLUMN IF NOT EXISTS passes_opp_half     INT,
    ADD COLUMN IF NOT EXISTS accurate_long_balls INT,
    ADD COLUMN IF NOT EXISTS long_ball_pct       NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS accurate_crosses    INT,
    ADD COLUMN IF NOT EXISTS cross_pct           NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS throws              INT,
    ADD COLUMN IF NOT EXISTS offsides            INT,
    ADD COLUMN IF NOT EXISTS duels_won           INT,
    ADD COLUMN IF NOT EXISTS ground_duels_won    INT,
    ADD COLUMN IF NOT EXISTS ground_duel_pct     NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS aerial_duels_won    INT,
    ADD COLUMN IF NOT EXISTS aerial_duel_pct     NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS successful_dribbles INT,
    ADD COLUMN IF NOT EXISTS dribble_pct         NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS tackles             INT,
    ADD COLUMN IF NOT EXISTS interceptions       INT,
    ADD COLUMN IF NOT EXISTS blocks              INT,
    ADD COLUMN IF NOT EXISTS clearances          INT,
    ADD COLUMN IF NOT EXISTS keeper_saves        INT,
    ADD COLUMN IF NOT EXISTS attack_left_pct     NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS attack_centre_pct   NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS attack_right_pct    NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS source              TEXT DEFAULT 'fotmob',
    ADD COLUMN IF NOT EXISTS loaded_at           TIMESTAMPTZ DEFAULT now();

ALTER TABLE match
    ADD COLUMN IF NOT EXISTS division   TEXT NOT NULL DEFAULT 'E0',
    ADD COLUMN IF NOT EXISTS gameweek   INT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- The views select columns by name, so they must be rebuilt after any change.
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

-- Check: 47 columns on team_match_stats, and the three zone columns present.
SELECT count(*) AS stat_columns,
       count(*) FILTER (WHERE column_name LIKE 'attack_%') AS zone_columns
FROM information_schema.columns
WHERE table_name = 'team_match_stats';
