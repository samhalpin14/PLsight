-- Brings an existing PLsight database up to date. Safe to run more than once,
-- and safe whether or not you ran the earlier zones migration.
--   1. adds the attacking-zone columns if they're missing
--   2. drops the physical columns (FotMob no longer publishes them)
--   3. rebuilds team_match_full to match

ALTER TABLE team_match_stats
    ADD COLUMN IF NOT EXISTS attack_left_pct   NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS attack_centre_pct NUMERIC(5,2),
    ADD COLUMN IF NOT EXISTS attack_right_pct  NUMERIC(5,2);

DROP VIEW IF EXISTS team_match_full;

ALTER TABLE team_match_stats
    DROP COLUMN IF EXISTS distance_km,
    DROP COLUMN IF EXISTS sprint_distance_km,
    DROP COLUMN IF EXISTS sprints;

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
