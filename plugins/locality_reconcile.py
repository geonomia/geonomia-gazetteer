"""Reconcile free-text locality descriptions to GADM states and districts.

An OpenRefine-compatible reconciliation service (Reconciliation API 0.1/0.2)
at /-/reconcile/gadm. Given a verbatim locality such as

    "Sungai Besi, 10 mi S of Kuala Lumpur, Selangor"

it finds the gazetteer and GADM names in the text by looking up every run
of one to six words (with common abbreviations such as Kg., Sg., Bt., Mt.
expanded), then weighs the places those names could refer to by the state
and district each lies in. Candidates are GADM units (districts and states)
identified by their GADM GID. A candidate's score is the percentage of the
evidence in the description that is consistent with the unit, and its
description says which names supported it.

Optional query properties add evidence from other columns of the record:
admin1 (state as written), admin2 (district as written) and country (a
record whose country is not one of the configured countries gets no
candidates).

Offsets such as "10 km N of X" are projected from X when SpatiaLite is
loaded; without it they count as plain mentions of X.

Needs the tables built by resources/create_reconcile_tables.sql
(gadm_units, gadm_unit_names, place_units) and geonames_names.

Configuration (metadata.json, all optional):

    "plugins": {"locality-reconcile": {
        "database": "geonames",
        "service_name": "...",
        "countries": ["Malaysia", "MY", "MYS"]
    }}
"""

import itertools
import json
import math
import re
from collections import defaultdict

from datasette import hookimpl
from datasette.utils import tilde_encode
from datasette.utils.asgi import Response

PLUGIN = "locality-reconcile"
BASE_PATH = "/-/reconcile/gadm"
DEFAULT_LIMIT = 5
MAX_LIMIT = 25
MAX_NGRAM = 6
MAX_VARIANTS = 16
MAX_PARTIAL_PHRASES = 30
MAX_PARTIAL_PLACES = 10

TYPES = [
    {"id": "district", "name": "District (GADM level 2)"},
    {"id": "state", "name": "State (GADM level 1)"},
]

QUERY_PROPERTIES = [
    {"id": "admin1", "name": "State, as written"},
    {"id": "admin2", "name": "District, as written"},
    {"id": "country", "name": "Country, as written"},
]

# id -> (name, gadm_units column, value kind)
EXTEND_PROPERTIES = {
    "name": ("Name", "name", "str"),
    "label": ("Label", "label", "str"),
    "type": ("Type", "type", "str"),
    "unit_type": ("GADM unit type", "unit_type", "str"),
    "admin1_gid": ("State GID", "admin1_gid", "str"),
    "admin1_name": ("State", "admin1_name", "str"),
    "lat": ("Centre latitude", "lat", "float"),
    "lon": ("Centre longitude", "lon", "float"),
    "uncertainty_m": ("Uncertainty radius (m)", "uncertainty_m", "int"),
    "area_km2": ("Area (km2)", "area_km2", "float"),
    "min_lat": ("Bounding box min latitude", "min_lat", "float"),
    "min_lon": ("Bounding box min longitude", "min_lon", "float"),
    "max_lat": ("Bounding box max latitude", "max_lat", "float"),
    "max_lon": ("Bounding box max longitude", "max_lon", "float"),
}

# Abbreviations and spelling variants common on Malaysian specimen labels.
# Each word is looked up as written and as each alternative.
VARIANTS = {
    "kg": ["kampung", "kampong"],
    "kpg": ["kampung", "kampong"],
    "kampung": ["kampong"],
    "kampong": ["kampung"],
    "sg": ["sungai", "sungei"],
    "sgi": ["sungai", "sungei"],
    "sungai": ["sungei"],
    "sungei": ["sungai"],
    "bkt": ["bukit"],
    "bt": ["bukit", "batu"],
    "gn": ["gunung", "gunong"],
    "gng": ["gunung", "gunong"],
    "g": ["gunung"],
    "gunung": ["gunong"],
    "gunong": ["gunung"],
    "mt": ["mount", "gunung"],
    "mount": ["gunung"],
    "tg": ["tanjung", "tanjong"],
    "tanjung": ["tanjong"],
    "tanjong": ["tanjung"],
    "p": ["pulau"],
    "pulo": ["pulau"],
    "ulu": ["hulu"],
    "hulu": ["ulu"],
    "jln": ["jalan"],
    "jl": ["jalan"],
}

