"""Quote-ready totals derived from the takeoff rows and the spec."""

from __future__ import annotations

from collections import Counter

FT = 0.3048
IN = 0.0254

BASE_ITEMS = ("Base cabinet", "Sink base", "Base cabinet under cooktop", "Vanity base", "Island base", "Peninsula base")
UPPER_ITEMS = ("Upper cabinet", "Corner upper cabinet", "Cabinet above refrigerator")
TALL_ITEMS = ("Pantry", "Oven tower")
COUNTER_ITEMS = ("Countertop section", "Island countertop", "Peninsula countertop", "Vanity countertop")
APPLIANCE_ITEMS = {
    "Refrigerator opening": "refrigerator", "Range opening": "range", "Cooktop": "cooktop",
    "Oven tower, double oven cutouts": "double wall oven", "Dishwasher opening": "dishwasher",
    "Undermount sink": "kitchen sink", "Wall-mount hood": "range hood",
}
FIXTURE_ITEMS = {
    "Toilet": "toilet", "Alcove tub": "alcove tub", "Shower pan and curb": "shower",
    "Undermount basin and faucet": "vanity basin", "Mirror": "mirror",
}


def _ft_in(m: float) -> str:
    total = int(round(m / IN))
    ft, inch = divmod(total, 12)
    return f'{inch}"' if ft == 0 else f"{ft}'{inch}\""


def _starts(item: str, prefixes) -> bool:
    return any(item.startswith(p) for p in prefixes)


def summarize(spec: dict, rows: list[dict]) -> dict:
    room = spec["room"]
    base_in = sum(r["width_in"] for r in rows if _starts(r["item"], BASE_ITEMS))
    upper_in = sum(r["width_in"] for r in rows if _starts(r["item"], UPPER_ITEMS))
    tall_in = sum(r["width_in"] for r in rows if _starts(r["item"], TALL_ITEMS))
    counter_sqin = sum(
        r["width_in"] * (r["depth_in"] or 25) for r in rows if _starts(r["item"], COUNTER_ITEMS)
    )
    backsplash_sqin = sum(r["width_in"] * (r["height_in"] or 22) for r in rows if r["item"].startswith("Backsplash"))
    tile_sqin = sum(
        r["width_in"] * (r["height_in"] or 0)
        for r in rows
        if r["item"].startswith(("Tub surround tile", "Shower tile"))
    )
    counts = Counter(r["item"] for r in rows)
    appliances = sorted({APPLIANCE_ITEMS[k] for k in counts if k in APPLIANCE_ITEMS})
    fixtures = sorted({FIXTURE_ITEMS[k] for k in counts if k in FIXTURE_ITEMS})
    cabinets = sum(n for item, n in counts.items() if _starts(item, BASE_ITEMS + UPPER_ITEMS + TALL_ITEMS))
    lights = sum(n for item, n in counts.items() if item in ("Pendant light", "Vanity light bar")) + sum(
        n for item, n in counts.items() if item == "Under-cabinet light strip"
    )
    return {
        "room": {
            "width_m": room["width"], "depth_m": room["depth"], "height_m": room["height"],
            "width": _ft_in(room["width"]), "depth": _ft_in(room["depth"]), "ceiling": _ft_in(room["height"]),
            "floor_area_sqft": round(room["width"] * room["depth"] / FT / FT, 1),
        },
        "room_type": "bathroom" if any(r.get("kind") == "bath" for r in spec.get("cabinet_runs") or []) else "kitchen",
        "cabinet_count": cabinets,
        "base_cabinet_linear_ft": round(base_in / 12, 1),
        "upper_cabinet_linear_ft": round(upper_in / 12, 1),
        "tall_cabinet_linear_ft": round(tall_in / 12, 1),
        "countertop_sqft": round(counter_sqin / 144, 1),
        "backsplash_sqft": round(backsplash_sqin / 144, 1),
        "wall_tile_sqft": round(tile_sqin / 144, 1),
        "counter_finish": (spec.get("style") or {}).get("counter", "white_quartz"),
        "appliances": appliances,
        "fixtures": fixtures,
        "seating": sum(n for item, n in counts.items() if item == "Counter stool"),
        "light_fixtures": lights,
        "line_items": len(rows),
        "item_counts": [{"item": item, "count": n} for item, n in sorted(counts.items())],
    }
