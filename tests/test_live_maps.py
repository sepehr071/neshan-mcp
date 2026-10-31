import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from neshan_mcp import codec, http
from neshan_mcp.maps import CITIES

pytestmark = [pytest.mark.live, pytest.mark.anyio]

AZADI, MILAD = "35.6997,51.3380", "35.7448,51.3753"
PROVINCES = {
    "tehran": "تهران",
    "mashhad": "خراسان رضوی",
    "isfahan": "اصفهان",
    "shiraz": "فارس",
    "tabriz": "آذربایجان شرقی",
    "karaj": "البرز",
    "qom": "قم",
    "ahvaz": "خوزستان",
    "kermanshah": "کرمانشاه",
    "rasht": "گیلان",
    "yazd": "یزد",
    "kerman": "کرمان",
    "urmia": "آذربایجان غربی",
    "hamadan": "همدان",
    "arak": "مرکزی",
    "ardabil": "اردبیل",
    "bandar abbas": "هرمزگان",
    "qazvin": "قزوین",
    "zanjan": "زنجان",
    "sari": "مازندران",
    "gorgan": "گلستان",
    "zahedan": "سیستان و بلوچستان",
    "kish": "هرمزگان",
}


async def test_live_search_place_and_reviews(client):
    r = (await client.call_tool("ns_search", {"query": "برج میلاد"})).structured_content
    milad = next(x for x in r["results"] if x["name"] == "برج میلاد")
    assert abs(milad["lat"] - 35.745) < 0.01 and abs(milad["lng"] - 51.3755) < 0.01
    p = (await client.call_tool("ns_place", {"place_id": milad["id"]})).structured_content
    assert p["name"] == "برج میلاد" and p["votes"] > 1000 and len(p["hours"]) == 7
    rv = (await client.call_tool("ns_reviews", {"place_id": milad["id"]})).structured_content
    assert rv["count"] == 10 and all(1 <= x.get("rating", 1) <= 5 for x in rv["reviews"])


async def test_live_search_in_another_city(client):
    r = (await client.call_tool("ns_search", {"query": "حافظیه", "near": "شیراز", "limit": 3})).structured_content
    assert r["results"] and all(abs(x["lat"] - 29.62) < 0.2 for x in r["results"])


async def test_live_reverse_geocode(client):
    r = (await client.call_tool("ns_reverse_geocode", {"location": MILAD})).structured_content
    assert "همت" in r["address"] and r["province"] == "استان تهران"


async def test_live_city_centres_lie_in_their_province(client):
    for city, province in PROVINCES.items():
        lat, lng = CITIES[city]
        r = (await client.call_tool("ns_reverse_geocode", {"location": f"{lat},{lng}"})).structured_content
        assert province in r["province"], (city, r)
        await asyncio.sleep(1)  # polite: one request a second


@pytest.mark.parametrize("mode", ["car", "motorcycle", "bicycle", "walk"])
async def test_live_route(client, mode):
    r = (await client.call_tool("ns_route", {"origin": AZADI, "destination": MILAD, "mode": mode})).structured_content
    first = r["routes"][0]
    assert 7 < first["distance_km"] < 16 and 5 < first["duration_min"] < 240
    assert first["steps"] and (first.get("steps_more") or first["steps"][-1]["do"] in ("مقصد", "به مقصد رسیدید"))


async def test_live_route_avoiding_the_traffic_zone(client):
    args = {"origin": "35.6880,51.3600", "destination": "35.6900,51.4800", "steps": False}
    plain = (await client.call_tool("ns_route", args)).structured_content
    avoid = (await client.call_tool("ns_route", {**args, "avoid_traffic_zone": True})).structured_content
    assert avoid["routes"][0]["distance_km"] > plain["routes"][0]["distance_km"]


async def test_live_transit(client):
    r = (await client.call_tool("ns_transit_route", {"origin": AZADI, "destination": MILAD})).structured_content
    assert r["options"] and any(leg.get("ride") for o in r["options"] for leg in o["legs"])
    assert all(o["fare_toman"] > 0 for o in r["options"])


