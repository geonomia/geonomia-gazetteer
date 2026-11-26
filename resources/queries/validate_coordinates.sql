-- title: Check coordinates against the stated state and district
-- description: Check whether a specimen's coordinates fall inside the state
--   (admin1) and district (admin2) written on its label, and test the common
--   transcription errors. Parameters: lat, lon - WGS84 decimal degrees as
--   recorded; admin1 - the state on the label (old and variant names such as
--   Malacca, Johore, Penang, North Borneo are recognised); admin2 - the
--   district, optional. Returns one row per hypothesis: 'as given', then
--   latitude/longitude swapped, and each sign flipped. For each: the state and
--   district the point actually falls in, whether that matches the label
--   (1/0, or null when no name was given or it was not recognised), the
--   distance in km to the stated unit (0 when inside), and an assessment.
--   stated_admin1_matched/stated_admin2_matched show which boundaries the
--   label names were matched to. If 'as given' does not match but a
--   swapped/flipped row does, the record probably has that error.
WITH p AS (
    SELECT CAST(:lat AS REAL) AS lat,
           CAST(:lon AS REAL) AS lon,
           trim(:admin1) AS a1,
           trim(replace(replace(trim(:admin2), 'Daerah ', ''), ' District', ''))
               AS a2
),
stated1 AS (
    SELECT s.NAME_1, s.geometry
    FROM gadm_adm_1 AS s, p
    WHERE p.a1 <> ''
      AND (s.NAME_1 LIKE p.a1 OR '|' || s.VARNAME_1 LIKE '%|' || p.a1 || '%')
),
stated2 AS (
    SELECT d.NAME_1, d.NAME_2, d.geometry
    FROM gadm_adm_2 AS d, p
    WHERE p.a2 <> ''
      AND (d.NAME_2 LIKE p.a2 OR '|' || d.VARNAME_2 LIKE '%|' || p.a2 || '%')
      AND (NOT EXISTS (SELECT 1 FROM stated1)
           OR d.NAME_1 IN (SELECT NAME_1 FROM stated1))
),
variants(sort, variant, lat, lon) AS (
    SELECT 1, 'as given', lat, lon FROM p
    UNION ALL SELECT 2, 'latitude and longitude swapped', lon, lat FROM p
    UNION ALL SELECT 3, 'latitude sign flipped', -lat, lon FROM p
    UNION ALL SELECT 4, 'longitude sign flipped', lat, -lon FROM p
    UNION ALL SELECT 5, 'both signs flipped', -lat, -lon FROM p
),
points AS (
    SELECT v.*,
           CASE WHEN v.lat BETWEEN -90 AND 90 AND v.lon BETWEEN -180 AND 180
                THEN MakePoint(v.lon, v.lat, 4326) END AS g
    FROM variants AS v
),
checked AS (
    SELECT pt.sort, pt.variant, pt.lat, pt.lon, pt.g,
           (SELECT a.COUNTRY FROM gadm_adm_0 AS a
            WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1)
               AS country,
           (SELECT a.NAME_1 FROM gadm_adm_1 AS a
            WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1)
               AS found_admin1,
           (SELECT a.NAME_2 FROM gadm_adm_2 AS a
            WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1)
               AS found_admin2,
           CASE WHEN NOT EXISTS (SELECT 1 FROM stated1) OR pt.g IS NULL THEN NULL
                ELSE EXISTS (SELECT 1 FROM stated1 AS s
                             WHERE MbrContains(s.geometry, pt.g) = 1
                               AND ST_Contains(s.geometry, pt.g) = 1)
           END AS in_stated_admin1,
           CASE WHEN NOT EXISTS (SELECT 1 FROM stated2) OR pt.g IS NULL THEN NULL
                ELSE EXISTS (SELECT 1 FROM stated2 AS s
                             WHERE MbrContains(s.geometry, pt.g) = 1
                               AND ST_Contains(s.geometry, pt.g) = 1)
           END AS in_stated_admin2
    FROM points AS pt
),
measured AS (
    SELECT c.*,
           CASE WHEN c.in_stated_admin1 = 1 THEN 0
                WHEN c.in_stated_admin1 = 0 THEN
                    (SELECT min(ST_Distance(s.geometry, c.g, 1)) FROM stated1 AS s) / 1000.0
           END AS km_from_stated_admin1,
           CASE WHEN c.in_stated_admin2 = 1 THEN 0
                WHEN c.in_stated_admin2 = 0 THEN
                    (SELECT min(ST_Distance(s.geometry, c.g, 1)) FROM stated2 AS s) / 1000.0
           END AS km_from_stated_admin2
    FROM checked AS c
)
SELECT m.variant,
       round(m.lat, 5) AS lat,
       round(m.lon, 5) AS lon,
       m.country,
       m.found_admin1,
       m.found_admin2,
       (SELECT group_concat(NAME_1, '; ') FROM stated1) AS stated_admin1_matched,
       (SELECT group_concat(NAME_2 || ' (' || NAME_1 || ')', '; ') FROM stated2)
           AS stated_admin2_matched,
       m.in_stated_admin1,
       m.in_stated_admin2,
       round(m.km_from_stated_admin1, 1) AS km_from_stated_admin1,
       round(m.km_from_stated_admin2, 1) AS km_from_stated_admin2,
       CASE
           WHEN m.g IS NULL THEN 'not a valid coordinate'
           WHEN m.country IS NULL THEN 'outside the country'
           WHEN p.a1 <> '' AND NOT EXISTS (SELECT 1 FROM stated1)
               THEN 'stated state not recognised; found in ' || coalesce(m.found_admin1, '?')
           WHEN m.in_stated_admin1 = 0
               THEN printf('not in stated state (%.1f km away)', m.km_from_stated_admin1)
           WHEN p.a2 <> '' AND NOT EXISTS (SELECT 1 FROM stated2)
               THEN 'in stated state; stated district not recognised'
           WHEN m.in_stated_admin2 = 0
               THEN printf('in stated state but not in stated district (%.1f km away)',
                           m.km_from_stated_admin2)
           WHEN m.in_stated_admin2 = 1 THEN 'consistent with stated state and district'
           WHEN m.in_stated_admin1 = 1 THEN 'consistent with stated state'
           ELSE 'in ' || coalesce(m.found_admin1, '?')
       END AS assessment
FROM measured AS m, p
ORDER BY m.sort
