A project that presents a geonames gazetteer for a specified country as a datasette instance with an MCP server configured for use in AI applications which support tool calling. Planned to integrate with geonomia for location intelligence.

## Reconciling localities to GADM units (OpenRefine)

The instance runs a reconciliation service that takes a verbatim locality description from a specimen label and suggests the GADM state or district it lies in. It is meant for curators reconciling a spreadsheet column in OpenRefine.

**Service URL:** `<instance>/-/reconcile/gadm`, e.g. `http://localhost:8001/-/reconcile/gadm` with `make run`.

### Using it in OpenRefine

1. **Add the service.** On the locality column, choose *Reconcile → Start reconciling… → Add Standard Service* and enter the service URL.
2. **Choose a type, or none.** Choose *District*, *State*, or *Reconcile against no particular type* to get both.
3. **Add other columns if you have them.** Under *Also use relevant details from other columns*, map them to these property ids:

   | Property id | Column |
   | --- | --- |
   | `admin1` | the state as written. Old and variant names (Johore, Malacca, Penang, North Borneo) are recognised. |
   | `admin2` | the district as written |
   | `country` | a record whose country is not Malaysia gets no candidates |

4. **Start reconciling.** Each candidate's ID is its GADM GID, e.g. `MYS.1.4_1` for Kota Tinggi, Johor.
5. **Check what supported a candidate.** Hovering over a candidate shows a preview, and its description lists the names that supported it.
6. **Add columns from the matched unit.** *Edit column → Add columns from reconciled values* can add the unit's name and state, its centre point (`lat`, `lon`), a point-radius uncertainty (`uncertainty_m`), its area and its bounding box.

### How candidates are found

1. **Finding names.** Every run of one to six words in the description is looked up in the gazetteer's place names, including alternate and historical names, and in the GADM unit names and variants. Common label abbreviations and spellings are expanded: Kg./Kpg., Sg./Sungei, Bkt., Bt., G./Gn./Mt., Tg./Tanjong, P., Ulu/Hulu, Jln. Where names overlap, only the longest match is kept.
2. **Partial names.** A capitalised name with no exact match may be the start of a longer gazetteer name. For example, "Lambir Hills" is matched to *Lambir Forest Reserve*. Such matches count for half, and only when the start matches ten places or fewer.
3. **Weighing the places.** Each name's weight is spread over the places it could mean, favouring settlements and terrain features over hotels and buildings, and more populous places. Each place counts for the state and district it lies in. A GADM unit named directly counts for that unit.
4. **Offsets.** In phrases like "10 mi S of Kuala Lumpur", the reference place is projected by that distance and direction. The district at the projected point gets most of that name's weight.
5. **State information.** A state named in the text, or given in `admin1`, agrees with every district in that state and counts against districts in other states.
6. **Scores.** A candidate's score is the percentage of all the evidence that is consistent with it. A state scores slightly lower than a district with the same support, so that the most specific well-supported unit comes first.
7. **Automatic matches.** A candidate is marked as a match only when all of these hold:
   - it scores at least 75;
   - it leads every unrelated candidate by at least 25 points;
   - it is supported by a unit name, a property or a settlement, or by more than one name.

   A lone mountain or forest is left for a curator to confirm.

### Limitations

- **District level only.** GADM for Malaysia goes no further than districts, so a locality that identifies a village still reconciles to its district.
- **Present-day boundaries.** Old labels may refer to units that have since been renamed or redrawn.
- **Gaps and border points in GeoNames.** Gunung Tahan, for example, is only present as a triangulation station, which falls just inside Kelantan. Features on or near a boundary can be assigned to the neighbouring unit.
- **SpatiaLite needed for offsets.** Offsets are projected only when Datasette is started with SpatiaLite (`--load-extension=spatialite`). Without it, the reference place counts as a plain mention.

### Implementation

- **The service:** [plugins/locality_reconcile.py](plugins/locality_reconcile.py), implementing versions 0.1 and 0.2 of the Reconciliation API with suggest, preview, view and data extension.
- **Its tables:** `gadm_units`, `gadm_unit_names` and `place_units`, built by [resources/create_reconcile_tables.sql](resources/create_reconcile_tables.sql) as part of `make db`. Use `make reconcile-tables` to rebuild them alone.
- **Configuration:** under `plugins.locality-reconcile` in [resources/metadata.json](resources/metadata.json). The settings are `database`, `service_name` and `countries`.
- **Tests:** `make test` runs [tests/test_locality_reconcile.py](tests/test_locality_reconcile.py) against the built database.

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
