"""Shared task-host boundary for browser execution and model candidate filtering."""

import ipaddress
from urllib.parse import urlsplit


def is_loopback_host(host):
    """Recognize local endpoints without resolving or widening their hostnames."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
        mapped = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped and mapped.is_loopback)
    except ValueError:
        return False


def normalized_host(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return ""
        host = (parsed.hostname or "").lower().rstrip(".").encode("idna").decode("ascii")
        try:
            address = ipaddress.ip_address(host)
            mapped = getattr(address, "ipv4_mapped", None)
            # Chrome's URL.hostname uses hex for mapped IPv6; some Python
            # versions stringify these addresses with a dotted IPv4 suffix.
            if mapped:
                return f"::ffff:{int(mapped) >> 16:x}:{int(mapped) & 0xffff:x}"
            return address.compressed
        except ValueError:
            return host if is_loopback_host(host) else host.removeprefix("www.")
    except (ValueError, UnicodeError, AttributeError):
        return ""


def scope_host(url):
    """Local apps are host-and-port scoped; public sites retain hostname scope."""
    host = normalized_host(url)
    if not host:
        return ""
    if not is_loopback_host(host):
        return host
    try:
        parsed = urlsplit(url)
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return ""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def host_allowed(url, allowed_hosts):
    """Local scope keys match exactly; public hosts permit only their own children."""
    host = normalized_host(url)
    key = scope_host(url)
    if not key:
        return False
    local = is_loopback_host(host)
    for base in allowed_hosts:
        if not isinstance(base, str) or not base:
            continue
        base_url = "http://" + base
        base_host = normalized_host(base_url)
        if is_loopback_host(base_host):
            try:
                if local and urlsplit(base_url).port is not None and key == scope_host(base_url):
                    return True
            except ValueError:
                pass
        elif not local and base_host and (host == base_host or host.endswith("." + base_host)):
            return True
    return False
