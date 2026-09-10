"""Validate a spec (already in metres) before drawing anything.

Returns (errors, warnings). Errors mean the plan cannot be drawn honestly
and the run should fail without charging. Warnings are design smells the
caller should see (tight clearances, unusual sizes) but do not block.
"""

from __future__ import annotations

WALLS = ("north", "south", "east", "west")
KITCHEN_TYPES = ("fridge", "range", "cooktop", "wall_oven", "sink", "dishwasher", "pantry", "corner")
BATH_TYPES = ("vanity", "toilet", "tub", "shower")
TYPES = KITCHEN_TYPES + BATH_TYPES
COUNTERS = ("white_quartz", "marble", "butcher_block", "black_granite")
TYPICAL_WIDTH = {  # metres, (min, max) before a warning fires
    "fridge": (0.6, 1.25), "range": (0.6, 1.25), "cooktop": (0.6, 1.0), "wall_oven": (0.6, 0.9),
    "sink": (0.45, 1.2), "dishwasher": (0.45, 0.65), "pantry": (0.3, 1.5), "corner": (0.9, 0.9),
    "vanity": (0.45, 2.5), "toilet": (0.4, 0.8), "tub": (1.2, 1.9), "shower": (0.8, 2.0),
}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(spec: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(spec, dict):
        return ["spec must be a JSON object"], []
    room = spec.get("room")
    if not isinstance(room, dict):
        return ["spec.room is required: {width, depth, height} in metres"], []
    for k in ("width", "depth", "height"):
        v = room.get(k)
        if not _num(v) or v <= 0:
            errors.append(f"room.{k} must be a positive number")
    if errors:
        return errors, warnings
    W, D, H = room["width"], room["depth"], room["height"]
    if not (1.0 <= W <= 30 and 1.0 <= D <= 30):
        warnings.append(f"room is {W:.2f} x {D:.2f} m; check units (expected roughly 2 to 12 m per side)")
    if not (2.0 <= H <= 6.0):
        warnings.append(f"room.height {H:.2f} m is unusual; check units")

    def wall_len(wall: str) -> float:
        return W if wall in ("north", "south") else D

    def check_wall(obj: dict, where: str) -> str | None:
        wall = obj.get("wall")
        if wall not in WALLS:
            errors.append(f"{where}.wall must be one of {', '.join(WALLS)} (got {wall!r})")
            return None
        return wall

    for i, w in enumerate(spec.get("windows") or []):
        where = f"windows[{i}]"
        wall = check_wall(w, where)
        if not wall:
            continue
        u0, u1, z0, z1 = (w.get(k) for k in ("u0", "u1", "z0", "z1"))
        if not all(_num(v) for v in (u0, u1)) or not 0 <= u0 < u1 <= wall_len(wall) + 1e-6:
            errors.append(f"{where}: need 0 <= u0 < u1 <= {wall_len(wall):.2f} (wall length)")
        if z0 is not None and z1 is not None and (not all(_num(v) for v in (z0, z1)) or not 0 <= z0 < z1 <= H + 1e-6):
            errors.append(f"{where}: need 0 <= z0 < z1 <= {H:.2f} (ceiling)")

    for i, o in enumerate(spec.get("openings") or []):
        where = f"openings[{i}]"
        wall = check_wall(o, where)
        if not wall:
            continue
        u0, u1 = o.get("u0"), o.get("u1")
        if not all(_num(v) for v in (u0, u1)) or not 0 <= u0 < u1 <= wall_len(wall) + 1e-6:
            errors.append(f"{where}: need 0 <= u0 < u1 <= {wall_len(wall):.2f} (wall length)")
        if o.get("z1") is not None and (not _num(o["z1"]) or not 0 < o["z1"] <= H + 1e-6):
            errors.append(f"{where}.z1 (head height) must be between 0 and {H:.2f}")

    runs = spec.get("cabinet_runs") or []
    if not runs and not spec.get("island") and not spec.get("peninsulas"):
        errors.append("spec needs at least one of cabinet_runs, island, or peninsulas")
    for i, run in enumerate(runs):
        where = f"cabinet_runs[{i}]"
        wall = check_wall(run, where)
        if not wall:
            continue
        s, e = run.get("start"), run.get("end")
        if not all(_num(v) for v in (s, e)) or not 0 <= s < e <= wall_len(wall) + 1e-6:
            errors.append(f"{where}: need 0 <= start < end <= {wall_len(wall):.2f} (wall length)")
            continue
        bath = run.get("kind") == "bath"
        apps = run.get("appliances") or []
        if not isinstance(apps, list):
            errors.append(f"{where}.appliances must be a list")
            continue
        placed = []
        for j, a in enumerate(apps):
            aw = f"{where}.appliances[{j}]"
            t = a.get("type")
            if t not in TYPES:
                errors.append(f"{aw}.type must be one of {', '.join(TYPES)} (got {t!r})")
                continue
            if bath and t not in BATH_TYPES:
                errors.append(f"{aw}: {t} is not a bath fixture; drop kind=bath or use {', '.join(BATH_TYPES)}")
            if not bath and t in BATH_TYPES:
                errors.append(f"{aw}: {t} needs the run to have \"kind\": \"bath\"")
            at, width = a.get("at"), a.get("width")
            if not all(_num(v) for v in (at, width)) or width <= 0:
                errors.append(f"{aw}: at and width must be numbers, width > 0")
                continue
            if at < s - 1e-6 or at + width > e + 1e-6:
                errors.append(f"{aw}: {t} at {at:.2f}..{at + width:.2f} falls outside the run {s:.2f}..{e:.2f}")
            lo, hi = TYPICAL_WIDTH.get(t, (0, 99))
            if not lo <= width <= hi:
                warnings.append(f"{aw}: {t} width {width:.2f} m is outside the typical {lo:.2f}..{hi:.2f} m")
            if t == "corner" and not (abs(at - s) < 0.01 or abs(at + width - e) < 0.01):
                errors.append(f"{aw}: a corner unit must sit at the start or end of its run")
            placed.append((at, at + width, t))
        placed.sort()
        for (a0, a1, t1), (b0, b1, t2) in zip(placed, placed[1:]):
            if b0 < a1 - 1e-6:
                errors.append(f"{where}: {t1} ({a0:.2f}..{a1:.2f}) overlaps {t2} ({b0:.2f}..{b1:.2f})")
        if not bath and e - s < 0.6:
            warnings.append(f"{where}: run is only {e - s:.2f} m long")

    isl = spec.get("island")
    if isl is not None:
        if not isinstance(isl, dict) or not all(_num(isl.get(k)) for k in ("x", "y", "width", "depth")):
            errors.append("island needs numeric x, y (centre), width, depth")
        else:
            x0, x1 = isl["x"] - isl["width"] / 2, isl["x"] + isl["width"] / 2
            y0, y1 = isl["y"] - isl["depth"] / 2, isl["y"] + isl["depth"] / 2
            if x0 < 0 or y0 < 0 or x1 > W + 1e-6 or y1 > D + 1e-6:
                errors.append("island extends outside the room")
            else:
                clear = min(x0, W - x1, y0, D - y1)
                for run in runs:
                    if run.get("wall") == "south":
                        clear = min(clear, y0 - 0.6)
                    elif run.get("wall") == "north":
                        clear = min(clear, D - 0.6 - y1)
                    elif run.get("wall") == "west":
                        clear = min(clear, x0 - 0.6)
                    elif run.get("wall") == "east":
                        clear = min(clear, W - 0.6 - x1)
                if clear < 0.9:
                    warnings.append(f"island clearance is {max(clear, 0):.2f} m to the nearest counter; 0.9 to 1.2 m is standard")

    for i, p in enumerate(spec.get("peninsulas") or []):
        where = f"peninsulas[{i}]"
        wall = check_wall(p, where)
        if not wall:
            continue
        if not all(_num(p.get(k)) for k in ("at", "width", "length")):
            errors.append(f"{where}: at, width, length must be numbers")
            continue
        if p["at"] < 0 or p["at"] + p["width"] > wall_len(wall) + 1e-6:
            errors.append(f"{where}: at..at+width must fit within the {wall_len(wall):.2f} m wall")
        depth_avail = D if wall in ("north", "south") else W
        if p["length"] > depth_avail + 1e-6:
            errors.append(f"{where}: length {p['length']:.2f} exceeds the room")
        if p.get("doors_side") not in (None, "left", "right"):
            errors.append(f"{where}.doors_side must be left or right")

    style = spec.get("style") or {}
    if style.get("counter") not in (None, *COUNTERS):
        warnings.append(f"style.counter {style.get('counter')!r} is not a known finish; it is passed through as a label")
    return errors, warnings
