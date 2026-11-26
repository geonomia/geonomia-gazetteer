-- title: Find a place by name
-- description: Look up gazetteer places by name, matching the official name,
--   the ASCII name and all alternate names (including historical and variant
--   spellings), case-insensitively. Parameter: name - the place name as written
--   on a specimen label, or the start of one (e.g. "Bukit Ti" matches
--   "Bukit Timah"). Exact matches are returned first (match_type 'exact'),
--   then names that start with the text ('prefix'), most populous first.
--   matched_name is the name variant that matched. total_matches counts all
--   matching places, of which at most 50 are returned: if it is more than 1,
--   the name is ambiguous and the state, district or feature type on the
--   label is needed to choose. lat/lon are WGS84 decimal degrees.
WITH matches AS (
    SELECT geonameid,
           min(CASE WHEN name = trim(:name) THEN 0 ELSE 1 END) AS prefix_only,
           substr(min(CASE WHEN name = trim(:name) THEN '0' ELSE '1' END || name), 2)
               AS matched_name
    FROM geonames_names
    WHERE trim(:name) <> ''
      AND name >= trim(:name)
      AND name < trim(:name) || char(1114111)
    GROUP BY geonameid
)
SELECT g.geonameid,
       g.name,
       round(g.latitude, 5) AS lat,
       round(g.longitude, 5) AS lon,
       g.feature_class,
       g.feature_code,
       fc.description AS feature,
       a1.name AS admin1_name,
       a2.name AS admin2_name,
       nullif(g.population, 0) AS population,
       coalesce(nullif(g.elevation, ''), nullif(g.dem, '')) AS elevation_m,
       m.matched_name,
       CASE m.prefix_only WHEN 0 THEN 'exact' ELSE 'prefix' END AS match_type,
       count(*) OVER () AS total_matches
FROM matches AS m
JOIN geonames AS g USING (geonameid)
LEFT JOIN feature_codes AS fc ON fc.code = g.feature_code
LEFT JOIN admin_1_codes AS a1 ON a1.code = g.admin1_code
LEFT JOIN admin_2_codes AS a2 ON a2.code = g.admin2_code
ORDER BY m.prefix_only, g.population DESC, g.name
LIMIT 50
