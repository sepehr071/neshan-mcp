import base64
import json
import urllib.parse

import httpx
import pytest
from conftest import serve

from neshan_mcp import http, maps, routes

pytestmark = pytest.mark.anyio

P = "/maps/pwa-api"
GISHA = "c861e2992dd4cca5f1185070daf27d21"
MILAD = "581b3cd78fffe971eb3b591a20967e72"
AZADI, TOWER, NASR = "35.6997,51.3380", "35.7448,51.3753", "35.7270,51.3793"
TOOLS = {
    "ns_search",
    "ns_place",
    "ns_reviews",
    "ns_photos",
    "ns_nearby",
    "ns_reverse_geocode",
    "ns_air_quality",
    "ns_route",
    "ns_travel_times",
    "ns_zone_check",
    "ns_transit_route",
}


def search_body(request: httpx.Request) -> dict:
    return json.loads(urllib.parse.unquote(base64.b64decode(request.url.params["body"]).decode()))


def route_points(request: httpx.Request) -> list:
    v = request.url.params["value"]
    return json.loads(base64.b64decode(v[:5] + v[-5:] + v[5:-5]))


async def test_server_starts_and_lists_read_only_tools(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        first = t.description.splitlines()[0]
        assert t.annotations.read_only_hint is True and t.title, t.name
        assert first.endswith(".") and len(first) < 125, t.name  # a complete one-line summary for the catalog
        assert "title" not in json.dumps(t.input_schema), t.name


# --- search ---------------------------------------------------------------------------------------------


async def test_search_drops_ads_and_reads_each_place(client, api):
    api[f"{P}/neshan-search"] = serve("search.txt")
    r = (await client.call_tool("ns_search", {"query": "داروخانه", "near": "35.745005,51.375503"})).structured_content
    names = [x["name"] for x in r["results"]]
    assert "داروخانه مسیحا دکتر سحر یوسفیان" not in names and r["count"] == 8  # the sponsored first result
    first = r["results"][0]
    assert first["name"] == "داروخانه اورژانس بیمارستان میلاد" and first["id"] == "7b932afebf87917c06e9122b360af946"
    assert (first["rating"], first["votes"], first["phone"]) == (4.0, 5, "02184090")
    assert first["address"] == "بزرگراه حکیم، میدان دکتر کامل شادپور (کوی نصر)" and 0 < first["distance_m"] < 2000


async def test_search_sends_the_web_maps_request(client, api):
    api[f"{P}/neshan-search"] = serve("search.txt")
    await client.call_tool("ns_search", {"query": "داروخانه", "near": "Shiraz"})
    req = api.calls[0]
    body = search_body(req)
    assert body["term"] == "داروخانه" and body["center"] == {"x": 52.5311, "y": 29.61} and body["limit"] == 30
    assert body["filters"] == {} and body["uuid"] == req.headers["uuid"] and req.headers["uuid"].startswith("web_")
    assert req.headers["x-client-version"] == http.CLIENT_VERSION and req.url.params["search-in-bound"] == "false"
    assert req.headers["sec-fetch-site"] == "same-origin" and "origin" not in req.headers
    assert req.headers["accept"] == "*/*" and "authorization" not in req.headers


@pytest.mark.parametrize(
    "args, filters",
    [
        ({"open_now": True}, {"is_open": True}),
        ({"min_rating": 4}, {"rate": "2"}),
        ({"min_rating": 4.7, "open_now": True}, {"is_open": True, "rate": "3"}),
        ({"min_rating": 3.6}, {"rate": "1"}),
        ({"min_rating": 2}, {}),
    ],
)
async def test_search_uses_the_sites_filters(client, api, args, filters):
    api[f"{P}/neshan-search"] = serve("search_filtered.txt")
    r = (await client.call_tool("ns_search", {"query": "داروخانه", **args})).structured_content
    assert search_body(api.calls[0])["filters"] == filters
    floor = args.get("min_rating", 0)
    assert all(x["rating"] >= floor for x in r["results"])
    if args.get("open_now"):
        assert {x["status"] for x in r["results"]} <= {"باز است", "شبانه\u200cروزی"}


async def test_search_streets_have_no_id(client, api):
    api[f"{P}/neshan-search"] = serve("search_street.txt")
    r = (await client.call_tool("ns_search", {"query": "خیابان ولیعصر"})).structured_content
    assert r["results"][0]["name"] == "ولیعصر" and "id" not in r["results"][0]
    assert r["results"][0]["category"] == "خیابان"


@pytest.mark.parametrize("near", ["Atlantis", "10,10", "35.7"])
async def test_search_rejects_unknown_near(client, api, near):
    r = await client.call_tool("ns_search", {"query": "کافه", "near": near})
    assert r.is_error and not api.calls


# --- places -----------------------------------------------------------------------------------------------


async def test_place_details_with_photos(client, api):
    api[f"{P}/info/v2/{GISHA}"] = serve("place.txt")
    api[f"{P}/info-photos-reviews/v2/{GISHA}/poi-photo/all"] = serve("place_photos.txt")
    r = (await client.call_tool("ns_place", {"place_id": f"https://neshan.org/maps/places/{GISHA}"})).structured_content
    assert r["name"] == "قهوه لمیز شعبه گیشا" and r["category"] == "کافی\u200cشاپ"
    assert r["address"] == "تهران، بزرگراه شهید چمران، کوی نصر، نبش کوچه یکم، پلاک ۱۶"
    assert (r["lat"], r["lng"]) == (35.727015, 51.37926)
    assert r["rating"] == 3.7 and r["votes"] == 41 and r["open"] in ("باز است", "بسته است")
    assert r["phones"] == ["02188255546"] and r["website"] == "https://www.lamizcoffee.com"
    assert len(r["hours"]) == 7 and r["hours"][0].startswith("شنبه: ") and "امروز" not in "".join(r["hours"])
    assert 1 <= len(r["photos"]) <= 5 and all(u.startswith("https://static.neshanmap.ir/") for u in r["photos"])
    assert "<" not in json.dumps(r, ensure_ascii=False)


async def test_place_survives_a_photo_failure(client, api):
    api[f"{P}/info/v2/{GISHA}"] = serve("place.txt")
    api[f"{P}/info-photos-reviews/v2/{GISHA}/poi-photo/all"] = lambda r: httpx.Response(500)
    r = (await client.call_tool("ns_place", {"place_id": GISHA})).structured_content
    assert r["name"] == "قهوه لمیز شعبه گیشا" and r["photos"] == []


async def test_place_rejects_bad_id(client, api):
    r = await client.call_tool("ns_place", {"place_id": "Milad Tower"})
    assert r.is_error and "ns_search" in r.content[0].text and not api.calls


def test_vote_counts_with_thousands_separator():
    assert maps._int("3٬270 رای") == 3270 and maps._int("۴۱ رای") == 41 and maps._int(None) is None


async def test_reviews_newest(client, api):
    api[f"{P}/info-photos-reviews/v2/{GISHA}/poi-review/all"] = serve("reviews.txt")
    r = (await client.call_tool("ns_reviews", {"place_id": GISHA, "page": 2})).structured_content
    assert api.calls[0].url.params["page"] == "1" and api.calls[0].url.params["size"] == "10"
    assert r["count"] == 10 and r["more"] is True
    assert r["reviews"][0] == {"author": "کاربر نشان", "rating": 5, "date": "1405/08/05", "comment": "بینظیر"}


async def test_reviews_featured(client, api):
    api[f"{P}/info-photos-reviews/v2/{MILAD}/poi-review/featured"] = serve("reviews_featured.txt")
    r = (await client.call_tool("ns_reviews", {"place_id": MILAD, "sort": "featured"})).structured_content
    assert [x["author"] for x in r["reviews"]] == ["Arshiya", "باقرنیازی", "یزدی", "pantea 1376"]
    assert r["reviews"][0]["likes"] == 314 and r["more"] is False
    assert all(len(x["comment"]) <= 401 for x in r["reviews"])


async def test_photos_all_and_menu(client, api):
    api[f"{P}/info-photos-reviews/v2/{MILAD}/poi-photo/all"] = serve("photos.txt")
    api[f"{P}/info-photos-reviews/v2/{MILAD}/poi-photo/category"] = serve("photos_menu.txt")
    r = (await client.call_tool("ns_photos", {"place_id": MILAD})).structured_content
    assert r["albums"] == [{"id": "menus", "title": "منو"}] and r["pages"] == 270 and len(r["photos"]) == 10
    assert r["photos"][0] == {
        "url": "https://static.neshanmap.ir/places/images/581/4729741_19040752.jpeg",
        "date": "۹ آبان",
        "author": "سعید افرنگ",
    }
    r = (await client.call_tool("ns_photos", {"place_id": MILAD, "album": "menus"})).structured_content
    assert api.calls[1].url.params["category_id"] == "menus" and len(r["photos"]) == 1 and r["pages"] == 1


async def test_photos_rejects_odd_album(client, api):
    r = await client.call_tool("ns_photos", {"place_id": MILAD, "album": "../x"})
    assert r.is_error and not api.calls


async def test_nearby_groups(client, api):
    api[f"{P}/info/v2/{MILAD}"] = serve("place_milad.txt")
    api[f"{P}/spotlight/v2/51.375503,35.745005"] = serve("nearby.txt")
    r = (await client.call_tool("ns_nearby", {"place_id": MILAD, "per_group": 2})).structured_content
    q = api.calls[1].url.params
    assert (q["source"], q["hashedId"], q["currentY"]) == ("NEAR_BY", MILAD, "35.745005")
    first = r["groups"][0]
    assert first["group"] == "رستوران" and len(first["places"]) == 2
    assert first["places"][0]["name"] == "کاخ رستوران لیدوما" and first["places"][0]["votes"] == 102
    assert 1000 < first["places"][0]["distance_m"] < 1600 and first["places"][0]["status"] == "باز است"
    assert "پارکینگ" in [g["group"] for g in r["groups"]]


async def test_nearby_outside_iran_is_explained(client, api):
    api[f"{P}/info/v2/{MILAD}"] = serve("place_milad.txt")
    api[f"{P}/spotlight/v2/51.375503,35.745005"] = lambda r: httpx.Response(403, text="<html>403</html>")
    r = await client.call_tool("ns_nearby", {"place_id": MILAD})
    assert r.is_error and "only to connections from Iran" in r.content[0].text


async def test_reverse_geocode(client, api):
    api[f"{P}/location-to-address"] = serve("reverse.txt")
    r = (await client.call_tool("ns_reverse_geocode", {"location": "35.7448, 51.3753"})).structured_content
    assert api.calls[0].url.params["lat"] == "35.7448" and api.calls[0].url.params["lng"] == "51.3753"
    assert r["address"] == "بزرگراه شهید همت، میدان آرش" and r["province"] == "استان تهران"
    assert r["street"] is None  # "معبر بدون نام" (unnamed road) is dropped


async def test_reverse_geocode_rejects_swapped_coordinates(client, api):
    r = await client.call_tool("ns_reverse_geocode", {"location": "51.3753,35.7448"})
    assert r.is_error and "lat,lng" in r.content[0].text and not api.calls


@pytest.mark.parametrize("place", ["Tehran", "تهران"])
async def test_air_quality_for_a_city(client, api, place):
    api[f"{P}/weather/air-quality/tehran/summary"] = serve("air_quality.txt")
    r = (await client.call_tool("ns_air_quality", {"place": place})).structured_content
    assert (r["city"], r["aqi"], r["status"]) == ("تهران", 99, "قابل قبول")
    assert r["pollutants"]["PM2.5"] == 99.0 and len(r["stations"]) == 9
    assert [s["aqi"] for s in r["stations"]] == sorted((s["aqi"] for s in r["stations"]), reverse=True)


async def test_air_quality_slug_for_two_word_cities(client, api):
    api[f"{P}/weather/air-quality/bandar-abbas/summary"] = serve("air_quality.txt")
    assert not (await client.call_tool("ns_air_quality", {"place": "بندرعباس"})).is_error


async def test_air_quality_for_a_point(client, api):
    api[f"{P}/weather/v2/summary"] = serve("air_point.txt")
    r = (await client.call_tool("ns_air_quality", {"place": "35.7448,51.3753"})).structured_content
    assert r == {"lat": 35.7448, "lng": 51.3753, "aqi": 84}


# --- routes -----------------------------------------------------------------------------------------------


async def test_car_route(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_car.json")
    r = (await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER})).structured_content
    first = r["routes"][0]
    assert first["via"] == "بزرگراه شیخ فضل الله نوری - بزرگراه شیخ فضل الله نوری"
    assert (first["distance_km"], first["duration_min"]) == (9.9, 18)  # 1059 s rounds to 18 min, as the site does
    assert first["steps"][0]["do"] == "در جهت شرق در میدان آزادی قرار بگیرید" and first["steps"][-1]["do"] == "مقصد"
    assert "toll_road" not in first and "enters_traffic_zone" not in first  # false flags are left out
    assert [z["plan"] for z in r["zones"]] == ["طرح ترافیک", "طرح آلودگی هوا"]


