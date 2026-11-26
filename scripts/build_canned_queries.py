"""Write the canned queries in resources/queries/*.sql into metadata.json.

Each .sql file becomes a canned query named after the file. Its leading
comment block supplies the title and description:

    -- title: Find a place by name
    -- description: First line of the description,
    --   continued on indented comment lines.
    -- params: lat, lon   (optional: parameter order; default first use)
    SELECT ...

The comment block is not included in the stored SQL. The script also turns
on datasette-mcp's canned query tools, so the queries are offered to MCP
clients. Re-run it (make metadata) after editing a query.

Usage:
    python scripts/build_canned_queries.py [--metadata resources/metadata.json]
        [--queries resources/queries] [--database geonames]
"""

import argparse
import json
import re
from pathlib import Path


def parse_query(path):
    header, body, in_header = {}, [], True
    key = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if in_header and line.startswith("--"):
            text = line[2:]
            match = re.match(r"\s(\w+):\s*(.*)", text)
            if match:
                key = match.group(1)
                header[key] = match.group(2).strip()
            elif key and text.startswith("   "):
                header[key] += " " + text.strip()
            continue
        in_header = False
        body.append(line)
    sql = "\n".join(body).strip() + "\n"
    query = {}
    for field in ("title", "description"):
        if field in header:
            query[field] = header[field]
    # Each parameter once, in order of first use, so Datasette's form and
    # MCP clients do not see a parameter listed for every place it is used.
    # A "-- params: a, b" header line sets the order explicitly.
    params = re.findall(r"(?<![:\w]):(\w+)", re.sub(r"'[^']*'", "''", sql))
    params = list(dict.fromkeys(params))
    if "params" in header:
        listed = [p.strip() for p in header["params"].split(",") if p.strip()]
        if set(listed) != set(params):
            raise SystemExit(f"{path}: params header {listed} does not match "
                             f"the parameters used in the SQL {params}")
        params = listed
    query["params"] = params
    query["sql"] = sql
    return query


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metadata", default="resources/metadata.json")
    parser.add_argument("--queries", default="resources/queries")
    parser.add_argument("--database", default="geonames")
    args = parser.parse_args()

    metadata_path = Path(args.metadata)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    queries = {
        path.stem: parse_query(path)
        for path in sorted(Path(args.queries).glob("*.sql"))
    }
    database = metadata.setdefault("databases", {}).setdefault(args.database, {})
    database["queries"] = queries
    metadata.setdefault("plugins", {}).setdefault("datasette-mcp", {})[
        "canned_queries"
    ] = True
    metadata_path.write_text(
        json.dumps(metadata, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(queries)} canned queries to {metadata_path}: "
          + ", ".join(queries))


if __name__ == "__main__":
    main()
