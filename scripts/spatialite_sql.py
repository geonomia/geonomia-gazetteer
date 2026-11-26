"""Run a SQL file against a database with SpatiaLite loaded.

Usage:
    python scripts/spatialite_sql.py DB SQLFILE [--extension PATH]
"""

import argparse

from spatialite_db import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db")
    parser.add_argument("sqlfile")
    parser.add_argument("--extension", default="mod_spatialite")
    args = parser.parse_args()

    conn = connect(args.db, args.extension)
    with open(args.sqlfile, encoding="utf-8") as f:
        sql = f.read()
    with conn:
        conn.executescript(sql)
    conn.close()


if __name__ == "__main__":
    main()
