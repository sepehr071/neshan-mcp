import base64
import json

import httpx
import pytest
from conftest import FIXTURES, raw, serve, wrapped

from neshan_mcp import codec, http

pytestmark = pytest.mark.anyio

P = "/maps/pwa-api"
RECORDED = sorted(p.name for p in (FIXTURES / "raw").iterdir())
NEW_KEY = b"https://example.org/k"


def xor(value, key: bytes) -> str:
    raw_bytes = json.dumps(value, ensure_ascii=False).encode()
    return base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw_bytes))).decode()


@pytest.mark.parametrize("name", RECORDED)
def test_every_recorded_answer_decodes(name):
    value = codec.decode(raw(name))
    assert isinstance(value, dict | list)
    if name == "transit.json":
        options = codec.unxor(value["data"])
        assert len(options) == 4 and options[0][1]["lineAlias"] == "خط 370"


def test_shapes_are_told_apart():
    assert codec.decode(raw("route_eta.json"))["res"]["error"] == 0  # plain JSON
    assert codec.decode(raw("reverse.txt"))["state"] == "استان تهران"  # wrapped, as served
    assert codec.decode(base64.b64encode(b'{"bare": 1}').decode()) == {"bare": 1}  # bare base64 JSON


@pytest.mark.parametrize("filler", [0, 12, 35, 60])
def test_other_filler_lengths_are_found_and_learned(filler):
    scheme = codec.Scheme()
    value = {"items": [{"title": "برج میلاد"}], "n": list(range(50))}
    assert codec.decode(wrapped(value, filler), scheme) == value
    assert scheme.filler == filler
    assert codec.decode(wrapped(value, filler), scheme) == value  # now the fast path


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "<!DOCTYPE html><html><body>403 Forbidden</body></html>",
        '{"broken": ',
        "x" * 300,
        "A" * 28 + "!!!!not-base64!!!!@3" + "B" * 28,
        "A" * 28 + base64.b64encode(b"plain text, not json").decode() + "@0" + "B" * 28,
    ],
)
def test_unknown_answers_raise_instead_of_returning_garbage(text):
    with pytest.raises(codec.DecodeError):
        codec.decode(text)


def test_wrong_xor_key_is_detected():
    data = xor([[{"section": {"type": "walk"}}]], NEW_KEY)
    with pytest.raises(codec.DecodeError):
        codec.unxor(data)
    assert codec.unxor(data, codec.Scheme(key=NEW_KEY)) == [[{"section": {"type": "walk"}}]]
    with pytest.raises(codec.DecodeError):
        codec.unxor(None)


# Minimal copies of the site's own code (checked against the real bundles on 2026-10-31).
ENTRY_JS = (
    "function o(e,t=!0){try{if(!e)return;let i=e.slice(28);i=i.slice(0,i.length-28);"
    'const o=i.slice(i.lastIndexOf("@")+1);i=i.slice(0,i.lastIndexOf("@"));const n=i.slice(0,o);'
)
RUNTIME_JS = (
    'r.u=e=>e+"-"+{46:"53f89f9ff64bb26bdf98",9719:"c08a9e469f739be35e93"}[e]+".js",'
    'r.miniCssF=e=>"css/"+{9719:"697d1866101a56e61cc7"}[e]+".css"'
)


def key_js(key: bytes) -> str:
    return f'const i=new Int8Array((new TextEncoder).encode("{key.decode()}")),n=t.length,o=new Int8Array(n);'


def test_scheme_is_read_from_the_sites_js():
    found = codec.scheme_from_js([ENTRY_JS, key_js(NEW_KEY)])
    assert (found.filler, found.key, found.source) == (28, NEW_KEY, "site JS")
    assert codec.scheme_from_js(["nothing here"]) is None
    assert codec.chunk_urls(RUNTIME_JS) == ["https://neshan.org/maps/9719-c08a9e469f739be35e93.js"]  # not the CSS


