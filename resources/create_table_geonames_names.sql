-- One row per distinct name of each geoname (name, asciiname and every entry
-- in the comma-separated alternatenames), indexed case-insensitively so that
-- name lookups need not scan alternatenames. Rebuilt from scratch each run.

DROP TABLE IF EXISTS geonames_names;

CREATE TABLE geonames_names (
    geonameid INTEGER NOT NULL REFERENCES geonames(geonameid),
    name TEXT NOT NULL COLLATE NOCASE,
    name_type TEXT NOT NULL  -- 'name', 'asciiname' or 'alternate'
);

INSERT INTO geonames_names (geonameid, name, name_type)
WITH RECURSIVE split(geonameid, item, rest) AS (
    SELECT geonameid, '', alternatenames || ','
    FROM geonames
    WHERE alternatenames IS NOT NULL AND alternatenames <> ''
    UNION ALL
    SELECT geonameid,
           trim(substr(rest, 1, instr(rest, ',') - 1)),
           substr(rest, instr(rest, ',') + 1)
    FROM split
    WHERE rest <> ''
),
all_names(geonameid, name, name_type, priority) AS (
    SELECT geonameid, name, 'name', 1 FROM geonames WHERE name <> ''
    UNION ALL
    SELECT geonameid, asciiname, 'asciiname', 2 FROM geonames WHERE asciiname <> ''
    UNION ALL
    SELECT geonameid, item, 'alternate', 3 FROM split WHERE item <> ''
),
ranked AS (
    SELECT geonameid, name, name_type,
           row_number() OVER (
               PARTITION BY geonameid, name COLLATE NOCASE ORDER BY priority
           ) AS n
    FROM all_names
)
SELECT geonameid, name, name_type FROM ranked WHERE n = 1;

CREATE INDEX idx_geonames_names_name ON geonames_names (name COLLATE NOCASE);
CREATE INDEX idx_geonames_names_geonameid ON geonames_names (geonameid);
