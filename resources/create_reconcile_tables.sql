-- Tables for the GADM locality reconciliation service
-- (plugins/locality_reconcile.py). Needs SpatiaLite, the gadm_adm_* tables
-- and the geonames geom column. Rebuilt from scratch each run.

-- One row per GADM state (level 1) and district (level 2), with a
-- point-radius georeference: the centre of the unit (centroid, or a point
-- inside it when the centroid falls outside) and the distance from it to the
-- furthest boundary point (a convex hull vertex).
SELECT DiscardGeometryColumn('gadm_units', 'geometry')
WHERE EXISTS (SELECT 1 FROM geometry_columns
              WHERE f_table_name = 'gadm_units' AND f_geometry_column = 'geometry');
DROP TABLE IF EXISTS gadm_units;
CREATE TABLE gadm_units (
    gid TEXT PRIMARY KEY,
    level INTEGER NOT NULL,
    type TEXT NOT NULL,          -- 'state' or 'district'
    name TEXT NOT NULL,
    label TEXT NOT NULL,         -- e.g. 'Petaling, Selangor'
    admin1_gid TEXT NOT NULL,
    admin1_name TEXT NOT NULL,
    unit_type TEXT,              -- GADM ENGTYPE, e.g. 'District', 'Federal Territory'
    lat REAL,
    lon REAL,
    uncertainty_m INTEGER,
    area_km2 REAL,
    min_lat REAL, min_lon REAL, max_lat REAL, max_lon REAL
);

INSERT INTO gadm_units
WITH unit AS (
    SELECT GID_1 AS gid, 1 AS level, 'state' AS type, NAME_1 AS name,
           NAME_1 AS label, GID_1 AS admin1_gid, NAME_1 AS admin1_name,
           ENGTYPE_1 AS unit_type, geometry
    FROM gadm_adm_1
    UNION ALL
    SELECT GID_2, 2, 'district', NAME_2, NAME_2 || ', ' || NAME_1, GID_1, NAME_1,
           ENGTYPE_2, geometry
    FROM gadm_adm_2
),
centred AS (
    SELECT unit.*,
           CASE WHEN ST_Contains(geometry, ST_Centroid(geometry)) = 1
                THEN ST_Centroid(geometry)
                ELSE ST_PointOnSurface(geometry) END AS c,
           ST_DissolvePoints(ST_ConvexHull(geometry)) AS hull
    FROM unit
),
hull_points(gid, c, hull, i, n) AS (
    SELECT gid, c, hull, 1, ST_NumGeometries(hull) FROM centred
    UNION ALL
    SELECT gid, c, hull, i + 1, n FROM hull_points WHERE i < n
),
radius AS (
    SELECT gid, CAST(max(ST_Distance(c, GeometryN(hull, i), 1)) AS INTEGER) + 1 AS r
    FROM hull_points
    GROUP BY gid
)
SELECT c.gid, c.level, c.type, c.name, c.label, c.admin1_gid, c.admin1_name,
       c.unit_type,
       round(Y(c.c), 5), round(X(c.c), 5),
       r.r,
       round(ST_Area(c.geometry, 1) / 1e6, 1),
       round(MbrMinY(c.geometry), 5), round(MbrMinX(c.geometry), 5),
       round(MbrMaxY(c.geometry), 5), round(MbrMaxX(c.geometry), 5)
FROM centred AS c
JOIN radius AS r USING (gid);

-- A simplified outline for display only (web maps and the OpenRefine
-- preview): about 200 m tolerance, snapped to a 1 m grid, roughly 5% of the
-- size of the GADM polygons. Spatial calculations use gadm_adm_* instead.
SELECT AddGeometryColumn('gadm_units', 'geometry', 4326, 'MULTIPOLYGON', 'XY');
UPDATE gadm_units
SET geometry = (
    SELECT coalesce(
               CastToMulti(ST_SnapToGrid(ST_SimplifyPreserveTopology(a.geometry, 0.002), 0.00001)),
               CastToMulti(a.geometry))
    FROM (SELECT GID_1 AS gid, geometry FROM gadm_adm_1
          UNION ALL
          SELECT GID_2, geometry FROM gadm_adm_2) AS a
    WHERE a.gid = gadm_units.gid
);