async def test_route_request_matches_the_web_map(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_car.json")
    args = {"origin": AZADI, "destination": TOWER, "avoid_traffic_zone": True}
    await client.call_tool("ns_route", args)
    q = api.calls[0].url.params
    assert route_points(api.calls[0]) == [{"lat": 35.6997, "lng": 51.338}, {"lat": 35.7448, "lng": 51.3753}]
    assert (
        q["value"] == "W3sibozNS42OTk3LCJsbmciOjUxLjMzOH0seyJsYXQiOjM1Ljc0NDgsImxuZyI6NTEuMzc1M31dGF0Ij"
    )  # seen in Chrome
    assert (q["type"], q["avoidTrafficZone"], q["avoidOddEvenZone"], q["eta"]) == ("CAR", "true", "false", "false")
    await client.call_tool("ns_route", {**args, "mode": "motorcycle"})
    q = api.calls[1].url.params
    assert (q["type"], q["avoidTrafficZone"]) == ("MOTOR", "false")  # avoid flags are car-only


async def test_route_with_a_stop_joins_both_legs(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_stops.json")
    r = (
        await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER, "stops": [NASR]})
    ).structured_content
    assert len(route_points(api.calls[0])) == 3 and route_points(api.calls[0])[1] == {"lat": 35.727, "lng": 51.3793}
    first = r["routes"][0]
    assert " | " in first["via"] and first["distance_km"] == 13.1
    assert [s["do"] for s in first["steps"]].count("مقصد") == 2  # one arrival per leg


