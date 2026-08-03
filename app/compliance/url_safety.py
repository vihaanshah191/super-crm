"""SSRF / unsafe-URL guards applied before any outbound collection request.

Every URL an adapter or the ScraplingCollector fetches -- including redirect
targets -- must pass through assert_safe_url() first. This is a defense-in-depth
control, not a substitute for the source_policy compliance gate (which decides
*whether* a source may be collected at all).
"""

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    pass


def _is_private_or_reserved(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    """Raises UnsafeURLError if `url` is not a plain public http(s) URL.

    Blocks: non-http(s) schemes, missing hostnames, credentials embedded in
    the URL, and hostnames that resolve to private/loopback/link-local
    addresses (RFC1918, 127.0.0.0/8, 169.254.0.0/16 including the cloud
    metadata endpoint, etc).
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Disallowed URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeURLError("URL has no hostname")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")

    try:
        addrinfos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve hostname: {parsed.hostname!r}") from exc

    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        if _is_private_or_reserved(ip_str):
            raise UnsafeURLError(
                f"URL {url!r} resolves to a private/reserved address ({ip_str}); refusing to fetch"
            )
