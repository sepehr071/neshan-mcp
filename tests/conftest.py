import base64
import json
import random
from pathlib import Path

import httpx
import pytest
from mcp import Client

from neshan_mcp import http
from neshan_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def raw(name: str) -> str:
    """A recorded neshan.org answer, as served (wrapped text, plain JSON or JSON with XOR data)."""
    return (FIXTURES / "raw" / name).read_text(encoding="utf-8")


def wrapped(obj, filler: int = 28) -> str:
    """Wrap a JSON value the way neshan.org's pwa-api does: filler + swapped base64 + @N + filler."""
    b = base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode().rstrip("=")
    n = random.randint(1, len(b) - 1)
    body = b[len(b) - n :] + b[: len(b) - n]  # decode computes body[n:] + body[:n]
    pad = "".join(random.choices("ABCDEFabcdef0123456789", k=filler))
    return pad + body + f"@{n}" + pad.lower()[::-1].replace("0", "x") if filler else body + f"@{n}"


def serve(name: str):
    """Route handler answering with a recorded fixture."""
    text = raw(name)
    return lambda request: httpx.Response(200, text=text)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api():
    """Route table for a fake upstream: api["/path"] = JSON body, or a callable(request) -> httpx.Response.

    Keys match the URL path only (host ignored). Unknown paths return 404. Every
    request is appended to api.calls so tests can assert on query params.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, json={"error": "no fake route"})
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
