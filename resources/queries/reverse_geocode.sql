-- title: Describe a coordinate (reverse geocode)
-- description: Describe where a point is: the country, state and district
--   boundaries (GADM) that contain it, and the 10 nearest gazetteer places
--   within about 50 km, each with its distance and the compass direction from
--   the place to the point. Use it to check a specimen's coordinates against
--   its label, or to write a locality description. Parameters: lat, lon -
--   WGS84 decimal degrees; feature_classes - optional comma-separated
--   GeoNames feature classes to limit the nearby places to (P populated
--   places, H streams/lakes, T mountains/hills, L areas, S spots/buildings/
--   estates, V forest, R roads, A administrative); leave empty for all.
--   Rows with kind 'country'/'state'/'district' are the containing boundaries;
--   rows with kind 'nearby' are places, nearest first, with a ready-made
--   locality phrase such as "3.2 km NE of Kampung X".
-- params: lat, lon, feature_classes
WITH pt AS (
    SELECT MakePoint(CAST(:lon AS REAL), CAST(:lat AS REAL), 4326) AS g
),
admin AS (
    SELECT 1 AS sort, 'country' AS kind, NULL AS geonameid, a.COUNTRY AS name,
           NULL AS feature_code, 'country' AS feature,
           NULL AS admin1_name, NULL AS admin2_name
    FROM gadm_adm_0 AS a, pt
    WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1
    UNION ALL
    SELECT 2, 'state', NULL, a.NAME_1, NULL, lower(a.ENGTYPE_1), a.NAME_1, NULL
    FROM gadm_adm_1 AS a, pt
    WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1
    UNION ALL
    SELECT 3, 'district', NULL, a.NAME_2, NULL, lower(a.ENGTYPE_2), a.NAME_1, a.NAME_2
    FROM gadm_adm_2 AS a, pt
    WHERE MbrContains(a.geometry, pt.g) = 1 AND ST_Contains(a.geometry, pt.g) = 1
),
nearby AS (
    SELECT g.geonameid, g.name, g.feature_code, g.latitude, g.longitude,
           g.admin1_code, g.admin2_code,
           ST_Distance(g.geom, pt.g, 1) AS distance_m,
           Degrees(Azimuth(g.geom, pt.g)) AS azimuth
    FROM geonames AS g, pt
    WHERE g.rowid IN (
        SELECT rowid FROM SpatialIndex
        WHERE f_table_name = 'geonames' AND f_geometry_column = 'geom'
          AND search_frame = BuildCircleMbr(
              CAST(:lon AS REAL), CAST(:lat AS REAL), 0.5, 4326)
    )
    AND (trim(:feature_classes) = ''
         OR instr(',' || upper(replace(:feature_classes, ' ', '')) || ',',
                  ',' || g.feature_class || ',') > 0)
    ORDER BY distance_m
    LIMIT 10
),
nearby_described AS (
    SELECT n.*,
           CASE WHEN n.distance_m < 50 THEN ''
                ELSE trim(substr(
                    'N  NNENE ENEE  ESESE SSES  SSWSW WSWW  WNWNW NNW',
                    (CAST((n.azimuth + 360 + 11.25) / 22.5 AS INTEGER) % 16) * 3 + 1,
                    3))
           END AS direction
    FROM nearby AS n
)
SELECT kind, geonameid, name, feature_code, feature, admin1_name, admin2_name,
       lat, lon, distance_m, direction, locality
FROM (
    SELECT sort, kind, geonameid, name, feature_code, feature,
           admin1_name, admin2_name,
           NULL AS lat, NULL AS lon, NULL AS distance_m, NULL AS direction,
           NULL AS locality
    FROM admin
    UNION ALL
    SELECT 4, 'nearby', n.geonameid, n.name, n.feature_code, fc.description,
           a1.name, a2.name,
           round(n.latitude, 5), round(n.longitude, 5),
           CAST(round(n.distance_m) AS INTEGER),
           n.direction,
           CASE WHEN n.direction = '' THEN 'at ' || n.name
                ELSE printf('%.1f km %s of %s', n.distance_m / 1000.0,
                            n.direction, n.name)
           END
    FROM nearby_described AS n
    LEFT JOIN feature_codes AS fc ON fc.code = n.feature_code
    LEFT JOIN admin_1_codes AS a1 ON a1.code = n.admin1_code
    LEFT JOIN admin_2_codes AS a2 ON a2.code = n.admin2_code
)
ORDER BY sort, distance_m