async def test_toll_and_zone_flags(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_toll.json")
    r = (
        await client.call_tool("ns_route", {"origin": AZADI, "destination": "34.6399,50.8759", "steps": False})
    ).structured_content
    assert all(x["toll_road"] for x in r["routes"]) and r["routes"][1]["enters_odd_even_zone"] is True
    assert "steps" not in r["routes"][0]


async def test_depart_at_posts_like_the_site(client, api):
    api[f"{P}/vehicle-routing-depart"] = serve("route_depart.json")
    args = {"origin": AZADI, "destination": TOWER, "depart_at": "2026-11-01 08:00"}
    r = (await client.call_tool("ns_route", args)).structured_content
    req = api.calls[0]
    assert req.method == "POST" and req.headers["content-type"] == "application/json"
    sent = json.loads(base64.b64decode(req.content))
    assert sent == {
        "origin": {"lat": 35.6997, "lng": 51.338},
        "destination": {"lat": 35.7448, "lng": 51.3753},
        "departureTime": "2026-11-01T08:00:00",
    }
    assert r["routes"][0]["arrive_at"] == "2026-11-01 08:28" and r["routes"][0]["duration_min"] == 28


async def test_arrive_by(client, api):
    api[f"{P}/vehicle-routing-arrival"] = serve("route_arrive.json")
    r = (
        await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER, "arrive_by": "2026-11-01T8:00"})
    ).structured_content
    assert json.loads(base64.b64decode(api.calls[0].content))["arrivalTime"] == "2026-11-01T08:00:00"
    assert r["routes"][0]["leave_by"] == "2026-11-01 07:31"


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"depart_at": "2026-11-01 08:00", "mode": "walk"}, "only with mode car"),
        ({"depart_at": "2026-11-01 08:00", "arrive_by": "2026-11-01 09:00"}, "not both"),
        ({"depart_at": "2026-11-01 08:00", "stops": [NASR]}, "cannot be combined"),
        ({"depart_at": "tomorrow 8am"}, "YYYY-MM-DD HH:MM"),
    ],
)
async def test_timed_route_validation(client, api, extra, message):
    r = await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER, **extra})
    assert r.is_error and message in r.content[0].text and not api.calls


