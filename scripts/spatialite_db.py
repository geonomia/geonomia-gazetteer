"""Open a SQLite database with the SpatiaLite extension loaded."""

import os
import sqlite3


def load_spatialite(conn, extension):
    """Load SpatiaLite into conn.

    extension is a path to mod_spatialite.dll on Windows, or "spatialite" /
    "mod_spatialite" to let SQLite find it on the library path (Linux).
    """
    if extension in ("spatialite", "mod_spatialite"):
        extension = "mod_spatialite"
    elif os.name == "nt":
        # Let Windows find the DLLs mod_spatialite depends on (GEOS, PROJ...),
        # which Python 3.8+ no longer looks for on PATH.
        os.add_dll_directory(os.path.dirname(os.path.abspath(extension)))
    conn.enable_load_extension(True)
    conn.load_extension(extension)
    conn.enable_load_extension(False)


def connect(db, extension):
    """Connect to db, load SpatiaLite and create its metadata tables if needed."""
    conn = sqlite3.connect(db)
    load_spatialite(conn, extension)
    if not conn.execute(
        "select 1 from sqlite_master where name = 'geometry_columns'"
    ).fetchone():
        # 1 = in a single transaction (much faster)
        conn.execute("select InitSpatialMetadata(1)")
    return conn
