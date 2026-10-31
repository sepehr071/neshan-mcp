"""Decoding of the neshan.org web map's scrambled answers.

The web map's API (pwa-api) answers in three shapes, all checked against the site's own JS on 2026-10-31:

- plain JSON (routes);
- wrapped text (most endpoints): <F filler chars><base64 cut at N, second part first>@N<F filler chars>.
  The site's decoder (bundle module 32165) drops F=28 chars at each end, takes N after the last "@" and
  base64-decodes rest[N:] + rest[:N];
- a JSON object whose `data` string is base64 of the real JSON XOR-ed with a fixed key (transit, chunk 9719:
  the bytes of "https://rajman.org").

`Scheme` holds the values the site's JS uses. It starts with the known values; `scheme_from_bundle` reads them
again from the site's current JS when an answer stops decoding (see http.fetch).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

FILLER = 28  # known filler length on each side (bundle module 32165)
XOR_KEY = b"https://rajman.org"  # known transit key (bundle chunk 9719)
MAX_FILLER = 64  # how far the fallback search looks when the known filler length fails

# Patterns in the site's JS that carry the values above.
FILLER_JS = re.compile(
    r"\.slice\((\d+)\);\w+=\w+\.slice\(0,\w+\.length-(\d+)\);const \w+=\w+\.slice\(\w+\.lastIndexOf\(\"@\"\)\+1\)"
)
XOR_JS = re.compile(r"Int8Array\(\(new TextEncoder\)\.encode\(\"([^\"]{4,64})\"\)\)")


class DecodeError(ValueError):
    """The answer is in none of the known shapes, or decodes to something that is not JSON."""


@dataclass
class Scheme:
    filler: int = FILLER
    key: bytes = XOR_KEY
    source: str = "built-in"  # or "site JS"


SCHEME = Scheme()


def _json(raw: bytes) -> Any:
    """Parse decoded bytes as JSON; anything else (garbage from a wrong key or cut) is a DecodeError."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DecodeError("decoded bytes are not UTF-8") from e
    if text[:1] not in ("{", "["):
        raise DecodeError("decoded text is not JSON")
    try:
        return json.loads(text)
    except ValueError as e:
        raise DecodeError("decoded text is not valid JSON") from e


def _b64(text: str) -> bytes:
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
    except (binascii.Error, ValueError) as e:
        raise DecodeError("not base64") from e


def _unwrap(s: str, filler: int) -> Any:
    """Undo the wrapping with a given filler length, the way the site's JS does."""
    if len(s) <= 2 * filler + 2:
        raise DecodeError("too short to be wrapped")
    inner = s[filler : len(s) - filler] if filler else s
    at = inner.rfind("@")
    if at < 0 or not inner[at + 1 :].isdigit():
        raise DecodeError("no @N marker")
    cut, body = int(inner[at + 1 :]), inner[:at]
    if cut > len(body):
        raise DecodeError("cut beyond the body")
    return _json(_b64(body[cut:] + body[:cut]))


def _search_wrap(s: str) -> tuple[Any, int]:
    """Fallback when the known filler length fails: try every filler length up to MAX_FILLER.

    Returns the value and the filler length that worked. A cheap check on the first base64 block
    (must decode to '{' or '[') keeps this fast on large answers.
    """
    for filler in range(0, min(MAX_FILLER, len(s) // 2) + 1):
        inner = s[filler : len(s) - filler] if filler else s
        at = inner.rfind("@")
        if at < 0 or not inner[at + 1 :].isdigit():
            continue
        cut, body = int(inner[at + 1 :]), inner[:at]
        raw = body[cut:] + body[:cut]
        try:
            head = base64.b64decode(raw[:4], validate=True)
        except (binascii.Error, ValueError):
            continue
        if head[:1] not in (b"{", b"["):
            continue
        try:
            return _json(_b64(raw)), filler
        except DecodeError:
            continue
    raise DecodeError("not wrapped with any filler length")


def decode(text: str, scheme: Scheme = SCHEME) -> Any:
    """Turn one pwa-api answer into JSON, detecting its shape. Raises DecodeError for anything unknown.

    Order: plain JSON; wrapped with the scheme's filler; wrapped with any filler (then the scheme learns it);
    bare base64 JSON. HTML (a CDN error page) is never decoded.
    """
    s = text.strip()
    if not s:
        raise DecodeError("empty answer")
    if s[0] in "{[":
        try:
            return json.loads(s)
        except ValueError as e:
            raise DecodeError("broken JSON") from e
    if s[0] == "<":
        raise DecodeError("an HTML page instead of data")
    try:
        return _unwrap(s, scheme.filler)
    except DecodeError:
        pass
    try:
        value, filler = _search_wrap(s)
    except DecodeError:
        return _json(_b64(s))  # last shape: plain base64 of JSON (not seen yet; raises DecodeError if not)
    scheme.filler = filler
    return value


def unxor(data: Any, scheme: Scheme = SCHEME) -> Any:
    """Decode a transit `data` string: base64, XOR with the scheme's key, JSON."""
    if not isinstance(data, str) or not data:
        raise DecodeError("no encoded data")
    raw = _b64(data)
    key = scheme.key
    return _json(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)))


def scheme_from_js(sources: list[str]) -> Scheme | None:
    """Read the filler length and XOR key from the site's JS; None when neither is found."""
    filler = key = None
    for js in sources:
        if filler is None and (m := FILLER_JS.search(js)) and m.group(1) == m.group(2):
            filler = int(m.group(1))
        if key is None and (m := XOR_JS.search(js)):
            key = m.group(1).encode("utf-8")
    if filler is None and key is None:
        return None
    return Scheme(filler if filler is not None else FILLER, key or XOR_KEY, "site JS")


SCRIPT_SRC = re.compile(r'<script[^>]+src="(https://neshan\.org/maps/[0-9]+-[0-9a-f]+\.js)"')
CHUNK_MAP = re.compile(r"(\d+):\"([0-9a-f]{20})\"")
JS_MAP = re.compile(r"\{([^{}]*)\}\[\w+\]\+\"\.js\"")
# Lazy chunks to read for the XOR key, by id (9719 held the transit code on 2026-10-31). Ids stay stable
# across builds unless the code moves; the hashes are read from the runtime chunk map.
KEY_CHUNKS = ("9719",)


def chunk_urls(runtime_js: str, ids: tuple[str, ...] = KEY_CHUNKS) -> list[str]:
    """URLs of the given lazy chunks, from the webpack runtime's `{id: "hash"}[e] + ".js"` map (not the CSS one)."""
    m = JS_MAP.search(runtime_js)
    found = dict(CHUNK_MAP.findall(m.group(1))) if m else {}
    return [f"https://neshan.org/maps/{i}-{found[i]}.js" for i in ids if i in found]