async def test_live_search_filters(client):
    args = {"query": "داروخانه", "near": "35.745005,51.375503", "open_now": True, "min_rating": 4}
    r = (await client.call_tool("ns_search", args)).structured_content
    assert r["results"] and all(
        x["rating"] >= 4 and x["status"] in ("باز است", "شبانه\u200cروزی") for x in r["results"]
    )


async def test_live_photos_and_featured_reviews(client):
    p = (await client.call_tool("ns_place", {"place_id": "581b3cd78fffe971eb3b591a20967e72"})).structured_content
    assert len(p["photos"]) == 5 and {"id": "menus", "title": "منو"} in p["photo_categories"]
    menu = (await client.call_tool("ns_photos", {"place_id": p["id"], "album": "menus"})).structured_content
    assert menu["photos"] and menu["photos"][0]["url"].startswith("https://static.neshanmap.ir/")
    rv = (await client.call_tool("ns_reviews", {"place_id": p["id"], "sort": "featured"})).structured_content
    assert rv["count"] >= 3 and rv["reviews"][0]["likes"] > 10


async def test_live_air_quality(client):
    r = (await client.call_tool("ns_air_quality", {"place": "Isfahan"})).structured_content
    assert r["city"] == "اصفهان" and 0 < r["aqi"] < 500 and r["stations"]
    r = (await client.call_tool("ns_air_quality", {"place": MILAD})).structured_content
    assert 0 < r["aqi"] < 500


async def test_live_zone_check(client):
    inside = (await client.call_tool("ns_zone_check", {"location": "35.6950,51.4200"})).structured_content
    odd_even = (await client.call_tool("ns_zone_check", {"location": "35.7400,51.4300"})).structured_content
    outside = (await client.call_tool("ns_zone_check", {"location": MILAD})).structured_content
    assert (inside["in_traffic_zone"], inside["in_odd_even_zone"]) == (True, True)
    assert (odd_even["in_traffic_zone"], odd_even["in_odd_even_zone"]) == (False, True)
    assert (outside["in_traffic_zone"], outside["in_odd_even_zone"]) == (False, False)


async def test_live_travel_times(client):
    args = {"origin": AZADI, "destinations": [MILAD, "35.7270,51.3793", "35.7575,51.4097"]}
    r = (await client.call_tool("ns_travel_times", args)).structured_content
    times = [x["duration_min"] for x in r["results"]]
    assert len(times) == 3 and times == sorted(times) and not r.get("failed")


async def test_live_route_with_stop_and_departure_time(client):
    r = (
        await client.call_tool(
            "ns_route", {"origin": AZADI, "destination": MILAD, "stops": ["35.7270,51.3793"], "steps": False}
        )
    ).structured_content
    assert " | " in r["routes"][0]["via"] and r["routes"][0]["distance_km"] > 10
    tomorrow = (datetime.now(timezone(timedelta(hours=3, minutes=30))) + timedelta(days=1)).strftime("%Y-%m-%d")
    r = (
        await client.call_tool("ns_route", {"origin": AZADI, "destination": MILAD, "depart_at": f"{tomorrow} 08:00"})
    ).structured_content
    assert r["routes"][0]["arrive_at"].startswith(f"{tomorrow} 08:")
    r = (
        await client.call_tool("ns_route", {"origin": AZADI, "destination": MILAD, "arrive_by": f"{tomorrow} 09:00"})
    ).structured_content
    assert r["routes"][0]["leave_by"].startswith(f"{tomorrow} 08:")


async def test_live_site_js_still_holds_the_known_scheme():
    found = await http.site_scheme()
    assert found is not None and (found.filler, found.key) == (codec.FILLER, codec.XOR_KEY)


async def test_live_nearby(client):
    r = await client.call_tool("ns_nearby", {"place_id": "581b3cd78fffe971eb3b591a20967e72"})
    if r.is_error:  # Neshan answers 403 outside Iran (e.g. through a foreign NESHAN_MCP_PROXY)
        assert "only to connections from Iran" in r.content[0].text
        return
    groups = r.structured_content["groups"]
    assert len(groups) >= 3 and all(p["distance_m"] < 5000 for g in groups for p in g["places"])