async def test_walk_route_uses_walking_wording_and_one_route(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_walk.json")
    r = (await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER, "mode": "walk"})).structured_content
    assert api.calls[0].url.params["type"] == "PEDESTRIAN" and len(r["routes"]) == 1
    steps = [s["do"] for s in r["routes"][0]["steps"]]
    assert steps[1] == "به چپ حرکت کنید" and r["routes"][0]["steps_more"] > 0 and len(steps) == 40
    assert r["routes"][0]["duration_min"] == 148


def test_roundabout_and_named_instructions():
    name = base64.b64encode("میدان ونک".encode()).decode()
    assert routes._instruction({"t": 10, "e": 2}, walk=False) == "وارد میدان شوید و از خروجی دوم خارج شوید"
    assert (
        routes._instruction({"t": 10, "e": 2, "n": name}, walk=False) == "وارد میدان ونک شوید و از خروجی دوم خارج شوید"
    )
    assert routes._instruction({"t": 0, "m": 6, "n": name}, walk=False) == "به سمت میدان ونک به چپ بپیچید"
    assert routes._instruction({"t": 0, "m": 4}, walk=True) == "به مسیر خود ادامه دهید"
    assert routes._instruction({"t": 12}, walk=False) == ""


async def test_route_without_result_is_a_clear_error(client, api):
    api[f"{P}/vehicle-routing/"] = lambda r: httpx.Response(200, json={"res": {"error": 0, "data": {"r": []}}})
    r = await client.call_tool("ns_route", {"origin": AZADI, "destination": TOWER})
    assert r.is_error and "no route" in r.content[0].text


