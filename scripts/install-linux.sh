#!/usr/bin/env bash
# Install the system packages needed to build and serve the gazetteer on
# Debian/Ubuntu: SpatiaLite for Datasette, plus the tools the Makefile uses.
#
# Usage: scripts/install-linux.sh        (uses sudo when not run as root)
# Then:  make install && make run
set -euo pipefail

PACKAGES=(
  # SpatiaLite extension for SQLite (mod_spatialite.so), loaded by Datasette
  libsqlite3-mod-spatialite
  # spatialite and sqlite3 command-line tools, handy for inspecting the db
  spatialite-bin
  sqlite3
  # Used by the Makefile
  make
  wget
  unzip
  python3
  python3-venv
  python3-pip
)

if ! command -v apt-get >/dev/null 2>&1; then
  echo "install-linux.sh: apt-get not found; this script supports Debian/Ubuntu only" >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  echo "install-linux.sh: run as root or install sudo" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y --no-install-recommends "${PACKAGES[@]}"

# Check that Python's sqlite3 can load SpatiaLite the way Datasette will.
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect(":memory:")
conn.enable_load_extension(True)
conn.load_extension("mod_spatialite")
version, proj, geos = conn.execute(
    "select spatialite_version(), proj_version(), geos_version()"
).fetchone()
print(f"SpatiaLite {version} loaded (PROJ {proj}; GEOS {geos})")
EOF