# Words that cannot start or end a name in running text
CONNECTORS = {
    "of", "near", "from", "to", "the", "and", "at", "in", "on", "by", "along",
    "above", "below", "between", "towards", "via", "nr", "ca", "c", "approx",
    "km", "kms", "mi", "miles", "mile", "m", "ft", "feet", "alt", "elev",
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "nne", "ene", "ese", "sse",
    "ssw", "wsw", "wnw", "nnw", "north", "south", "east", "west",
}

# Single words too generic to count as a place name on their own
GENERIC = {
    "sungai", "sungei", "kampung", "kampong", "bukit", "gunung", "gunong",
    "mount", "jalan", "road", "river", "forest", "reserve", "hill", "hills",
    "mountain", "mountains", "island", "pulau", "estate", "ulu", "hulu",
    "kuala", "batu", "tanjung", "tanjong", "lake", "tasik", "bay", "teluk",
    "park", "taman", "camp", "station", "track", "trail", "ridge", "valley",
    "swamp", "beach", "pantai", "district", "state", "division", "daerah",
    "bahagian", "negeri", "malaysia", "malaya", "borneo", "peninsular",
    "village", "town", "near", "summit", "peak", "slope", "slopes", "foot",
    "primary", "secondary", "lowland", "upland", "dipterocarp", "heath",
    "limestone", "logged", "edge", "side", "bank", "banks", "national",
    "field", "centre", "center", "studies", "research", "garden", "gardens",
}

# Prior weight of a place by GeoNames feature class: populated places and
# terrain features are what labels usually cite; hotels, schools etc. less so
CLASS_PRIOR = {"P": 2.0, "T": 1.5, "H": 1.0, "L": 1.0, "V": 1.0,
               "S": 0.5, "R": 0.5, "U": 0.5}

UNIT_SHARE = 0.7          # share of a name's evidence from GADM unit names
OFFSET_SHARE = 0.7        # share of an offset place given to the projected point
PARTIAL_FACTOR = 0.5      # weight of a name matching only the start of a place name
PROPERTY_WEIGHT = 3.0     # weight of an admin1/admin2 property
STATE_PROPERTY_PENALTY = 0.3
STATE_MENTION_PENALTY = 0.5
STATE_SPECIFICITY = 0.9   # a state ranks below a district with the same support
MATCH_SCORE = 75
MATCH_MARGIN = 25

TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")

DIRECTIONS = {
    "n": 0, "nne": 22.5, "ne": 45, "ene": 67.5, "e": 90, "ese": 112.5,
    "se": 135, "sse": 157.5, "s": 180, "ssw": 202.5, "sw": 225,
    "wsw": 247.5, "w": 270, "wnw": 292.5, "nw": 315, "nnw": 337.5,
    "north": 0, "northeast": 45, "east": 90, "southeast": 135, "south": 180,
    "southwest": 225, "west": 270, "northwest": 315,
}
UNIT_METRES = {"km": 1000, "kms": 1000, "kilometre": 1000, "kilometres": 1000,
               "kilometer": 1000, "kilometers": 1000, "mi": 1609.344,
               "mile": 1609.344, "miles": 1609.344, "m": 1, "metres": 1,
               "meters": 1, "metre": 1, "meter": 1}
OFFSET_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(km|kms|kilomet(?:er|re)s?|mi|miles?|m|met(?:er|re)s?)\.?"
    r"\s*(?:to\s+the\s+)?"
    r"(?:(north[\s-]?east|north[\s-]?west|south[\s-]?east|south[\s-]?west|"
    r"north|south|east|west|[nsew]{1,3})\.?\s*)?"
    r"(?:of|from)\s+",
    re.IGNORECASE,
)


# --- hooks ---------------------------------------------------------------