async def test_travel_times_sorted_with_failures(client, api):
    eta = serve("route_eta.json")
    slow = json.loads(serve("route_eta.json")(None).text)
    slow["res"]["data"]["r"][0]["du"] = base64.b64encode(b"300").decode()

    def answer(request):
        to = route_points(request)[1]
        if to["lat"] == 35.1:
            return httpx.Response(200, json={"res": {"error": 0, "data": {"r": []}}})
        return httpx.Response(200, json=slow) if to["lat"] == 35.727 else eta(request)

    api[f"{P}/vehicle-routing/"] = answer
    args = {"origin": AZADI, "destinations": [TOWER, NASR, "35.1,51.1"]}
    r = (await client.call_tool("ns_travel_times", args)).structured_content
    assert [x["to"] for x in r["results"]] == [NASR, TOWER]
    assert r["results"][0]["duration_min"] == 5 and r["results"][1] == {
        "to": TOWER,
        "duration_min": 18,
        "distance_km": 9.9,
    }
    assert list(r["failed"]) == ["35.1,51.1"]
    assert {c.url.params["eta"] for c in api.calls} == {"true"}


async def test_zone_check(client, api):
    api[f"{P}/vehicle-routing/"] = serve("route_zone_in.json")
    r = (await client.call_tool("ns_zone_check", {"location": "35.6950,51.4200"})).structured_content
    assert (r["in_traffic_zone"], r["in_odd_even_zone"]) == (True, True)
    assert [p["plan"] for p in r["plans"]] == ["طرح ترافیک", "طرح آلودگی هوا"]
    pts = route_points(api.calls[0])
    assert pts[0] == {"lat": 35.695, "lng": 51.42} and abs(pts[1]["lat"] - 35.698) < 1e-9


async def test_transit_options(client, api):
    api[f"{P}/bus-routing/"] = serve("transit.json")
    r = (await client.call_tool("ns_transit_route", {"origin": AZADI, "destination": TOWER})).structured_content
    q = api.calls[0].url.params
    assert (q["p1"], q["p2"], q["p3"], q["p4"]) == ("35.6997", "51.338", "35.7448", "51.3753")
    opts = r["options"]
    assert [o["fare_toman"] for o in opts] == [4660, 5000, 10640, 7000]
    bus = opts[0]["legs"][1]
    assert (bus["ride"], bus["line"], bus["from"], bus["to"], bus["stops"]) == (
        "bus",
        "خط 370",
        "پمپ بنزین",
        "بیمارستان میلاد",
        6,
    )
    assert {leg.get("ride") for leg in opts[2]["legs"]} >= {"BRT", "metro"}


# --- http -------------------------------------------------------------------------------------------------


async def test_blocked_request_is_a_tool_error(client, api):
    api[f"{P}/location-to-address"] = lambda r: httpx.Response(403, text="<html>403</html>")
    r = await client.call_tool("ns_reverse_geocode", {"location": TOWER})
    assert r.is_error and "403" in r.content[0].text and "NESHAN_MCP_PROXY" in r.content[0].text
    assert len(api.calls) == 1  # 403 is not retried


async def test_answers_are_cached(client, api):
    api[f"{P}/location-to-address"] = serve("reverse.txt")
    for _ in range(2):
        await client.call_tool("ns_reverse_geocode", {"location": TOWER})
    assert len(api.calls) == 1


async def test_server_error_is_retried(client, api):
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(502) if len(calls) == 1 else serve("reverse.txt")(request)

    api[f"{P}/location-to-address"] = flaky
    r = await client.call_tool("ns_reverse_geocode", {"location": TOWER})
    assert not r.is_error and len(calls) == 2
