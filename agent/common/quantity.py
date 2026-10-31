"""Kubernetes memory quantities.

Binary suffixes (Ki..Ei) are powers of 1024. Decimal suffixes (k, M, G, T, P, E)
are powers of 1000. Lowercase m is milli, not mega.
"""

_SUFFIXES = (
    ("Ei", 1024 ** 6),
    ("Pi", 1024 ** 5),
    ("Ti", 1024 ** 4),
    ("Gi", 1024 ** 3),
    ("Mi", 1024 ** 2),
    ("Ki", 1024),
    ("E", 1000 ** 6),
    ("P", 1000 ** 5),
    ("T", 1000 ** 4),
    ("G", 1000 ** 3),
    ("M", 1000 ** 2),
    ("k", 1000),
    ("m", 0.001),
)


def parse_memory_bytes(value):
    """Return the quantity in bytes, or None when it is not a memory quantity."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for suffix, factor in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            number = text[: -len(suffix)].strip()
            try:
                return float(number) * factor
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None