@hookimpl
def register_routes():
    base = re.escape(BASE_PATH)
    return [
        (base + r"$", reconcile_view),
        (base + r"/preview$", preview_view),
        (base + r"/view/(?P<gid>[^/]+)$", entity_view),
        (base + r"/extend/propose$", propose_properties_view),
        (base + r"/suggest/entity$", suggest_entity_view),
        (base + r"/suggest/type$", suggest_type_view),
        (base + r"/suggest/property$", suggest_property_view),
    ]


@hookimpl
def skip_csrf(datasette, scope):
    # OpenRefine POSTs form-encoded queries without a CSRF token
    return scope.get("path", "").startswith(datasette.urls.path(BASE_PATH))


# --- views ---------------------------------------------------------------

def _config(datasette):
    return datasette.plugin_config(PLUGIN) or {}


async def _database(datasette, request):
    name = _config(datasette).get("database", "geonames")
    await datasette.ensure_permissions(
        request.actor,
        [("view-table", (name, "gadm_units")), ("view-database", name),
         "view-instance"],
    )
    return name, datasette.get_database(name)


def _respond(request, body, status=200):
    callback = request.args.get("callback")
    headers = {"Access-Control-Allow-Origin": "*"}
    if callback and re.fullmatch(r"[\w.$]+", callback):
        return Response(
            f"{callback}({json.dumps(body)})",
            status=status,
            content_type="application/javascript; charset=utf-8",
            headers=headers,
        )
    return Response.json(body, status=status, headers=headers)


def _base_url(datasette, request):
    url = datasette.absolute_url(request, datasette.urls.path(BASE_PATH))
    proto = request.headers.get("x-forwarded-proto")
    if proto in ("http", "https"):
        url = re.sub(r"^https?://", proto + "://", url)
    return url


async def _params(request):
    """Request parameters from the query string and, for a POST, the form
    body. The body can only be read once, so read everything here."""
    params = {key: request.args.get(key) for key in request.args.keys()}
    if request.method == "POST":
        params.update(await request.post_vars())
    return params


async def reconcile_view(request, datasette):
    _, db = await _database(datasette, request)
    params = await _params(request)
    try:
        queries = params.get("queries")
        if queries:
            results = {}
            for key, query in json.loads(queries).items():
                if isinstance(query, str):
                    query = {"query": query}
                results[key] = {
                    "result": await reconcile_query(db, query, _config(datasette))
                }
            return _respond(request, results)
        extend = params.get("extend")
        if extend:
            return _respond(request, await extend_data(db, json.loads(extend)))
    except (ValueError, TypeError, AttributeError) as e:
        return _respond(request, {"error": f"Bad request: {e}"}, status=400)
    return _respond(request, manifest(datasette, request))


def manifest(datasette, request):
    base = _base_url(datasette, request)
    config = _config(datasette)

    def service(path):
        return {"service_url": base, "service_path": path}

    return {
        "versions": ["0.1", "0.2"],
        "name": config.get(
            "service_name", "GADM state or district from a locality description"
        ),
        "identifierSpace": config.get("identifier_space", "https://gadm.org/"),
        "schemaSpace": config.get("schema_space", "https://gadm.org/"),
        "defaultTypes": TYPES,
        "view": {"url": base + "/view/{{id}}"},
        "preview": {"url": base + "/preview?id={{id}}", "width": 430, "height": 170},
        "batchSize": 10,
        "suggest": {
            "entity": service("/suggest/entity"),
            "type": service("/suggest/type"),
            "property": service("/suggest/property"),
        },
        "extend": {
            "propose_properties": service("/extend/propose"),
            "property_settings": [],
        },
    }


async def entity_view(request, datasette):
    name, _ = await _database(datasette, request)
    gid = request.url_vars["gid"]
    return Response.redirect(datasette.urls.row(name, "gadm_units", tilde_encode(gid)))


