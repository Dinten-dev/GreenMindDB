"""Packaged copy-only launcher with transport reuse enabled."""
import os
import runpy
from pathlib import Path

os.environ['RAW_ARCHIVE_REUSE_SSH'] = 'true'
# Preserve configured checks and additionally guard the live Gateway route.
urls = [url.strip() for url in os.environ.get('RAW_ARCHIVE_HEALTH_URLS', '').split(',') if url.strip()]
if not urls:
    raise RuntimeError('Configured receiver health checks are required')
gateway_health = 'http://127.0.0.1:8120/health'
if gateway_health not in urls:
    urls.append(gateway_health)
os.environ['RAW_ARCHIVE_HEALTH_URLS'] = ','.join(urls)
runpy.run_path(str(Path(__file__).with_name('adaptive-copy.py')), run_name='__main__')
