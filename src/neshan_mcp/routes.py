"""Neshan routing tools: car/motorcycle/bicycle/walk directions, travel times, traffic zones, transit."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import SITE, ApiError, b64, fetch, unb64
from .maps import _minutes, _point
from .registry import tool

# Turn instructions, ported from the web map's route parser (module 48791, checked 2026-10-31).
# Keys are the step type `t`; %1$s, %2$s ... are filled in order.
DRIVE = {0: "%1$s بپیچید", 1: "به مسیر خود ادامه دهید", 2: "در جهت %1$s قرار بگیرید", 3: "مقصد", 4: "%1$s برانید ", 5: "%1$s برانید و وارد خروجی شوید", 6: "%1$s برانید و از خروجی خارج شوید", 7: "%1$s برانید", 8: "در انتهای مسیر %1$s بپیچید", 9: "%1$s بپیچید و به مسیر خود ادامه دهید", 10: "وارد میدان شوید و از خروجی %1$s خارج شوید", 11: "%1$s برانید و وارد میدان شوید و از خروجی %2$s به سمت %3$s خارج شوید", 14: "حدود %1$s از مبدا به سمت ایستگاه دوچرخه %2$s پیاده روی کنید", 15: "مستقیم برانید", 16: "%1$s برانید و وارد میدان شوید و از خروجی %2$s خارج شوید", 17: "%1$s بپیچید"}  # fmt: skip
DRIVE_NAMED = {0: "به سمت %1$s %2$s بپیچید", 1: "در %1$s به مسیر خود ادامه دهید", 2: "در جهت %1$s در %2$s قرار بگیرید", 3: "مقصد", 4: "%2$s برانید و وارد %1$s شوید", 5: "%2$s برانید و وارد %1$s شوید", 6: "%2$s برانید و از %1$s خارج شوید", 7: "%2$s برانید و وارد %1$s شوید", 8: "در انتهای مسیر %2$s بپیچید و وارد %1$s شوید", 9: "در %1$s %2$s برانید و به مسیر خود ادامه دهید", 10: "وارد %1$s شوید و از خروجی %2$s خارج شوید", 11: "%1$s برانید و وارد %2$s شوید و از خروجی %3$s به سمت %4$s خارج شوید", 14: "حدود %1$s از ایستگاه دوچرخه %2$s به سمت مقصد پیاده روی کنید", 15: "مستقیم برانید", 16: "%1$s برانید و وارد %2$s شوید و از خروجی %3$s خارج شوید", 17: "به سمت %1$s %2$s بپیچید"}  # fmt: skip
WALK = {0: "%1$s حرکت کنید", 1: "به مسیر خود ادامه دهید", 2: "در جهت %1$s قرار بگیرید", 3: "به مقصد رسیدید", 4: "%1$s حرکت کنید ", 5: "%1$s حرکت کنید و وارد خروجی شوید", 6: "%1$s حرکت کنید و از خروجی خارج شوید", 7: "%1$s حرکت کنید", 8: "در انتهای مسیر %1$s حرکت کنید", 9: "%1$s حرکت کنید و به مسیر خود ادامه دهید", 10: "وارد میدان شوید و از خروجی %1$s خارج شوید", 11: "%1$s حرکت کنید و وارد میدان شوید و از خروجی %2$s به سمت %3$s خارج شوید", 15: "مستقیم حرکت کنید", 16: "%1$s حرکت کنید و وارد میدان شوید و از خروجی %2$s خارج شوید", 17: "%1$s بپیچید"}  # fmt: skip
WALK_NAMED = {0: "به سمت %1$s %2$s حرکت کنید", 1: "در پیاده\u200cرو %1$s به مسیر خود ادامه دهید", 2: "در جهت %1$s در پیاده\u200cرو %2$s قرار بگیرید", 3: "به مقصد رسیدید", 4: "%2$s حرکت کنید و وارد پیاده\u200cرو %1$s شوید", 5: "%2$s حرکت کنید و وارد پیاده\u200cرو %1$s شوید", 6: "%2$s حرکت کنید و از %1$s خارج شوید", 7: "%2$s حرکت کنید و وارد پیاده\u200cرو %1$s شوید", 8: "در انتهای مسیر %2$s حرکت کنید و وارد پیاده\u200cرو %1$s شوید", 9: "در پیاده\u200cرو %1$s %2$s حرکت کنید و به مسیر خود ادامه دهید", 10: "وارد %1$s شوید و از خروجی %2$s خارج شوید", 11: "%1$s حرکت کنید و وارد %2$s شوید و از خروجی %3$s به سمت %4$s خارج شوید", 15: "مستقیم حرکت کنید", 16: "%1$s حرکت کنید و وارد %2$s شوید و از خروجی %3$s خارج شوید", 17: "%1$s بپیچید"}  # fmt: skip
BEARINGS = {"N": "شمال", "NE": "شمال شرقی", "NW": "شمال غربی", "S": "جنوب", "SE": "جنوب شرقی", "SW": "جنوب غربی", "E": "شرق", "W": "غرب"}  # fmt: skip
TURNS_DRIVE = ["دور بزنید", "کاملا به راست", "به راست", "کمی به راست", "در مسیر خود", "کمی به چپ", "به چپ", "کاملا به چپ"]  # fmt: skip
TURNS_WALK = ["دور بزنید", "به راست", "به راست", "کمی به راست", "در مسیر خود", "کمی به چپ", "به چپ", "به چپ"]
ORDINALS = ["صفرم", "اول", "دوم", "سوم", "چهارم", "پنجم", "ششم", "هفتم", "هشتم"]
MODES = {"car": "CAR", "motorcycle": "MOTOR", "bicycle": "BIKE", "walk": "PEDESTRIAN"}
MAX_STEPS = 40  # a long walk has 60+ steps; the rest is counted in steps_more
TRANSIT_KINDS = {"bus": "bus", "BRT": "BRT", "metro": "metro", "walk": "walk"}


def _fill(template: str | None, *args: Any) -> str:
    if not template:
        return ""
    return re.sub(r"%(\d+)\$s", lambda m: str(args[int(m.group(1)) - 1] or "") if int(m.group(1)) <= len(args) else "", template)  # fmt: skip


def _instruction(st: dict[str, Any], walk: bool) -> str:
    """One step's Persian instruction, built like the web map builds it."""
    kind, mod, exit_no = st.get("t"), st.get("m"), st.get("e")
    name, rotary, bearing = unb64(st.get("n")), unb64(st.get("rn")), BEARINGS.get(st.get("b"), "")
    plain, named, turns = (WALK, WALK_NAMED, TURNS_WALK) if walk else (DRIVE, DRIVE_NAMED, TURNS_DRIVE)
    p, n = plain.get(kind), named.get(kind)
    ordinal = ORDINALS[exit_no] if isinstance(exit_no, int) and 0 <= exit_no < len(ORDINALS) else ""
    turn = turns[mod] if isinstance(mod, int) and 0 <= mod < len(turns) else ""
    if kind in (0, 4, 5, 6, 7, 8, 9, 17):
        if mod == 4:
            text = plain[1] if not name else _fill(named[1], name)
        else:
            text = _fill(p, turn) if not name else _fill(n, name, turn)
            if kind == 9 and name and mod == 0:
                text = text.replace("حرکت کنید", "").replace("برانید", "").replace("  ", " ")
    elif kind == 2:
        text = _fill(p, bearing) if not name else _fill(n, bearing, name)
    elif kind == 1:
        text = p if not name else _fill(n, name)
    elif kind == 10:
        text = _fill(p, ordinal) if not name else _fill(n, name, ordinal)
    elif kind == 11:
        if not rotary:
            text = _fill(plain[16], turn, ordinal) if not name else _fill(p, turn, ordinal, name)
        else:
            text = _fill(named[16], turn, rotary, ordinal) if not name else _fill(n, turn, rotary, ordinal, name)
    elif kind == 14:  # bicycle: walk to or from a bike-share station
        text = _fill(p if mod == 8 else n, unb64(st.get("rd")), name)
    elif kind in (3, 15):
        text = n or ""
    else:
        text = ""
    return re.sub(r"\s+", " ", text).strip()