def _escape(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


async def preview_view(request, datasette):
    name, db = await _database(datasette, request)
    gid = request.args.get("id", "")
    rows = (await db.execute("select * from gadm_units where gid = ?", [gid])).rows
    if not rows:
        return Response.html("<p>Unknown GADM unit</p>", status=404)
    u = dict(rows[0])
    link = datasette.urls.row(name, "gadm_units", tilde_encode(gid))
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{font:13px/1.4 system-ui,sans-serif;margin:8px}}
th{{text-align:left;padding-right:8px;font-weight:600;color:#555}}</style></head>
<body><div><a href="{_escape(link)}" target="_blank"><b>{_escape(u['label'])}</b></a>
&nbsp;<code>{_escape(u['gid'])}</code></div><table>
<tr><th>Type</th><td>{_escape(u['type'])} ({_escape(u['unit_type'])})</td></tr>
<tr><th>Centre</th><td>{u['lat']}, {u['lon']} (WGS84)</td></tr>
<tr><th>Uncertainty</th><td>{u['uncertainty_m']:,} m</td></tr>
<tr><th>Area</th><td>{u['area_km2']:,} km&sup2;</td></tr></table></body></html>"""
    return Response.html(html)


async def propose_properties_view(request, datasette):
    await _database(datasette, request)
    type_ = request.args.get("type", "district")
    limit = int(request.args.get("limit", len(EXTEND_PROPERTIES)))
    properties = [{"id": pid, "name": spec[0]} for pid, spec in EXTEND_PROPERTIES.items()]
    return _respond(request, {"type": type_, "properties": properties[:limit]})


async def suggest_entity_view(request, datasette):
    _, db = await _database(datasette, request)
    prefix = (request.args.get("prefix") or "").strip()
    if not prefix:
        return _respond(request, {"result": []})
    rows = await db.execute(
        """
        select u.gid, u.label, u.name, u.type, min(n.name) as matched
        from gadm_unit_names as n join gadm_units as u using (gid)
        where n.name >= :p and n.name < :p || char(1114111)
        group by u.gid
        order by u.level, u.label
        limit 15
        """,
        {"p": prefix},
    )
    result = []
    for r in rows:
        description = r["type"]
        if r["matched"].lower() != r["name"].lower():
            description += f" (also called {r['matched']})"
        result.append({"id": r["gid"], "name": r["label"], "description": description})
    return _respond(request, {"result": result})


async def suggest_type_view(request, datasette):
    prefix = (request.args.get("prefix") or "").lower()
    return _respond(request, {"result": [
        t for t in TYPES if prefix in t["id"] or prefix in t["name"].lower()
    ]})


async def suggest_property_view(request, datasette):
    prefix = (request.args.get("prefix") or "").lower()
    return _respond(request, {"result": [
        p for p in QUERY_PROPERTIES
        if p["id"].startswith(prefix) or p["name"].lower().startswith(prefix)
    ]})


async def extend_data(db, data):
    ids = [str(i) for i in data.get("ids", [])]
    requested = [p["id"] for p in data.get("properties", [])]
    found = {}
    if ids:
        placeholders = ", ".join("?" for _ in ids)
        result = await db.execute(
            f"select * from gadm_units where gid in ({placeholders})", ids
        )
        found = {r["gid"]: dict(r) for r in result}
    rows = {}
    for gid in ids:
        unit = found.get(gid)
        values = {}
        for pid in requested:
            spec = EXTEND_PROPERTIES.get(pid)
            value = unit.get(spec[1]) if unit and spec else None
            values[pid] = [] if value is None else [{spec[2]: value}]
        rows[gid] = values
    meta = [{"id": pid, "name": EXTEND_PROPERTIES.get(pid, (pid,))[0]}
            for pid in requested]
    return {"meta": meta, "rows": rows}


# --- finding names in the text ---------------------------------------------

def tokenize(text):
    return [(m.group(), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def word_variants(word):
    return [word] + VARIANTS.get(word.lower().rstrip("."), [])


def candidate_spans(tokens):
    """Every run of 1..MAX_NGRAM words that could be a name, with the
    spelling variants to look up for it."""
    spans = []
    for i in range(len(tokens)):
        for j in range(i + 1, min(i + MAX_NGRAM, len(tokens)) + 1):
            words = [t[0] for t in tokens[i:j]]
            first, last = words[0].lower(), words[-1].lower()
            if first in CONNECTORS or last in CONNECTORS or words[0].isdigit():
                continue
            if len(words) == 1 and (len(first) < 4 or first in GENERIC
                                    or first in VARIANTS or first.isdigit()):
                continue
            variants = [" ".join(v) for v in itertools.islice(
                itertools.product(*(word_variants(w) for w in words)), MAX_VARIANTS)]
            spans.append({
                "start": i, "end": j, "words": len(words), "variants": variants,
                "capitalised": words[0][:1].isupper() and words[-1][:1].isupper(),
            })
    return spans


async def lookup_names(db, phrases):
    """Places and GADM units with exactly one of these names."""
    if not phrases:
        return [], []
    placeholders = ", ".join("?" for _ in phrases)
    places = await db.execute(
        f"""
        select n.name as matched, g.geonameid, g.name, g.feature_class,
               g.feature_code, fc.description as feature,
               coalesce(g.population, 0) as population,
               g.latitude, g.longitude, pu.gid_1, pu.gid_2
        from geonames_names as n
        join geonames as g using (geonameid)
        left join place_units as pu using (geonameid)
        left join feature_codes as fc on fc.code = g.feature_code
        where n.name in ({placeholders}) and g.feature_class <> 'A'
        """,
        phrases,
    )
    units = await db.execute(
        f"""
        select n.name as matched, u.gid, u.type, u.label, u.admin1_gid
        from gadm_unit_names as n join gadm_units as u using (gid)
        where n.name in ({placeholders})
        """,
        phrases,
    )
    return [dict(r) for r in places], [dict(r) for r in units]


async def lookup_prefixes(db, phrases):
    """Places with a name starting with one of these phrases followed by a
    space (e.g. 'Lambir' -> 'Lambir Forest Reserve'), for phrases matching
    at most MAX_PARTIAL_PLACES places. Returns {phrase lower: [places]}."""
    found = {}
    for phrase in phrases[:MAX_PARTIAL_PHRASES]:
        rows = await db.execute(
            """
            select n.name as matched, g.geonameid, g.name, g.feature_class,
                   g.feature_code, fc.description as feature,
                   coalesce(g.population, 0) as population,
                   g.latitude, g.longitude, pu.gid_1, pu.gid_2
            from geonames_names as n
            join geonames as g using (geonameid)
            left join place_units as pu using (geonameid)
            left join feature_codes as fc on fc.code = g.feature_code
            where n.name >= :p || ' ' and n.name < :p || ' ' || char(1114111)
              and g.feature_class <> 'A'
            limit :limit
            """,
            {"p": phrase, "limit": MAX_PARTIAL_PLACES * 3 + 1},
        )
        places = {r["geonameid"]: dict(r) for r in rows}
        if 0 < len(places) <= MAX_PARTIAL_PLACES:
            found[phrase.lower()] = list(places.values())
    return found


def parse_offsets(text, tokens):
    """Map the index of the first word of a reference name to an offset
    (distance_m, bearing or None, phrase) for 'N km SE of <name>'."""
    offsets = {}
    for m in OFFSET_RE.finditer(text):
        metres = float(m.group(1).replace(",", ".")) * UNIT_METRES[m.group(2).lower()]
        bearing = None
        if m.group(3):
            bearing = DIRECTIONS.get(re.sub(r"[\s-]", "", m.group(3).lower()))
        for index, (_, start, _) in enumerate(tokens):
            if start >= m.end():
                offsets[index] = (metres, bearing, m.group().strip())
                break
    return offsets


def destination(lat, lon, bearing_deg, distance_m):
    """Point at a distance and bearing from (lat, lon) on a sphere."""
    d = distance_m / 6371008.8
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


async def district_at(db, lat, lon):
    """(GID_2, GID_1) of the district containing the point, (None, None)
    if none does, or False if SpatiaLite is not loaded."""
    try:
        rows = await db.execute(
            """
            select GID_2, GID_1 from gadm_adm_2
            where rowid in (select rowid from SpatialIndex
                            where f_table_name = 'gadm_adm_2'
                              and search_frame = MakePoint(:lon, :lat, 4326))
              and ST_Contains(geometry, MakePoint(:lon, :lat, 4326)) = 1
            limit 1
            """,
            {"lat": lat, "lon": lon},
        )
    except Exception:  # SpatiaLite not loaded
        return False
    return tuple(rows.rows[0]) if rows.rows else (None, None)


async def resolve_admin(db, value, level, within_states=None):
    """GADM units of a level whose name or variant name equals value,
    ignoring prefixes and suffixes such as 'State of' and 'District'."""
    value = (value or "").strip()
    if not value:
        return []
    names = {value}
    stripped = re.sub(r"^(state of|daerah|bahagian|district of|division of)\s+|"
                      r"\s+(district|division|state)$", "", value, flags=re.I).strip()
    if stripped:
        names.add(stripped)
    placeholders = ", ".join("?" for _ in names)
    rows = await db.execute(
        f"""
        select distinct u.gid, u.admin1_gid from gadm_unit_names as n
        join gadm_units as u using (gid)
        where n.name in ({placeholders}) and u.level = ?
        """,
        [*names, level],
    )
    units = [dict(r) for r in rows]
    if within_states:
        units = [u for u in units if u["admin1_gid"] in within_states]
    return units


def property_values(query, pid):
    values = []
    for prop in query.get("properties") or []:
        if prop.get("pid") == pid:
            v = prop.get("v")
            for item in v if isinstance(v, list) else [v]:
                if isinstance(item, dict):
                    item = item.get("id") or item.get("name")
                if item not in (None, ""):
                    values.append(str(item))
    return values


# --- weighing the evidence -------------------------------------------------

def place_prior(place):
    prior = CLASS_PRIOR.get(place["feature_class"], 1.0)
    if place["population"]:
        prior *= 1 + math.log10(1 + place["population"]) / 4
    return prior


class Evidence:
    """One piece of evidence: a name in the text or a property value.

    district[gid]   share of this evidence pointing to that district
    state[gid]      share pointing to that state (directly or via a district)
    state_only[gid] share pointing to the state as a whole; it is consistent
                    with, but says nothing specific about, its districts
    notes[gid]      explanations for a unit; state_notes[gid] those of the
                    state-level evidence, which also explain its districts
    """

    def __init__(self, weight, strong=False):
        self.weight = weight
        self.strong = strong          # a unit name, property or settlement
        self.district = defaultdict(float)
        self.state = defaultdict(float)
        self.state_only = defaultdict(float)
        self.notes = defaultdict(list)
        self.state_notes = defaultdict(list)

    def add_district(self, gid_2, gid_1, share, note, state_note=None):
        self.district[gid_2] += share
        self.state[gid_1] += share
        self.notes[gid_2].append(note)
        self.notes[gid_1].append(state_note or note)

    def add_state(self, gid_1, share, note):
        self.state[gid_1] += share
        self.state_only[gid_1] += share
        self.notes[gid_1].append(note)
        self.state_notes[gid_1].append(note)

    def notes_for(self, unit):
        if unit["type"] == "state":
            return self.notes.get(unit["gid"], [])
        return (self.notes.get(unit["gid"], [])
                + self.state_notes.get(unit["admin1_gid"], []))


async def name_evidence(db, span, offset):
    quoted = f'"{span["text"]}"'
    units, places = span["units"], span["places"]
    projecting = offset is not None and offset[1] is not None and places
    if projecting:
        units = []   # the name is a reference point, not where the specimen is
    weight = min(span["words"], 3) + (1 if units else 0)
    if span.get("partial"):
        weight *= PARTIAL_FACTOR
    strong = bool(units) or any(p["feature_class"] == "P" for p in places)
    ev = Evidence(weight, strong)

    unit_share = UNIT_SHARE if places else 1.0
    place_share = 1.0 - unit_share if units else 1.0
    for u in units:
        each = unit_share / len(units)
        if u["type"] == "state":
            ev.add_state(u["gid"], each, f"{quoted} names this state")
        else:
            ev.add_district(u["gid"], u["admin1_gid"], each,
                            f"{quoted} names this district")

    if places:
        total = sum(place_prior(p) for p in places)
        counts = defaultdict(lambda: [0, set()])   # gid -> [places, kinds]
        shares = []                                 # (gid_2, gid_1, share)
        for p in places:
            share = place_share * place_prior(p) / total
            targets = [(p["gid_2"], p["gid_1"], share)]
            if projecting:
                lat, lon = destination(p["latitude"], p["longitude"], offset[1], offset[0])
                at = await district_at(db, lat, lon)
                if at is not False:
                    targets = [(p["gid_2"], p["gid_1"], share * (1 - OFFSET_SHARE)),
                               (at[0], at[1], share * OFFSET_SHARE)]
            for gid_2, gid_1, s in targets:
                shares.append((gid_2, gid_1, s))
                for gid in {gid_2, gid_1} - {None}:
                    counts[gid][0] += 1
                    counts[gid][1].add(p["feature"] or p["feature_code"] or "")
        n = len(places)
        subject = f"{quoted} ({offset[2]} it)" if offset else quoted
        if span.get("partial"):
            what = f'{subject} starts the name of {n} place{"s" if n > 1 else ""}'
        else:
            what = f'{subject} names {n} place{"s" if n > 1 else ""}'

        def note(gid):
            count, kinds = counts[gid]
            kinds_text = ", ".join(sorted(k for k in kinds if k)[:3])
            return f"{what}, {count} here ({kinds_text})"

        for gid_2, gid_1, s in shares:
            if gid_2:
                ev.add_district(gid_2, gid_1, s, note(gid_2), note(gid_1))
            elif gid_1:
                ev.add_state(gid_1, s, note(gid_1))
    return ev


async def reconcile_query(db, query, config):
    text = str(query.get("query") or "")
    limit = max(1, min(int(query.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    types = query.get("type") or []
    types = {t if isinstance(t, str) else t.get("id")
             for t in (types if isinstance(types, list) else [types])} - {None}

    countries = {c.lower() for c in config.get("countries", ["Malaysia", "MY", "MYS"])}
    stated_countries = property_values(query, "country")
    if stated_countries and not any(c.lower() in countries for c in stated_countries):
        return []

    tokens = tokenize(text)
    spans = candidate_spans(tokens)
    places, units = await lookup_names(
        db, sorted({v for s in spans for v in s["variants"]}, key=str.lower))
    places_by_name, units_by_name = defaultdict(list), defaultdict(list)
    for p in places:
        places_by_name[p["matched"].lower()].append(p)
    for u in units:
        units_by_name[u["matched"].lower()].append(u)

    def collect(span, by_name, key):
        seen, found = set(), []
        for v in span["variants"]:
            for item in by_name.get(v.lower(), []):
                if item[key] not in seen:
                    seen.add(item[key])
                    found.append(item)
        return found

    exact = []
    for span in spans:
        span["places"] = collect(span, places_by_name, "geonameid")
        span["units"] = collect(span, units_by_name, "gid")
        if span["places"] or span["units"]:
            exact.append(span)

    def inside(a, b):
        return b["start"] <= a["start"] and a["end"] <= b["end"] and b["words"] > a["words"]

    # Keep only the longest names: drop a match lying inside a longer one
    exact = [s for s in exact if not any(inside(s, o) for o in exact)]

    # Capitalised names with no exact match may be the start of a longer
    # gazetteer name, e.g. "Lambir Hills" -> "Lambir Forest Reserve"
    unmatched = [s for s in spans if not s["places"] and not s["units"]
                 and s["capitalised"] and s["words"] <= 3
                 and not any(inside(s, o) for o in exact)
                 and (s["words"] > 1 or len(s["variants"][0]) >= 5)]
    partial_found = await lookup_prefixes(
        db, list(dict.fromkeys(v for s in unmatched for v in s["variants"])))
    partial = []
    for span in unmatched:
        span["places"] = collect(span, partial_found, "geonameid")
        if span["places"]:
            span["partial"] = True
            partial.append(span)
    partial = [s for s in partial if not any(inside(s, o) for o in partial)]

    offsets = parse_offsets(text, tokens)
    evidence = []
    mentioned_states = set()
    for span in exact + partial:
        span["text"] = text[tokens[span["start"]][1]:tokens[span["end"] - 1][2]]
        ev = await name_evidence(db, span, offsets.get(span["start"]))
        evidence.append(ev)
        if span["units"] and all(u["type"] == "state" for u in span["units"]) \
                and len(span["units"]) == 1 and span["start"] not in offsets:
            mentioned_states.add(span["units"][0]["gid"])

    # Other columns of the record
    stated_states = set()
    for value in property_values(query, "admin1"):
        found = await resolve_admin(db, value, 1)
        ev = Evidence(PROPERTY_WEIGHT, strong=True)
        for u in found:
            stated_states.add(u["gid"])
            ev.add_state(u["gid"], 1 / len(found), f'state given as "{value}"')
        evidence.append(ev)
    for value in property_values(query, "admin2"):
        found = await resolve_admin(db, value, 2, stated_states or None)
        ev = Evidence(PROPERTY_WEIGHT, strong=True)
        for u in found:
            ev.add_district(u["gid"], u["admin1_gid"], 1 / len(found),
                            f'district given as "{value}"')
        evidence.append(ev)

    total_weight = sum(e.weight for e in evidence)
    if not total_weight:
        return []

    # A district's support: evidence pointing to it, plus state-level
    # evidence for its state. A state's support: everything pointing into it.
    district_ids = {gid for e in evidence for gid in e.district if gid}
    state_ids = {gid for e in evidence for gid in e.state if gid}
    if not district_ids and not state_ids:
        return []
    ids = sorted(district_ids | state_ids)
    rows = await db.execute(
        f"select gid, type, label, admin1_gid from gadm_units "
        f"where gid in ({', '.join('?' for _ in ids)})", ids)
    info = {r["gid"]: dict(r) for r in rows}

    candidates = []
    for gid, unit in info.items():
        if types and unit["type"] not in types:
            continue
        state = unit["admin1_gid"]
        supporting = []
        score = 0.0
        for e in evidence:
            if unit["type"] == "district":
                s = e.district.get(gid, 0) + e.state_only.get(state, 0)
            else:
                s = e.state.get(gid, 0)
            if s:
                score += e.weight * min(s, 1.0)
                supporting.append(e)
        if unit["type"] == "district" and not any(e.district.get(gid) for e in evidence):
            continue
        share = score / total_weight
        if stated_states and state not in stated_states:
            share *= STATE_PROPERTY_PENALTY
        elif mentioned_states and state not in mentioned_states:
            share *= STATE_MENTION_PENALTY
        if unit["type"] == "state":
            share *= STATE_SPECIFICITY
        notes = [note for e in supporting for note in e.notes_for(unit)]
        candidates.append({
            "id": gid,
            "name": unit["label"],
            "type": [t for t in TYPES if t["id"] == unit["type"]],
            "score": round(min(share, 1.0) * 100, 1),
            "match": False,
            "description": "; ".join(dict.fromkeys(notes))[:500],
            "_state": state,
            "_strong": sum(1 for e in supporting if e.strong),
            "_pieces": len(supporting),
        })

    candidates.sort(key=lambda c: (-c["score"], c["type"][0]["id"] != "district", c["name"]))
    if candidates:
        top = candidates[0]
        rivals = [c for c in candidates[1:]
                  if c["_state"] != top["_state"]
                  or c["type"][0]["id"] == top["type"][0]["id"] == "district"]
        well_supported = top["_strong"] >= 1 or top["_pieces"] >= 2
        if (top["score"] >= MATCH_SCORE and well_supported
                and (not rivals or top["score"] - rivals[0]["score"] >= MATCH_MARGIN)):
            top["match"] = True
    for c in candidates:
        for key in ("_state", "_strong", "_pieces"):
            del c[key]
    return candidates[:limit]
