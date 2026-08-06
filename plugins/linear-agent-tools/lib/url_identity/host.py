"""Canonical DNS and IP host identities."""

from __future__ import annotations

import ipaddress
import re

_DNS_HOST_PATTERN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_HOST_LABEL_PATTERN = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)")


class UrlHostError(ValueError):
    """Report one ambiguous or unsupported URL host."""


def canonical_host_get(value: str, *, ipv6_allowed: bool) -> str:
    """Return one lowercase canonical DNS, IPv4, or optional IPv6 host.

    Args:
        value: Parsed hostname without IPv6 brackets.
        ipv6_allowed: Whether canonical IPv6 literals are supported.

    Returns:
        Canonical host text.
    """

    if not isinstance(value, str) or not value or not value.isascii() or len(value) > 253:
        raise UrlHostError("URL host is malformed")
    host = value.lower()
    if ":" in host:
        if not ipv6_allowed or "%" in host:
            raise UrlHostError("URL host is malformed")
        try:
            return ipaddress.IPv6Address(host).compressed
        except ipaddress.AddressValueError as error:
            raise UrlHostError("URL host is malformed") from error
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        if _DNS_HOST_PATTERN.fullmatch(host) is None or all(
            _NUMERIC_HOST_LABEL_PATTERN.fullmatch(label) is not None for label in host.split(".")
        ):
            raise UrlHostError("URL host is malformed")
        return host
    if str(address) != host:
        raise UrlHostError("URL host is malformed")
    return host
