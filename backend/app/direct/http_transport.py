"""Reuse the verified trust store, never a viewer's cookies or authorization.

HTTPX otherwise loads the complete CA bundle for every new client/transport,
including HTTP over the private Unix socket. Clients remain request-scoped so
dashboard response cookies cannot cross viewers and access is rechecked each time.
"""

from functools import lru_cache

import httpx


@lru_cache(maxsize=1)
def verified_tls_context():
    return httpx.create_ssl_context(verify=True, trust_env=False)
