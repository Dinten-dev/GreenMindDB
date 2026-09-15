"""Validate a proposal using temporary loopback ports and disposable TLS keys."""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
source = (
    Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "nginx.proposed.conf"
)
config = source.read_text()
config = re.sub(
    r"ssl_certificate\s+[^;]+;", f"ssl_certificate {root}/nginx-test-cert.pem;", config
)
config = re.sub(
    r"ssl_certificate_key\s+[^;]+;",
    f"ssl_certificate_key {root}/nginx-test-key.pem;",
    config,
)
config = re.sub(r"include /etc/letsencrypt/[^;]+;", "", config)
config = re.sub(r"ssl_dhparam\s+[^;]+;", "", config)
config = re.sub(r"access_log\s+[^;]+;", "access_log off;", config)
config = re.sub(r"error_log\s+[^;]+;", "error_log stderr;", config)
# nginx -t also checks listeners. Use temporary unprivileged loopback ports,
# never the production listeners or certificate keys.
port = 18440


def local_listener(match):
    global port
    port += 1
    flags = " ssl http2" if "ssl" in match.group(1).split() else ""
    return f"listen 127.0.0.1:{port}{flags};"


config = re.sub(r"listen\s+([^;]+);", local_listener, config)
complete = f"error_log stderr;\npid {root}/nginx-check.pid;\nevents {{}}\nhttp {{\n{config}\n}}\n"
path = root / "nginx-syntax-only.conf"
path.write_text(complete)
subprocess.run(["nginx", "-t", "-c", str(path), "-p", str(root)], check=True)
