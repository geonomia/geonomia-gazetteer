"""Import the feature layers of a GeoPackage into a SpatiaLite database.

Each layer becomes a table named <prefix><layer>, lower-cased (e.g. GADM's
ADM_ADM_1 -> gadm_adm_1 with --prefix gadm_ --strip ADM_), with its attribute columns, a registered geometry
column called "geometry" and a spatial index. Existing tables of the same
name are replaced, so the import can be re-run.

Usage:
    python scripts/import_gpkg.py DB GPKG [--prefix gadm_] [--strip ADM_]
                                          [--extension PATH]

--extension is the SpatiaLite module to load: a path to mod_spatialite.dll
on Windows, or "spatialite"/"mod_spatialite" to let SQLite find it (Linux).
"""

import argparse
import re

from spatialite_db import connect


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def import_layer(conn, layer, geom_col, geom_type, srid, table):
    columns = [
        (name, coltype or "TEXT")
        for _, name, coltype, *_ in conn.execute(
            f"pragma gpkg.table_info({quote(layer)})"
        )
        if name not in ("fid", geom_col)
    ]
    exists = conn.execute(
        "select 1 from geometry_columns where f_table_name = lower(?)", [table]
    ).fetchone()
    if exists:
        conn.execute("select DropTable(NULL, ?)", [table])
    conn.execute(f"drop table if exists {quote(table)}")

    column_defs = ", ".join(f"{quote(n)} {t}" for n, t in columns)
    conn.execute(
        f"create table {quote(table)} (id integer primary key, {column_defs})"
    )
    ok = conn.execute(
        "select AddGeometryColumn(?, 'geometry', ?, ?, 'XY')",
        [table, srid, geom_type],
    ).fetchone()[0]
    if not ok:
        raise SystemExit(f"AddGeometryColumn failed for {table}")

    names = ", ".join(quote(n) for n, _ in columns)
    cast = "CastToMulti" if geom_type.startswith("MULTI") else ""
    # GeomFromGPB converts GeoPackage geometry blobs to SpatiaLite geometry
    conn.execute(
        f"insert into {quote(table)} (id, {names}, geometry) "
        f"select fid, {names}, {cast}(GeomFromGPB({quote(geom_col)})) "
        f"from gpkg.{quote(layer)}"
    )
    conn.execute("select CreateSpatialIndex(?, 'geometry')", [table])
    count = conn.execute(f"select count(*) from {quote(table)}").fetchone()[0]
    print(f"{layer} -> {table}: {count} rows")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db")
    parser.add_argument("gpkg")
    parser.add_argument("--prefix", default="gadm_")
    parser.add_argument(
        "--strip", default="", help="remove this prefix from layer names first"
    )
    parser.add_argument("--extension", default="mod_spatialite")
    args = parser.parse_args()

    conn = connect(args.db, args.extension)
    conn.execute("attach database ? as gpkg", [args.gpkg])
    layers = conn.execute(
        "select c.table_name, g.column_name, g.geometry_type_name, g.srs_id "
        "from gpkg.gpkg_contents c "
        "join gpkg.gpkg_geometry_columns g using (table_name) "
        "where c.data_type = 'features' order by c.table_name"
    ).fetchall()
    with conn:
        for layer, geom_col, geom_type, srid in layers:
            # Store as MULTI* so mixed single/multi features fit one column
            multi = geom_type.upper()
            if multi in ("POINT", "LINESTRING", "POLYGON"):
                multi = "MULTI" + multi
            name = layer[len(args.strip):] if args.strip and layer.startswith(args.strip) else layer
            table = args.prefix + re.sub(r"\W", "_", name).lower()
            import_layer(conn, layer, geom_col, multi, srid, table)
    conn.execute("detach database gpkg")
    conn.close()


if __name__ == "__main__":
    main()