def fake_site(api, key: bytes = NEW_KEY):
    """Serve the web map's page and scripts so refresh_scheme can read them."""
    page = '<script defer="defer" src="https://neshan.org/maps/9121-aaaa1111.js"></script>'
    page += '<script defer="defer" src="https://neshan.org/maps/3524-bbbb2222.js"></script>'
    api["/maps"] = lambda r: httpx.Response(200, text=page)
    api["/maps/9121-aaaa1111.js"] = lambda r: httpx.Response(200, text=RUNTIME_JS)
    api["/maps/3524-bbbb2222.js"] = lambda r: httpx.Response(200, text=ENTRY_JS)
    api["/maps/9719-c08a9e469f739be35e93.js"] = lambda r: httpx.Response(200, text=key_js(key))


async def test_changed_xor_key_is_read_from_the_site_and_the_call_retried(client, api):
    fake_site(api)
    options = codec.unxor(codec.decode(raw("transit.json"))["data"])
    api[f"{P}/bus-routing/"] = lambda r: httpx.Response(200, json={"code": 0, "data": xor(options, NEW_KEY)})
    r = await client.call_tool("ns_transit_route", {"origin": "35.6997,51.3380", "destination": "35.7448,51.3753"})
    assert not r.is_error, r.content[0].text
    assert r.structured_content["options"][0]["fare_toman"] == 4660
    assert codec.SCHEME.key == NEW_KEY and codec.SCHEME.source == "site JS"
    paths = [c.url.path for c in api.calls]
    assert paths.count(f"{P}/bus-routing/") == 2 and "/maps" in paths
    page = next(c for c in api.calls if c.url.path == "/maps")
    assert page.headers["sec-fetch-mode"] == "navigate"


async def test_undecodable_answer_is_a_clear_error_after_one_retry(client, api):
    fake_site(api, codec.XOR_KEY)
    api[f"{P}/location-to-address"] = lambda r: httpx.Response(200, text="Zm9v" * 40)
    r = await client.call_tool("ns_reverse_geocode", {"location": "35.7448,51.3753"})
    assert r.is_error and "does not recognise" in r.content[0].text
    assert [c.url.path for c in api.calls].count(f"{P}/location-to-address") == 2
    before = len(api.calls)
    r = await client.call_tool("ns_reverse_geocode", {"location": "35.7449,51.3753"})
    assert r.is_error
    assert not any(c.url.path == "/maps" for c in api.calls[before:])  # the site's JS is read at most every 10 min


async def test_html_error_page_with_status_200_is_not_data(client, api):
    fake_site(api, codec.XOR_KEY)
    api[f"{P}/location-to-address"] = lambda r: httpx.Response(200, text="<!DOCTYPE html><html>403</html>")
    r = await client.call_tool("ns_reverse_geocode", {"location": "35.7448,51.3753"})
    assert r.is_error and "HTML" in r.content[0].text


async def test_site_unreachable_during_refresh_keeps_the_known_scheme(client, api):
    api[f"{P}/location-to-address"] = lambda r: httpx.Response(200, text="Zm9v" * 40)  # /maps answers 404
    r = await client.call_tool("ns_reverse_geocode", {"location": "35.7448,51.3753"})
    assert r.is_error and codec.SCHEME.source == "built-in" and codec.SCHEME.filler == 28


async def test_empty_204_answer_is_no_data(client, api):
    api[f"{P}/weather/air-quality/kish/summary"] = lambda r: httpx.Response(204)
    r = await client.call_tool("ns_air_quality", {"place": "Kish"})
    assert r.is_error and "no air-quality data" in r.content[0].text
    assert len(api.calls) == 1  # empty is not a decoding failure


async def test_unwrap_helper_matches_codec():
    assert http.unwrap(raw("reverse.txt")) == codec.decode(raw("reverse.txt"))
    assert serve("reverse.txt")(None).status_code == 200