def _route(r: dict[str, Any], walk: bool, with_steps: bool) -> dict[str, Any]:
    legs = [leg for leg in r.get("lg") or [] if isinstance(leg, dict)] or [{}]
    seconds = float(unb64(r.get("du")) or 0)
    metres = r.get("rd") or sum(float(unb64(leg.get("rd")) or 0) for leg in legs)
    out: dict[str, Any] = {
        "via": " | ".join(unb64(leg.get("s")) for leg in legs if leg.get("s")) or None,
        "distance_km": round(float(metres) / 1000, 1),
        "duration_min": _minutes(seconds),
        "arrive_at": (r.get("art") or "").replace("T", " ")[:16] or None,  # depart_at routes
        "leave_by": (r.get("lb") or "").replace("T", " ")[:16] or None,  # arrive_by routes
        "toll_road": r.get("hasToll") or None,
        "enters_traffic_zone": r.get("wct") or None,
        "enters_odd_even_zone": r.get("wco") or None,
    }
    if with_steps:
        steps = []
        for leg in legs:
            for st in leg.get("st") or []:
                text = _instruction(st, walk)
                if text:
                    m, s = float(unb64(st.get("rd")) or 0), float(unb64(st.get("du")) or 0)
                    steps.append({"do": text, "m": round(m), "s": round(s)})
        out["steps"] = steps[:MAX_STEPS]
        if len(steps) > MAX_STEPS:
            out["steps_more"] = len(steps) - MAX_STEPS
    return out


