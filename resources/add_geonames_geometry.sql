-- Add a SpatiaLite point geometry (WGS84) to geonames, built from
-- longitude/latitude, with a spatial index. Safe to re-run: the column and
-- index are only created once and the points are recomputed.

SELECT AddGeometryColumn('geonames', 'geometry', 4326, 'POINT', 'XY')
WHERE NOT EXISTS (
    SELECT 1 FROM geometry_columns
    WHERE f_table_name = 'geonames' AND f_geometry_column = 'geometry'
);

UPDATE geonames
SET geometry = MakePoint(longitude, latitude, 4326)
WHERE longitude IS NOT NULL AND latitude IS NOT NULL;

SELECT CreateSpatialIndex('geonames', 'geometry')
WHERE NOT EXISTS (
    SELECT 1 FROM geometry_columns
    WHERE f_table_name = 'geonames' AND f_geometry_column = 'geometry'
      AND spatial_index_enabled = 1
);