-- Which state and district each gazetteer place lies in. Places just outside
-- every polygon (coastal and sea features, mostly) are given the nearest
-- district within about 10 km; assigned_by records which.
DROP TABLE IF EXISTS place_units;
CREATE TABLE place_units (
    geonameid INTEGER PRIMARY KEY REFERENCES geonames(geonameid),
    gid_1 TEXT,
    gid_2 TEXT,
    assigned_by TEXT              -- 'contains' or 'nearest'
);

INSERT INTO place_units (geonameid, gid_2, assigned_by)
SELECT g.geonameid,
       (SELECT d.GID_2 FROM gadm_adm_2 AS d
        WHERE d.rowid IN (SELECT rowid FROM SpatialIndex
                          WHERE f_table_name = 'gadm_adm_2'
                            AND search_frame = g.geom)
          AND ST_Contains(d.geometry, g.geom) = 1
        LIMIT 1),
       'contains'
FROM geonames AS g
WHERE g.geom IS NOT NULL;

UPDATE place_units
SET gid_2 = (
        SELECT d.GID_2
        FROM geonames AS g, gadm_adm_2 AS d
        WHERE g.geonameid = place_units.geonameid
          AND d.rowid IN (SELECT rowid FROM SpatialIndex
                          WHERE f_table_name = 'gadm_adm_2'
                            AND search_frame = BuildCircleMbr(X(g.geom), Y(g.geom), 0.1))
        ORDER BY ST_Distance(d.geometry, g.geom)
        LIMIT 1),
    assigned_by = 'nearest'
WHERE gid_2 IS NULL;

UPDATE place_units
SET gid_1 = (SELECT admin1_gid FROM gadm_units WHERE gid = place_units.gid_2);

UPDATE place_units SET assigned_by = NULL WHERE gid_2 IS NULL;

CREATE INDEX idx_place_units_gid_2 ON place_units (gid_2);

-- Every name of each GADM unit: the official name, GADM's variant names
-- (VARNAME, '|'-separated) and, for states, the names and alternate names of
-- the GeoNames first-order division lying in it (e.g. Malacca, Penang).
DROP TABLE IF EXISTS gadm_unit_names;
CREATE TABLE gadm_unit_names (
    gid TEXT NOT NULL REFERENCES gadm_units(gid),
    name TEXT NOT NULL COLLATE NOCASE,
    name_type TEXT NOT NULL       -- 'official', 'gadm variant' or 'geonames'
);

INSERT INTO gadm_unit_names (gid, name, name_type)
WITH RECURSIVE variants(gid, rest, item) AS (
    SELECT GID_1, VARNAME_1 || '|', '' FROM gadm_adm_1
    UNION ALL
    SELECT GID_2, VARNAME_2 || '|', '' FROM gadm_adm_2
    UNION ALL
    SELECT gid, substr(rest, instr(rest, '|') + 1),
           trim(substr(rest, 1, instr(rest, '|') - 1))
    FROM variants
    WHERE rest <> ''
),
all_names(gid, name, name_type, priority) AS (
    SELECT gid, name, 'official', 1 FROM gadm_units
    UNION ALL
    SELECT gid, item, 'gadm variant', 2 FROM variants
    WHERE item NOT IN ('', 'NA', '<Null>')
    UNION ALL
    SELECT pu.gid_1, n.name, 'geonames', 3
    FROM geonames AS g
    JOIN place_units AS pu USING (geonameid)
    JOIN geonames_names AS n USING (geonameid)
    WHERE g.feature_code = 'ADM1' AND pu.gid_1 IS NOT NULL
),
ranked AS (
    SELECT gid, name, name_type,
           row_number() OVER (PARTITION BY gid, name COLLATE NOCASE
                              ORDER BY priority) AS n
    FROM all_names
)
SELECT gid, name, name_type FROM ranked
WHERE n = 1
  -- Codes and fragments (PRK, Pe, Jor) and phrases shared by several units
  -- would cause false matches in free text
  AND (length(name) >= 4 OR name_type = 'official')
  AND name NOT IN ('Federal Territory', 'Wilayah Persekutuan', 'State');

CREATE INDEX idx_gadm_unit_names_name ON gadm_unit_names (name COLLATE NOCASE);