def _route_value(points: list[tuple[float, float]]) -> str:
    """The `value` the web map sends: base64 of the points with characters 5-9 moved to the end."""
    v = b64(json.dumps([{"lat": lat, "lng": lng} for lat, lng in points], separators=(",", ":")))
    return v[:5] + v[10:] + v[5:10]


def _pin(p: tuple[float, float]) -> str:
    return f"{p[0]:.6f},{p[1]:.6f}"


def _routing_params(points, mode: str, eta: bool = False, traffic=False, odd_even=False, tolls=False) -> dict:
    car = mode == "car"
    return {
        "type": MODES[mode],
        "value": _route_value(points),
        "eta": str(eta).lower(),
        "avoidTrafficZone": str(car and traffic).lower(),
        "avoidOddEvenZone": str(car and odd_even).lower(),
        "avoidToll": str(car and tolls).lower(),
        "poiHashId": "undefined",
    }


def _routing_data(body: Any) -> dict[str, Any]:
    data = ((body.get("res") or {}).get("data") if isinstance(body, dict) else None) or {}
    return data if isinstance(data, dict) else {}


def _when(text: str, what: str) -> str:
    """'YYYY-MM-DD HH:MM' (Tehran time) -> the site's 'YYYY-MM-DDTHH:MM:00'."""
    m = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})(?::00)?\s*", text or "")
    if not m:
        raise ApiError(f"{what} must be 'YYYY-MM-DD HH:MM' in Tehran time (Gregorian), e.g. '2026-11-01 08:00'.")
    return f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}:00"


def _zones(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"plan": z.get("title"), "hours": z.get("timeText")}
        for z in data.get("dz") or []
        if isinstance(z, dict) and z.get("enable")
    ]


