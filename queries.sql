-- ============================================================================
-- PLsight — the recurring queries.
--
-- DBeaver: run one block at a time with Ctrl+Enter. It prompts for :season
--          (type 2026-27, no quotes) and sometimes :gw (type a number).
-- psql:    psql -d PLsight -v season="'2026-27'" -v gw=1 -f queries.sql
--
-- Sections A-B are for writing a single post. Section C is the season arc.
-- ============================================================================


-- ############################################################################
-- A. WRITING THE POST — run these while drafting
-- ############################################################################

-- A1. MATCH CARD --------------------------------------------------------------
-- Everything about one gameweek's fixtures, two rows per match. This is the one
-- you read while writing. The three-layer decomposition is the spine:
--   xg   = quality of chances created
--   xgot = quality of the shots actually struck   (xgot - xg = shot execution)
--   gf   = what went in                           (gf - xgot  = keeper beaten)
SELECT t.name AS team, o.name AS opponent,
       CASE WHEN f.is_home THEN 'H' ELSE 'A' END AS venue,
       f.gf, f.ga,
       f.xg, f.xgot,
       ROUND(f.gf - f.xg, 2)            AS finishing,
       ROUND(f.xgot - f.xg, 2)          AS shot_execution,
       ROUND(f.xgot_against - f.ga, 2)  AS keeping,
       f.possession_pct AS poss, f.shots, f.shots_on_target AS sot,
       f.big_chances AS bc, f.big_chances_missed AS bc_missed,
       f.touches_opp_box AS box_touches
FROM team_match_full f
JOIN team t ON t.team_id = f.team_id
JOIN team o ON o.team_id = f.opponent_id
WHERE f.season = :'season' AND f.division = 'E0' AND f.gameweek = :gw
ORDER BY f.match_date, f.match_id, f.is_home DESC;


-- A2. HOW THEY PLAYED ---------------------------------------------------------
-- Style, not quality.
--   directness  = share of accurate passes that were long balls
--   territory   = share of accurate passes in the opposition half
--   press_index = opponent accurate passes per tackle+interception.
--                 A rough PPDA. LOWER = more intense pressing. It is a proxy,
--                 there is no zonal data behind it — say so in the writing.
--   clr_per_int = clearances per interception. HIGH = deep block clearing its
--                 lines. LOW = higher line stepping out to intercept.
SELECT t.name AS team,
       ROUND(100.0 * f.accurate_long_balls / NULLIF(f.accurate_passes, 0), 1) AS directness,
       ROUND(100.0 * f.passes_opp_half     / NULLIF(f.accurate_passes, 0), 1) AS territory,
       ROUND(f.accurate_passes_against::numeric
             / NULLIF(f.tackles + f.interceptions, 0), 1)                     AS press_index,
       ROUND(f.clearances::numeric / NULLIF(f.interceptions, 0), 2)           AS clr_per_int,
       f.tackles, f.interceptions, f.clearances, f.blocks, f.keeper_saves,
       ROUND(f.ground_duel_pct, 0) AS grnd_duel_pct,
       ROUND(f.aerial_duel_pct, 0) AS aer_duel_pct,
       f.attack_left_pct AS atk_l, f.attack_centre_pct AS atk_c, f.attack_right_pct AS atk_r
FROM team_match_full f JOIN team t ON t.team_id = f.team_id
WHERE f.season = :'season' AND f.division = 'E0' AND f.gameweek = :gw
ORDER BY press_index NULLS LAST;


-- ############################################################################
-- B. THE STANDING TABLE — generated, not written. Goes under every post.
-- ############################################################################

-- B1. THE TABLE ---------------------------------------------------------------
-- Points beside what the underlying numbers say they deserved. `overperformance`
-- is the story: positive means scoring more than the chances merit, and
-- historically that does not last.
SELECT t.name AS team,
       COUNT(*)              AS pl,
       SUM(f.points)         AS pts,
       SUM(f.gf)             AS gf,
       SUM(f.ga)             AS ga,
       SUM(f.gf) - SUM(f.ga) AS gd,
       ROUND(SUM(f.xg), 2)                     AS xg,
       ROUND(SUM(f.xg_against), 2)             AS xga,
       ROUND(SUM(f.xg) - SUM(f.xg_against), 2) AS xgd,
       ROUND(SUM(f.gf) - SUM(f.xg), 2)         AS overperformance,
       ROUND(AVG(f.possession_pct), 1)         AS avg_poss
FROM team_match_full f JOIN team t ON t.team_id = f.team_id
WHERE f.season = :'season' AND f.division = 'E0' AND f.xg IS NOT NULL
GROUP BY t.name
ORDER BY pts DESC, gd DESC;


-- B2. THE THREE LAYERS, SEASON TO DATE ----------------------------------------
-- Where each team's goal difference actually comes from. A side can have a
-- normal goal difference while creating badly and finishing brilliantly — a
-- completely different team from one doing the reverse, and the distinction
-- most coverage misses.
SELECT t.name AS team,
       ROUND(SUM(f.xg), 2)                       AS chances_created,
       ROUND(SUM(f.xgot) - SUM(f.xg), 2)         AS shot_execution,
       ROUND(SUM(f.gf) - SUM(f.xgot), 2)         AS beat_the_keeper,
       ROUND(SUM(f.xg_against), 2)               AS chances_conceded,
       ROUND(SUM(f.xgot_against) - SUM(f.ga), 2) AS keeper_saved,
       SUM(f.big_chances) AS bc, SUM(f.big_chances_missed) AS bc_missed
