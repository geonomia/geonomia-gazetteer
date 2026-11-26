"""Make --load-extension work for SQLite extensions on Windows.

Two problems stop e.g. mod_spatialite.dll loading on Windows, both reported
as "The specified module could not be found":

1. Datasette parses --load-extension as "path:entrypoint", splitting at the
   first colon, so "C:/x/mod_spatialite.dll" becomes path "C" with
   entrypoint "/x/mod_spatialite.dll". This plugin rejoins the drive letter.

2. Since Python 3.8, Windows no longer searches PATH for the dependencies of
   DLLs loaded by Python, so GEOS, PROJ etc. are not found even when they sit
   next to the extension. This plugin registers each extension's folder with
   os.add_dll_directory().

Both are done in the startup hook, which runs before any database connection
is opened.
"""

import os
import re

from datasette import hookimpl

# Keep the handles alive for the life of the process.
_dll_dir_handles = []


def _fix_drive_letter(extension):
    """Undo Datasette splitting "C:/path/ext.dll[:entrypoint]" at the drive colon."""
    if not isinstance(extension, tuple):
        return extension
    path, entrypoint = extension
    if not (re.fullmatch(r"[A-Za-z]", path) and entrypoint[:1] in ("/", "\\")):
        return extension
    full = f"{path}:{entrypoint}"
    # A real entrypoint may still follow, e.g. C:/x/ext.dll:sqlite3_ext_init
    head, sep, tail = full[2:].rpartition(":")
    if sep:
        return (full[:2] + head, tail)
    return full


@hookimpl
def startup(datasette):
    if os.name != "nt":
        return
    datasette.sqlite_extensions = [
        _fix_drive_letter(e) for e in datasette.sqlite_extensions
    ]
    for extension in datasette.sqlite_extensions:
        path = extension[0] if isinstance(extension, tuple) else extension
        folder = os.path.dirname(os.path.abspath(path))
        if os.path.isdir(folder):
            _dll_dir_handles.append(os.add_dll_directory(folder))
