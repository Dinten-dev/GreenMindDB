"""Generate a hash-guarded proposal without modifying the installed proxy."""

import hashlib
import json
import sys
from pathlib import Path

name = sys.argv[1]
assert name in ("staging", "production")
root = (
    Path(sys.argv[3]).resolve()
    if len(sys.argv) > 3
    else Path(__file__).resolve().parent
)
root.mkdir(parents=True, exist_ok=True)
production = name == "production"
source = Path(
    "/etc/nginx/sites-available/"
    + ("greenmind-prod" if production else "greenmind-staging")
)
if len(sys.argv) > 2:
    source = Path(sys.argv[2]).resolve()
old_front, api, front = (3000, 8004, 3004) if production else (3001, 8005, 3005)
original = source.read_text()
marker = "    # API → Backend directly"
assert original.count(marker) == 1 and "visualization-health" not in original
headers = """        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_redirect off;
        proxy_connect_timeout 3s;
        proxy_read_timeout 30s;
"""
added = f"""    # Authenticated visualization only; all ingestion remains on the old API.
    location = /visualization-health {{
        proxy_pass http://127.0.0.1:{api}/health;
{headers}    }}
    location ^~ /api/v1/visualization/ {{
        proxy_pass http://127.0.0.1:{api};
{headers}    }}
    location ~ ^/api/v1/sensors/[0-9a-fA-F-]+/(data|export)$ {{
        proxy_pass http://127.0.0.1:{api};
{headers}    }}
    # Open browsers may still request assets from the previous frontend build.
    location ^~ /_next/static/ {{
        proxy_pass http://127.0.0.1:{front};
        proxy_intercept_errors on;
        error_page 404 = @visual_previous_assets;
{headers}    }}
    location @visual_previous_assets {{
        proxy_pass http://127.0.0.1:{old_front};
{headers}    }}

"""
updated = original.replace(marker, added + marker)
needle = f"proxy_pass http://127.0.0.1:{old_front};"
# Replace only the original frontend block, preserving fallback above.
position = updated.index("    # Frontend (Next.js")
before, after = updated[:position], updated[position:]
assert after.count(needle) == 1
updated = before + after.replace(needle, f"proxy_pass http://127.0.0.1:{front};")
(root / "nginx.before.conf").write_text(original)
(root / "nginx.proposed.conf").write_text(updated)
(root / "proxy-manifest.json").write_text(
    json.dumps(
        {
            "target": str(source),
            "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "after_sha256": hashlib.sha256(updated.encode()).hexdigest(),
            "environment": name,
            "api_port": api,
            "frontend_port": front,
        },
        indent=2,
    )
    + "\n"
)
print(f"{name}: proxy proposal generated; installed configuration unchanged")
