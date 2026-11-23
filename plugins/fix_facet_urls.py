"""
Plugin to fix facet URLs when behind a reverse proxy (e.g., Codespace tunnels).

This plugin wraps the absolute_url method to use X-Forwarded-* headers or 
environment variables to determine the correct public hostname.
"""
import os
from datasette import hookimpl


@hookimpl
def startup(datasette):
    """Patch absolute_url to handle reverse proxy headers."""
    original_absolute_url = datasette.absolute_url

    def fixed_absolute_url(request, path):
        # First get the original URL
        url = original_absolute_url(request, path)
        
        # Check for X-Forwarded-Host header (standard reverse proxy header)
        forwarded_host = None
        for name, value in request.scope.get("headers") or []:
            if name.lower() == b"x-forwarded-host":
                forwarded_host = value.decode("latin1")
                break
        
        # Also check environment variable for explicit hostname
        env_host = os.environ.get("DATASETTE_HOST")
        
        if env_host or forwarded_host:
            # Need to replace the host in the URL
            corrected_host = env_host or forwarded_host
            
            # Replace the hostname in the URL
            # URL format: scheme://host[:port]/path?query
            if url.startswith("http://"):
                scheme_end = 7
                scheme = "http"
            elif url.startswith("https://"):
                scheme_end = 8
                scheme = "https"
            else:
                return url
            
            # Find the end of the host part (next / or ? or end of string)
            rest = url[scheme_end:]
            host_end = len(rest)
            for i, char in enumerate(rest):
                if char in "/?":
                    host_end = i
                    break
            
            # Reconstruct URL with corrected host
            rest_of_url = rest[host_end:]
            url = f"{scheme}://{corrected_host}{rest_of_url}"
        
        return url

    # Replace the method on the datasette instance
    datasette.absolute_url = fixed_absolute_url
