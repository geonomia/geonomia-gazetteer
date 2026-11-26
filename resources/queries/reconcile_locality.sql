-- title: Reconcile a locality to a GADM state or district
-- description: Suggest the GADM state or district that a verbatim specimen
--   label locality lies in, e.g. "Kg. Sg. Rengit, Kota Tinggi" or
--   "10 mi S of Kuala Lumpur". This is the same scoring as the OpenRefine
--   reconciliation service: every run of one to six words is looked up in the
--   gazetteer (with label abbreviations such as Kg., Sg., Bt., Mt. expanded),
--   and offsets such as "10 km N of X" are projected from X. Parameters:
--   locality - the locality text as written; admin1 (optional) - the state as
--   written on the label, old names such as Johore or Malacca accepted;
--   admin2 (optional) - the district as written. Returns up to 25 candidates,
--   best first. score (0-100) is the percentage of the evidence in the text
--   consistent with the unit; is_match is 1 only when the top candidate is
--   well supported and clearly ahead of the rest. evidence lists the names
--   that supported each candidate: read it before accepting one. When
--   is_match is 0, check the place names with find_place, or a coordinate
--   with reverse_geocode. lat/lon is the unit's centre and uncertainty_m the
--   radius of a point-radius georeference for the whole unit. No rows means
--   no known place names were found.
-- params: locality, admin1, admin2
SELECT json_extract(j.value, '$.id') AS gid,
       json_extract(j.value, '$.name') AS name,
       json_extract(j.value, '$.type[0].id') AS unit_type,
       json_extract(j.value, '$.score') AS score,
       json_extract(j.value, '$.match') AS is_match,
       json_extract(j.value, '$.description') AS evidence,
       u.admin1_name,
       u.lat,
       u.lon,
       u.uncertainty_m
FROM json_each(reconcile_locality(:locality, nullif(trim(:admin1), ''),
                                  nullif(trim(:admin2), ''))) AS j
JOIN gadm_units AS u ON u.gid = json_extract(j.value, '$.id')
ORDER BY j.key