@tool("Neshan directions")
async def ns_route(
    origin: Annotated[str, Field(description="Start point as 'lat,lng', e.g. '35.6997,51.3380' (Azadi Square).")],
    destination: Annotated[str, Field(description="End point as 'lat,lng', e.g. '35.7448,51.3753' (Milad Tower).")],
    mode: Annotated[
        Literal["car", "motorcycle", "bicycle", "walk"],
        Field(description="How to travel: car, motorcycle, bicycle or walk."),
    ] = "car",
    stops: Annotated[
        list[str] | None,
        Field(
            description="Places to pass on the way, in order, each 'lat,lng' (at most 3), e.g. ['35.7270,51.3793'].",
            max_length=3,
        ),
    ] = None,
    depart_at: Annotated[
        str | None,
        Field(
            description="Car only: leave at this Tehran time instead of now, 'YYYY-MM-DD HH:MM' (Gregorian), e.g. '2026-11-01 08:00'. Gives the predicted travel time and arrive_at."
        ),
    ] = None,
    arrive_by: Annotated[
        str | None,
        Field(
            description="Car only: arrive by this Tehran time, 'YYYY-MM-DD HH:MM' (Gregorian), e.g. '2026-11-01 09:00'. Gives leave_by."
        ),
    ] = None,
    avoid_traffic_zone: Annotated[
        bool, Field(description="Car only: avoid Tehran's traffic-plan zone (طرح ترافیک).")
    ] = False,
    avoid_odd_even: Annotated[
        bool, Field(description="Car only: avoid the odd-even / air-pollution plan zone (طرح زوج و فرد).")
    ] = False,
    avoid_tolls: Annotated[bool, Field(description="Car only: avoid toll roads.")] = False,
    steps: Annotated[bool, Field(description="Include turn-by-turn steps for the first route.")] = True,
) -> dict[str, Any]:
    """Get car, motorcycle, bicycle or walking directions on Neshan: distance, travel time and turn-by-turn steps.

    Use for "how long from A to B", "best route to ...", "how far is ...", "when should I leave to be there by 9".
    Points are 'lat,lng'; get them from ns_search first. Car trips usually return 2-3 alternative routes, the first
    being Neshan's pick; each has distance_km, duration_min (live estimate for leaving now, or for depart_at),
    `via` (main roads), and flags toll_road, enters_traffic_zone, enters_odd_even_zone when true. depart_at and
    arrive_by (car only, Neshan's traffic forecast; it covers Tehran, Karaj and Mashhad) cannot be combined with
    stops or avoid_* options. Steps (Persian, first route only, up to 40) have `do`, `m` metres and `s` seconds.
    `zones` lists the traffic plans for the area with their active hours. For several destinations at once use
    ns_travel_times; for bus and metro use ns_transit_route.
    """
    o, d = _point(origin, "origin"), _point(destination, "destination")
    via = [_point(s, "stop") for s in stops or []]
    timed = depart_at or arrive_by
    if timed and mode != "car":
        raise ApiError("depart_at and arrive_by work only with mode car (the web map offers them only for cars).")
    if depart_at and arrive_by:
        raise ApiError("Give either depart_at or arrive_by, not both.")
    if timed and (via or avoid_traffic_zone or avoid_odd_even or avoid_tolls):
        raise ApiError("depart_at / arrive_by cannot be combined with stops or avoid_* options.")
    referer = f"{SITE}/maps/routing/{MODES[mode].lower()}/origin/{_pin(o)}/destination/{_pin(d)}"
    if timed:
        key = "departureTime" if depart_at else "arrivalTime"
        body = await fetch(
            "vehicle-routing-depart" if depart_at else "vehicle-routing-arrival",
            headers={"Content-Type": "application/json"},
            referer=referer,
            body={
                "origin": {"lat": o[0], "lng": o[1]},
                "destination": {"lat": d[0], "lng": d[1]},
                key: _when(timed, "depart_at" if depart_at else "arrive_by"),
            },
            ttl=300,
        )
    else:
        params = _routing_params([o, *via, d], mode, False, avoid_traffic_zone, avoid_odd_even, avoid_tolls)
        body = await fetch(
            "vehicle-routing/", params, headers={"Content-Type": "application/json"}, referer=referer, ttl=60
        )
    data = _routing_data(body)
    routes = [r for r in data.get("r") or [] if isinstance(r, dict)]
    if not routes:
        raise ApiError("Neshan found no route between these points. Check both are on or near a road, 'lat,lng' order.")
    walk = mode == "walk"
    if walk:
        routes = routes[:1]  # the web map shows only the first walking route
    out: dict[str, Any] = {"mode": mode, "routes": [_route(r, walk, steps and i == 0) for i, r in enumerate(routes)]}
    if zones := _zones(data):
        out["zones"] = zones
    return out


@tool("Neshan travel times to several places")
async def ns_travel_times(
    origin: Annotated[str, Field(description="Start point as 'lat,lng', e.g. '35.6997,51.3380'.")],
    destinations: Annotated[
        list[str],
        Field(
            description="1-10 end points, each 'lat,lng', e.g. ['35.7448,51.3753', '35.7270,51.3793'].",
            min_length=1,
            max_length=10,
        ),
    ],
    mode: Annotated[
        Literal["car", "motorcycle", "bicycle", "walk"],
        Field(description="How to travel: car, motorcycle, bicycle or walk."),
    ] = "car",
) -> dict[str, Any]:
    """Get the travel time from one point to several places on Neshan, fastest first.

    Use to answer "which of these is closest by car", "how far is each branch from me", or to pick the nearest
    result of ns_search by real travel time instead of straight-line distance. Each result keeps the destination
    as given ('lat,lng') with duration_min (Neshan's live estimate for leaving now, its first route) and
    distance_km (car and motorcycle only). A destination with no route is listed under `failed`. For the full
    route and steps to the winner call ns_route.
    """
    o = _point(origin, "origin")
    points = [(text, _point(text, "destination")) for text in destinations]

    async def one(text: str, p: tuple[float, float]) -> dict[str, Any]:
        body = await fetch(
            "vehicle-routing/",
            _routing_params([o, p], mode, eta=True),
            headers={"Content-Type": "application/json"},
            referer=f"{SITE}/maps/places",
            ttl=60,
        )
        routes = [r for r in _routing_data(body).get("r") or [] if isinstance(r, dict)]
        if not routes:
            raise ApiError("no route")
        r = routes[0]
        km = round(float(r["rd"]) / 1000, 1) if r.get("rd") else None
        return {"to": text, "duration_min": _minutes(float(unb64(r.get("du")) or 0)), "distance_km": km}

    results = await asyncio.gather(*(one(t, p) for t, p in points), return_exceptions=True)
    ok = sorted((r for r in results if isinstance(r, dict)), key=lambda r: r["duration_min"])
    failed = {t: str(r) for (t, _), r in zip(points, results, strict=True) if not isinstance(r, dict)}
    if not ok:
        raise ApiError("Neshan found no route to any destination. Check the points are 'lat,lng' near roads.")
    return {"from": origin, "mode": mode, "results": ok, "failed": failed or None}


