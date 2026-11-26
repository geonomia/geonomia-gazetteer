A project that presents a geonames gazetteer for a specified country as a datasette instance with an MCP server configured for use in AI applications which support tool calling. Planned to integrate with geonomia for location intelligence.

## Canned queries for georeferencing

The gazetteer supports georeferencing botanical specimens: finding the places named on a label, checking recorded coordinates, and producing a coordinate with an uncertainty radius. Canned queries package these tasks so that AI services using the MCP server (at `/-/mcp`) can call them as tools. They are also available in the Datasette web interface.

When the instance is served with `--setting default_allow_sql off`, MCP clients cannot run their own SQL, and the canned queries are the whole interface.

### Implemented

| Query | Parameters | What it does |
| --- | --- | --- |
| `find_place` | `name` | Finds places by name, matching official, ASCII and alternate names (including historical and variant spellings such as Malacca for Melaka), case-insensitively. Exact matches come first, then names starting with the text. `total_matches` shows whether a name is ambiguous. |
| `reverse_geocode` | `lat`, `lon`, `feature_classes` (optional) | Describes a point: the country, state and district containing it, and the 10 nearest gazetteer places within about 50 km, each with distance, compass direction and a locality phrase such as "3.2 km NE of Kampung X". |
| `validate_coordinates` | `lat`, `lon`, `admin1`, `admin2` (optional) | Checks whether a point falls in the state and district named on the label. Also tests the common transcription errors (latitude and longitude swapped, signs flipped), reporting the distance to the stated unit and an assessment for each. |
| `admin_unit_georeference` | `admin1`, `admin2` (optional) | Point-radius georeference for a locality that can only be placed in a state or district: the centre of the unit and the distance from it to the furthest point of the unit's boundary. |

### Proposed

Further queries that would be useful, not yet implemented:

- **`find_place_in_area(name, admin1, admin2)`**: `find_place` restricted to a state or district, to choose between places with the same name.
- **`place_ambiguity(name)`**: counts places with a name, grouped by state and feature type, so that an AI can report that a name is ambiguous rather than choosing one.
- **`find_admin_unit(name)`**: looks up states and districts by current, old or variant name (GADM `VARNAME_*` columns), e.g. North Borneo or Province Wellesley.
- **`nearby_features(lat, lon, radius_km, feature_class, feature_code)`**: features of a given type within a radius, for labels such as "foot of Gunung X" or "banks of Sungai Y".
- **`offset_from_place(geonameid, distance_km, direction)`**: turns a locality such as "8 mi NE of Kuching" into coordinates.
- **`feature_georeference(geonameid)`**: a place's point with uncertainty guidance for its feature type. GeoNames records a stream or mountain range as a single point, so it needs a much larger uncertainty than a village.
- **`feature_types(search)`**: describes GeoNames feature classes and codes (e.g. `STMB` stream bend, `EST` estate), so that an AI can interpret and filter by them.
- **`list_admin_units(admin1)`**: lists states and districts with their GeoNames and GADM codes.

### Design conventions

- **Columns.** Queries that return places use the same column names (`geonameid`, `name`, `lat`, `lon`, `feature_code`, `feature`, `admin1_name`, `admin2_name`, plus `distance_m` where relevant), so that results can be passed from one query to another.
- **Coordinates.** All coordinates are WGS84 decimal degrees, and distances are measured on the ellipsoid.
- **Descriptions.** Each query has a title and a description written for an AI client. `list_canned_queries` passes them on, and they act as its instructions for when and how to use the query.
- **Row limits.** Results are limited (e.g. 50 rows for `find_place`) so that a common name does not fill the client's context.
- **Boundaries.** State and district boundaries come from GADM 4.1, so they are present-day units. A historical unit may not match its modern namesake.

### Editing queries

Each query is a SQL file in [resources/queries/](resources/queries/), with a comment header giving its title and description (and optionally its parameter order):

```sql
-- title: Find a place by name
-- description: Look up gazetteer places by name, ...
--   continued on indented comment lines.
-- params: name
SELECT ...
```

After adding or editing a query, run `make metadata`. It writes the queries into [resources/metadata.json](resources/metadata.json) and turns on `datasette-mcp`'s canned query tools. Restart Datasette to pick up the changes.

The queries depend on:
- the SpatiaLite extension;
- the `geometry` column on `geonames`;
- the `geonames_names` lookup table;
- the `gadm_adm_*` boundary tables.

`make db` builds all of these.
