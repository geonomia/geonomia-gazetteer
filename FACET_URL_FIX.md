# Datasette Facet URL Fix

## Problem

Datasette has a bug where facet links are generated using `absolute_url()`, which uses `request.url` to construct full URLs. When accessed through a reverse proxy (like GitHub Codespaces tunnels), this causes facet links to incorrectly use the forwarded localhost address instead of the actual public hostname.

**Before fix:**
```html
<a href="http://localhost:8001/geonames/geonames?_facet=feature_class">feature_class</a>
```

**After fix:**
```html
<a href="https://humble-space-yodel-jqg6p4qqj4cg74-8001.app.github.dev/geonames/geonames?_facet=feature_class">feature_class</a>
```

Other internal links correctly use relative paths and work fine.

## Solution

The fix is implemented as a Datasette plugin (`plugins/fix_facet_urls.py`) that wraps the `absolute_url()` method to use reverse proxy headers or environment variables to determine the correct public hostname.

## Usage

### Option 1: Environment Variable (Simplest)

Set the `DATASETTE_HOST` environment variable when starting the server:

```bash
DATASETTE_HOST="humble-space-yodel-jqg6p4qqj4cg74-8001.app.github.dev" \
  python -m datasette serve data/geonames.db \
  --host 0.0.0.0 \
  --port 8001 \
  --plugins-dir plugins
```

Or in the Makefile:

```makefile
run: data/geonames.db | $(VENV_SENTINEL)
	DATASETTE_HOST="your-codespace-host" $(PYTHON) -m datasette serve $< \
	  --host 0.0.0.0 \
	  --port 8001 \
	  --plugins-dir plugins
```

### Option 2: X-Forwarded-Host Header (Reverse Proxy)

If you're behind a reverse proxy that sets the `X-Forwarded-Host` header, the plugin will automatically use it:

```bash
# When behind a proxy that sends X-Forwarded-Host
python -m datasette serve data/geonames.db \
  --host 0.0.0.0 \
  --port 8001 \
  --plugins-dir plugins
```

### Priority

The plugin checks for the correct host in this order:
1. `DATASETTE_HOST` environment variable (highest priority)
2. `X-Forwarded-Host` header (from reverse proxy)
3. Default hostname from request (fallback)

## How It Works

The plugin's `startup` hook patches Datasette's `absolute_url()` method to:

1. Call the original method to get the full URL
2. Check for `DATASETTE_HOST` environment variable
3. Check for `X-Forwarded-Host` HTTP header
4. If either is found, replace the hostname in the URL while keeping the path and query string
5. Return the corrected URL

This ensures all Datasette-generated absolute URLs (including facet links) use the correct public hostname.

## Files

- `plugins/fix_facet_urls.py` - The plugin implementation
- `Makefile` - Updated `run` target to load the plugin via `--plugins-dir`
