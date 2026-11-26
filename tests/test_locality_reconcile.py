"""Tests for the GADM locality reconciliation service (plugins/locality_reconcile.py).

They run against the built database (make db) with SpatiaLite loaded. Set
SPATIALITE_EXT to the extension to load: a path to mod_spatialite.dll on
Windows, or "spatialite" (the default) to let Datasette find it. `make test`
sets it from the Makefile.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from datasette.app import Datasette

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "geonames.db"
BASE = "/-/reconcile/gadm"

pytestmark = pytest.mark.skipif(not DB.exists(), reason="run make db first")


@pytest.fixture(scope="module")
def ds():
    datasette = Datasette(
        [str(DB)],
        plugins_dir=str(ROOT / "plugins"),
        sqlite_extensions=[os.environ.get("SPATIALITE_EXT", "spatialite")],
        metadata=json.loads((ROOT / "resources" / "metadata.json").read_text("utf-8")),
    )
    asyncio.run(datasette.invoke_startup())
    return datasette


def get(ds, path, **kwargs):
    return asyncio.run(ds.client.get(path, **kwargs))


def post(ds, path, **kwargs):
    return asyncio.run(ds.client.post(path, **kwargs))


def reconcile(ds, query, **extra):
    q = {"query": query, **extra}
    response = post(ds, BASE, data={"queries": json.dumps({"q0": q})})
    assert response.status_code == 200
    return response.json()["q0"]["result"]


def props(**values):
    return [{"pid": k, "v": v} for k, v in values.items()]


# --- service description ---------------------------------------------------

def test_manifest(ds):
    response = get(ds, BASE)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    m = response.json()
    assert m["versions"] == ["0.1", "0.2"]
    assert {t["id"] for t in m["defaultTypes"]} == {"district", "state"}
    assert m["view"]["url"].endswith(BASE + "/view/{{id}}")
    assert m["suggest"]["entity"]["service_path"] == "/suggest/entity"


def test_manifest_behind_proxy(ds):
    m = get(ds, BASE, headers={"x-forwarded-proto": "https",
                               "x-forwarded-host": "example.hf.space"}).json()
    assert m["view"]["url"] == "https://example.hf.space" + BASE + "/view/{{id}}"


def test_jsonp(ds):
    response = get(ds, BASE + "?callback=cb")
    assert response.text.startswith("cb(")


# --- reconciliation --------------------------------------------------------

def test_abbreviations_and_district_name(ds):
    result = reconcile(ds, "Kg. Sg. Rengit, Kota Tinggi")
    assert result[0]["id"] == "MYS.1.4_1"          # Kota Tinggi, Johor
    assert result[0]["match"] is True
    assert result[1]["id"] == "MYS.1_1"            # Johor ranks below


def test_state_property_supports_district(ds):
    result = reconcile(ds, "Pasoh Forest Reserve",
                       properties=props(admin1="Negeri Sembilan"))
    assert result[0]["id"] == "MYS.7.1_1"
    assert result[0]["match"] is True


def test_old_state_name_property(ds):
    result = reconcile(ds, "Batu Pahat", properties=props(admin1="Johore"))
    assert result[0]["id"] == "MYS.1.1_1"


def test_state_property_penalises_other_states(ds):
    result = reconcile(ds, "Bukit Timah", properties=props(admin1="Penang"))
    assert result[0]["id"].startswith("MYS.11.")    # Pulau Pinang
    assert all(not r["id"].startswith("MYS.1.") or r["score"] < result[0]["score"]
               for r in result)


def test_ambiguous_districts_match_the_state(ds):
    result = reconcile(ds, "Malacca: Bukit Bruang")
    assert result[0]["id"] == "MYS.6_1"            # Melaka
    assert result[0]["type"][0]["id"] == "state"


def test_ambiguous_name_is_not_matched(ds):
    result = reconcile(ds, "Bukit Timah")
    assert len({r["id"] for r in result if r["type"][0]["id"] == "district"}) >= 2
    assert not any(r["match"] for r in result)


def test_single_non_settlement_place_is_not_auto_matched(ds):
    result = reconcile(ds, "Mt. Kinabalu, 2200 m")
    assert result[0]["id"] == "MYS.13.5_1"         # Kota Belud, Sabah
    assert result[0]["match"] is False


def test_offset_is_projected(ds):
    result = reconcile(ds, "10 mi S of Kuala Lumpur")
    districts = [r for r in result if r["type"][0]["id"] == "district"]
    assert districts[0]["id"].startswith("MYS.15.")  # in Selangor, south of KL
    assert "10 mi S of" in districts[0]["description"]


def test_partial_name(ds):
    result = reconcile(ds, "Lambir Hills")
    assert result[0]["id"] == "MYS.14.19_1"        # Miri, Sarawak
    assert "starts the name" in result[0]["description"]


def test_type_filter(ds):
    result = reconcile(ds, "Kota Tinggi", type="state")
    assert result and all(r["type"][0]["id"] == "state" for r in result)


def test_limit(ds):
    assert len(reconcile(ds, "Bukit Timah", limit=1)) == 1


def test_other_country(ds):
    assert reconcile(ds, "Kepong", properties=props(country="Indonesia")) == []


def test_no_names(ds):
    assert reconcile(ds, "roadside near the river") == []
    assert reconcile(ds, "") == []


def test_get_and_string_queries(ds):
    response = get(ds, BASE, params={"queries": json.dumps({"a": "Kota Tinggi"})})
    assert response.json()["a"]["result"][0]["id"] == "MYS.1.4_1"


def test_bad_request(ds):
    response = post(ds, BASE, data={"queries": "not json"})
    assert response.status_code == 400


# --- other endpoints -------------------------------------------------------

def test_extend(ds):
    data = {"ids": ["MYS.1.4_1", "nope"],
            "properties": [{"id": "admin1_name"}, {"id": "uncertainty_m"}, {"id": "lat"}]}
    response = post(ds, BASE, data={"extend": json.dumps(data)})
    body = response.json()
    assert [m["id"] for m in body["meta"]] == ["admin1_name", "uncertainty_m", "lat"]
    row = body["rows"]["MYS.1.4_1"]
    assert row["admin1_name"] == [{"str": "Johor"}]
    assert row["uncertainty_m"][0]["int"] > 0
    assert body["rows"]["nope"]["lat"] == []


def test_propose_properties(ds):
    body = get(ds, BASE + "/extend/propose?type=district").json()
    assert "uncertainty_m" in {p["id"] for p in body["properties"]}


def test_suggest_entity(ds):
    body = get(ds, BASE + "/suggest/entity?prefix=Malac").json()
    assert "MYS.6_1" in {r["id"] for r in body["result"]}


def test_suggest_property_and_type(ds):
    assert get(ds, BASE + "/suggest/property?prefix=admin").json()["result"]
    assert get(ds, BASE + "/suggest/type?prefix=dis").json()["result"][0]["id"] == "district"


def test_preview(ds):
    response = get(ds, BASE + "/preview?id=MYS.1.4_1")
    assert response.status_code == 200
    assert "Kota Tinggi" in response.text
    assert get(ds, BASE + "/preview?id=nope").status_code == 404


def test_preview_outline(ds):
    district = get(ds, BASE + "/preview?id=MYS.1.4_1").text
    assert district.count("<path") == 2            # the state, then the district
    state = get(ds, BASE + "/preview?id=MYS.1_1").text
    assert state.count("<path") == 1


# --- canned query (for MCP clients) ----------------------------------------

def canned(ds, locality, admin1="", admin2=""):
    response = get(ds, "/geonames/reconcile_locality.json", params={
        "locality": locality, "admin1": admin1, "admin2": admin2,
        "_shape": "objects"})
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def test_canned_query_matches_service(ds):
    text = "Kg. Sg. Rengit, Kota Tinggi"
    rows, service = canned(ds, text), reconcile(ds, text, limit=25)
    assert [r["gid"] for r in rows] == [r["id"] for r in service]
    assert rows[0]["gid"] == "MYS.1.4_1" and rows[0]["is_match"] == 1
    assert rows[0]["score"] == service[0]["score"]
    assert rows[0]["uncertainty_m"] > 0


def test_canned_query_uses_admin1_and_offsets(ds):
    assert canned(ds, "Batu Pahat", admin1="Johore")[0]["gid"] == "MYS.1.1_1"
    districts = [r for r in canned(ds, "10 mi S of Kuala Lumpur")
                 if r["unit_type"] == "district"]
    assert districts[0]["gid"].startswith("MYS.15.")


def test_canned_query_no_names(ds):
    assert canned(ds, "roadside near the river") == []


# --- maps ------------------------------------------------------------------

def columns(ds, table):
    return asyncio.run(ds.get_database("geonames").table_columns(table))


def test_display_geometry_is_small(ds):
    assert "geometry" in columns(ds, "gadm_units")
    response = get(ds, "/geonames/gadm_units.geojson?_size=max")
    assert response.status_code == 200
    features = response.json()["features"]
    assert len(features) == 160 and all(f["geometry"] for f in features)
    assert len(response.content) < 1_500_000       # full resolution is ~10 MB


def test_geonames_gets_only_the_cluster_map(ds):
    # A column named geometry would add a second, polygon map to the page
    assert "geom" in columns(ds, "geonames") and "geometry" not in columns(ds, "geonames")
    html = get(ds, "/geonames/geonames").text
    assert "datasette-cluster-map" in html
    assert "datasette-geojson-map" not in html


def test_gadm_units_gets_only_the_polygon_map(ds):
    # Its lat/lon centre columns would otherwise add a point map too
    html = get(ds, "/geonames/gadm_units").text
    assert "datasette-geojson-map" in html
    assert "datasette-cluster-map" not in html
    assert "opentopomap" in html


def test_view_redirects_to_row(ds):
    response = get(ds, BASE + "/view/MYS.1.4_1")
    assert response.status_code == 302
    assert response.headers["location"].endswith("/geonames/gadm_units/MYS~2E1~2E4_1")