@tool("Tehran traffic and odd-even zone check")
async def ns_zone_check(
    location: Annotated[str, Field(description="The point as 'lat,lng', e.g. '35.6950,51.4200'.")],
) -> dict[str, Any]:
    """Check whether a point is inside Tehran's traffic-plan zone or odd-even (air-pollution) zone, with plan hours.

    Use before driving to or parking at an address in Tehran: inside the traffic zone (طرح ترافیک) a car needs a
    permit while the plan runs; inside the odd-even zone (طرح آلودگی هوا / زوج و فرد) plates are restricted on
    alternate days. Answers from Neshan's router, which reports the zones of a trip's start point. `plans` gives the
    active hours Neshan shows. To route around the zones use ns_route with avoid_traffic_zone / avoid_odd_even.
    """
    lat, lng = _point(location, "location")
    near = (round(lat + 0.003, 6), lng)  # a point ~300 m north: the router needs a trip to report zones
    body = await fetch(
        "vehicle-routing/",
        _routing_params([(lat, lng), near], "car"),
        headers={"Content-Type": "application/json"},
        referer=f"{SITE}/maps/routing/car/origin/{_pin((lat, lng))}/destination/{_pin(near)}",
        ttl=600,
    )
    data = _routing_data(body)
    if "oit" not in data:
        raise ApiError("Neshan returned no zone information for this point. Check it is on or near a road.")
    return {
        "lat": lat,
        "lng": lng,
        "in_traffic_zone": bool(data.get("oit")),
        "in_odd_even_zone": bool(data.get("oio")),
        "plans": _zones(data) or None,
    }


def _stop(p: Any) -> str | None:
    return p.get("unique_id") if isinstance(p, dict) else None


@tool("Neshan public transit directions")
async def ns_transit_route(
    origin: Annotated[str, Field(description="Start point as 'lat,lng', e.g. '35.6997,51.3380'.")],
    destination: Annotated[str, Field(description="End point as 'lat,lng', e.g. '35.7448,51.3753'.")],
) -> dict[str, Any]:
    """Get bus, BRT and metro options between two points on Neshan, with lines, stops, walking and fares.

    Use when the user wants to go by public transport (اتوبوس، مترو، بی\u200cآر\u200cتی), mainly within Tehran
    and other big cities Neshan covers. Each option lists legs in order: walk (metres) or a ride with `line`,
    from/to stop, number of stops and fare in Toman; `fare_toman` is the option's total. Neshan gives no travel
    time here. No option: the city may have no transit data; use ns_route with mode walk or car.
    """
    o, d = _point(origin, "origin"), _point(destination, "destination")
    body = await fetch(
        "bus-routing/",
        {"p1": o[0], "p2": o[1], "p3": d[0], "p4": d[1]},
        headers={"Content-Type": "application/json"},
        referer=f"{SITE}/maps/routing/public_transportation/origin/{_pin(o)}/destination/{_pin(d)}",
        ttl=600,
        xor="data",
    )
    raw_options = body.get("data") if isinstance(body, dict) else None
    options = []
    for opt in raw_options if isinstance(raw_options, list) else []:
        legs, fare = [], 0
        for s in opt if isinstance(opt, list) else []:
            sec = s.get("section") if isinstance(s, dict) else None
            if not isinstance(sec, dict):
                continue
            kind = TRANSIT_KINDS.get(sec.get("type"), sec.get("type"))
            if kind == "walk":
                legs.append({"walk_m": round(sec.get("distance") or 0), "to": _stop(s.get("end")) or "مقصد"})
                continue
            fare += s.get("cost") or 0
            legs.append(
                {
                    "ride": kind,
                    "line": s.get("lineAlias"),
                    "line_name": s.get("line_name"),
                    "from": _stop(s.get("start")),
                    "to": _stop(s.get("end")),
                    "stops": s.get("station_between"),
                    "fare_toman": s.get("cost") or None,
                }
            )
        if legs:
            walk = sum(leg.get("walk_m", 0) for leg in legs)
            options.append({"fare_toman": fare, "walk_m": walk, "legs": legs})
    if not options:
        raise ApiError("Neshan has no public transit option between these points. Try ns_route instead.")
    return {"options": options}
