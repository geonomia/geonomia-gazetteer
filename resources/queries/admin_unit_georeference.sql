-- title: Georeference to a state or district (point-radius)
-- description: Give a coordinate and uncertainty radius for a specimen whose
--   locality can only be placed in a state or district, using the
--   point-radius method: the centre of the unit (its centroid, or a point
--   inside it when the centroid falls outside, e.g. for crescent shapes or
--   island groups) and the distance from that centre to the furthest point of
--   the unit's boundary. Parameters: admin1 - the state (old and variant names
--   such as Malacca, Johore, Penang, North Borneo are recognised); admin2 -
--   the district, optional; when given, the district is georeferenced,
--   otherwise the whole state. Returns one row per matching unit: lat/lon
--   (WGS84 decimal degrees), uncertainty_radius_m, area_km2 and how the centre
--   was chosen. Boundaries are GADM 4.1 (present-day units), so a historical
--   unit may not match its modern namesake.
WITH p AS (
    SELECT trim(:admin1) AS a1,
           trim(replace(replace(trim(:admin2), 'Daerah ', ''), ' District', ''))
               AS a2
),
unit AS (
    SELECT 'district' AS level, d.NAME_2 AS name, d.NAME_1 AS admin1_name,
           d.geometry
    FROM gadm_adm_2 AS d
    JOIN gadm_adm_1 AS s ON s.NAME_1 = d.NAME_1, p
    WHERE p.a2 <> ''
      AND (d.NAME_2 LIKE p.a2 OR '|' || d.VARNAME_2 LIKE '%|' || p.a2 || '%')
      AND (p.a1 = '' OR s.NAME_1 LIKE p.a1
           OR '|' || s.VARNAME_1 LIKE '%|' || p.a1 || '%')
    UNION ALL
    SELECT 'state', s.NAME_1, s.NAME_1, s.geometry
    FROM gadm_adm_1 AS s, p
    WHERE p.a2 = '' AND p.a1 <> ''
      AND (s.NAME_1 LIKE p.a1 OR '|' || s.VARNAME_1 LIKE '%|' || p.a1 || '%')
),
centred AS (
    SELECT level, name, admin1_name,
           ST_Contains(geometry, ST_Centroid(geometry)) AS centroid_inside,
           CASE WHEN ST_Contains(geometry, ST_Centroid(geometry))
                THEN ST_Centroid(geometry)
                ELSE ST_PointOnSurface(geometry) END AS c,
           ST_Area(geometry, 1) / 1e6 AS area_km2,
           -- The furthest boundary point from any centre is a hull vertex
           ST_DissolvePoints(ST_ConvexHull(geometry)) AS hull
    FROM unit
),
hull_points(level, name, admin1_name, centroid_inside, c, area_km2, hull, i, n) AS (
    SELECT level, name, admin1_name, centroid_inside, c, area_km2, hull,
           1, ST_NumGeometries(hull)
    FROM centred
    UNION ALL
    SELECT level, name, admin1_name, centroid_inside, c, area_km2, hull, i + 1, n
    FROM hull_points
    WHERE i < n
)
SELECT level,
       name,
       admin1_name,
       round(Y(c), 5) AS lat,
       round(X(c), 5) AS lon,
       CAST(max(ST_Distance(c, GeometryN(hull, i), 1)) AS INTEGER) + 1
           AS uncertainty_radius_m,
       round(area_km2, 1) AS area_km2,
       CASE WHEN centroid_inside THEN 'centroid'
            ELSE 'point inside unit (centroid falls outside)' END AS centre_method,
       'WGS84' AS datum,
       'point-radius; GADM 4.1 boundaries' AS method
FROM hull_points
GROUP BY level, name, admin1_name
ORDER BY level, admin1_name, name