FROM team_match_full f JOIN team t ON t.team_id = f.team_id
WHERE f.season = :'season' AND f.division = 'E0' AND f.xg IS NOT NULL
GROUP BY t.name
ORDER BY shot_execution DESC;


-- ############################################################################
-- C. THE SEASON ARC — what makes week 20 worth more than week 3
-- ############################################################################

-- C1. ROLLING FORM ------------------------------------------------------------
-- Rolling 6-match xG difference. The signature chart — same one every week, so
-- readers learn to read it.
SELECT t.name AS team, f.gameweek,
       ROUND(AVG(f.xg - f.xg_against) OVER (
           PARTITION BY f.team_id ORDER BY f.gameweek
           ROWS BETWEEN 5 PRECEDING AND CURRENT ROW), 3) AS roll6_xgd
FROM team_match_full f JOIN team t ON t.team_id = f.team_id
WHERE f.season = :'season' AND f.division = 'E0' AND f.xg IS NOT NULL
ORDER BY t.name, f.gameweek;


-- C2. MOVERS ------------------------------------------------------------------
-- Biggest week-on-week swings in rolling xG difference. Writes the
-- "what changed" section for you.
WITH roll AS (
    SELECT f.team_id, f.gameweek,
           AVG(f.xg - f.xg_against) OVER (
               PARTITION BY f.team_id ORDER BY f.gameweek
               ROWS BETWEEN 5 PRECEDING AND CURRENT ROW) AS r
    FROM team_match_full f
    WHERE f.season = :'season' AND f.division = 'E0' AND f.xg IS NOT NULL
),
delta AS (
    SELECT team_id, gameweek, r,
           r - LAG(r) OVER (PARTITION BY team_id ORDER BY gameweek) AS chg
    FROM roll
)
SELECT t.name AS team, d.gameweek, ROUND(d.r, 3) AS roll6_xgd, ROUND(d.chg, 3) AS change
FROM delta d JOIN team t ON t.team_id = d.team_id
WHERE d.gameweek = (SELECT MAX(gameweek) FROM delta) AND d.chg IS NOT NULL
ORDER BY ABS(d.chg) DESC
LIMIT 6;


-- C3. STYLE DRIFT -------------------------------------------------------------
-- The differentiated one. Nobody tracks how a team's *style* shifts across a
-- season off free data. Last 6 matches against first 6. A manager change, an
-- injury to a key passer or a tactical rethink shows up here weeks before it
-- shows up in results. Needs 12+ matches played, so it goes live around
-- November — worth knowing it's dormant until then.
WITH numbered AS (
    SELECT f.team_id, f.gameweek, f.accurate_long_balls, f.accurate_passes,
           f.passes_opp_half, f.tackles, f.interceptions, f.accurate_passes_against,
           ROW_NUMBER() OVER (PARTITION BY f.team_id ORDER BY f.gameweek) AS n,
           COUNT(*)    OVER (PARTITION BY f.team_id)                      AS total
    FROM team_match_full f
    WHERE f.season = :'season' AND f.division = 'E0' AND f.accurate_passes IS NOT NULL
),
windowed AS (
    SELECT *, CASE WHEN n <= 6 THEN 'first6'
                   WHEN n > total - 6 THEN 'last6' END AS phase
    FROM numbered WHERE total >= 12
)
SELECT t.name AS team,
       ROUND(100.0 * SUM(w.accurate_long_balls) FILTER (WHERE w.phase = 'first6')
             / NULLIF(SUM(w.accurate_passes) FILTER (WHERE w.phase = 'first6'), 0), 1) AS direct_first6,
       ROUND(100.0 * SUM(w.accurate_long_balls) FILTER (WHERE w.phase = 'last6')
             / NULLIF(SUM(w.accurate_passes) FILTER (WHERE w.phase = 'last6'), 0), 1)  AS direct_last6,
       ROUND(SUM(w.accurate_passes_against) FILTER (WHERE w.phase = 'first6')::numeric
             / NULLIF(SUM(w.tackles + w.interceptions) FILTER (WHERE w.phase = 'first6'), 0), 1) AS press_first6,
       ROUND(SUM(w.accurate_passes_against) FILTER (WHERE w.phase = 'last6')::numeric
             / NULLIF(SUM(w.tackles + w.interceptions) FILTER (WHERE w.phase = 'last6'), 0), 1)  AS press_last6
FROM windowed w JOIN team t ON t.team_id = w.team_id
WHERE w.phase IS NOT NULL
GROUP BY t.name
ORDER BY t.name;


-- C4. HISTORICAL CONTEXT ------------------------------------------------------
-- Shot-based, because there is no xG before this season. Twelve seasons of
-- football-data.co.uk gives shot dominance at the same stage, so you can write
-- "the best start by shot share any promoted side has made since 2014" —
-- which nobody else can do off a free dataset.
SELECT tm.season, t.name AS team,
       COUNT(*) AS pl, SUM(tm.points) AS pts,
       ROUND(SUM(tm.shots)::numeric
             / NULLIF(SUM(tm.shots) + SUM(tm.shots_against), 0), 3) AS tsr
FROM team_match tm JOIN team t USING (team_id)
WHERE tm.division = 'E0' AND tm.gameweek <= :gw
GROUP BY tm.season, t.name
ORDER BY tsr DESC NULLS LAST
LIMIT 40;
