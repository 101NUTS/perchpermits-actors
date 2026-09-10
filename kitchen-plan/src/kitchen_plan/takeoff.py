"""Cabinet takeoff: the list of cabinets and appliances a spec implies, in inches, ready for a quote.

Ported from renderbench/takeoff.py.
Mirrors the builder's module logic (base runs split into ~24 in. doors, drawers over doors, uppers between
appliances and windows, corner units, pantries, oven towers, island and peninsula fronts).
"""

BASE_D, UPPER_D, UPPER_Z0, UPPER_Z1 = 0.60, 0.35, 1.50, 2.20


def inches(m):
    return int(round(m / 0.0254))


def split(span, target=0.6):
    n = max(1, round(span / target))
    return [span / n] * n


def subtract(span, blocks):
    free = [span]
    for b0, b1 in blocks:
        nxt = []
        for f0, f1 in free:
            if b1 <= f0 or b0 >= f1:
                nxt.append((f0, f1))
                continue
            if b0 > f0:
                nxt.append((f0, b0))
            if b1 < f1:
                nxt.append((b1, f1))
        free = nxt
    return free


def takeoff(spec):
    rows = []
    wall_name = {"north": "Back wall", "south": "Front wall", "east": "Right wall", "west": "Left wall"}

    def add(location, item, width_m, height_m=None, depth_m=None, note=""):
        rows.append({"location": location, "item": item, "width_in": inches(width_m),
                     "height_in": inches(height_m) if height_m else "", "depth_in": inches(depth_m) if depth_m else "", "note": note})

    for run in spec.get("cabinet_runs", []):
        loc = wall_name.get(run["wall"], run["wall"])
        s, e = run["start"], run["end"]
        apps = sorted(run.get("appliances", []), key=lambda a: a["at"])
        blocked_upper = []
        bath = run.get("kind") == "bath"
        cursor = s
        for a in apps + [None]:
            seg_end = a["at"] if a else e
            w = seg_end - cursor
            if bath:
                pass
            elif 0.02 < w < 0.2:
                add(loc, "Base filler", w, 0.9, BASE_D)
            elif w >= 0.2:
                for dw in split(w):
                    add(loc, "Base cabinet, drawer over door", dw, 0.9, BASE_D)
            if a is None:
                break
            a0, a1, k = a["at"], a["at"] + a["width"], a["type"]
            if k == "vanity":
                for dw in split(a["width"], 0.5):
                    add(loc, "Vanity base, drawer over doors", dw, 0.86, 0.53)
                add(loc, "Vanity countertop", a["width"] + 0.04, None, 0.55, spec.get("style", {}).get("counter", "white_quartz"))
                nb = 2 if a["width"] >= 1.5 else 1
                for _ in range(nb):
                    add(loc, "Undermount basin and faucet", 0.42)
                add(loc, "Mirror", a["width"] - 0.1, 0.9)
                add(loc, "Vanity light bar", a["width"] - 0.3)
            elif k == "toilet":
                add(loc, "Toilet", a["width"], 0.82, 0.7)
            elif k == "tub":
                add(loc, "Alcove tub", a["width"], 0.56, 0.76)
                add(loc, "Tub surround tile, back wall", a["width"], 1.5)
                add(loc, "Tub/shower valve, head, and spout", 0.1)
            elif k == "shower":
                sd = a.get("depth", 0.9)
                add(loc, "Shower pan and curb", a["width"], 0.12, sd)
                add(loc, "Shower tile, back wall", a["width"], 2.0)
                add(loc, "Frameless glass with door", a["width"], 1.9, None, "plus side panel where not against a wall")
                add(loc, "Shower valve and head", 0.1)
            elif k == "fridge":
                add(loc, "Refrigerator opening", a["width"], 1.8, 0.7)
                add(loc, "Cabinet above refrigerator", a["width"], UPPER_Z1 - 1.86, BASE_D)
                blocked_upper.append((a0, a1))
            elif k == "range":
                add(loc, "Range opening", a["width"], 0.91, 0.65)
                add(loc, "Wall-mount hood", a["width"] + 0.04, 0.12, 0.5)
                blocked_upper.append((a0 - 0.02, a1 + 0.02))
            elif k == "cooktop":
                for dw in split(a["width"]):
                    add(loc, "Base cabinet under cooktop", dw, 0.9, BASE_D)
                add(loc, "Cooktop", a["width"] - 0.04, None, BASE_D - 0.11)
                add(loc, "Wall-mount hood", a["width"] + 0.04, 0.12, 0.5)
                blocked_upper.append((a0 - 0.02, a1 + 0.02))
            elif k == "sink":
                add(loc, "Sink base", a["width"], 0.9, BASE_D)
                add(loc, "Undermount sink", a["width"] - 0.12, None, BASE_D - 0.16)
            elif k == "dishwasher":
                add(loc, "Dishwasher opening", a["width"], 0.87, BASE_D)
            elif k == "pantry":
                add(loc, "Pantry, full height, split doors", a["width"], UPPER_Z1, BASE_D)
                blocked_upper.append((a0, a1))
            elif k == "wall_oven":
                add(loc, "Oven tower, double oven cutouts", a["width"], UPPER_Z1, BASE_D)
                blocked_upper.append((a0, a1))
            elif k == "corner":
                add(loc, "Lazy-susan corner base, 45-degree door", 0.9, 0.9, 0.9, "occupies 36 in. on each wall")
                add(loc, "Corner upper cabinet", UPPER_D + 0.55, UPPER_Z1 - UPPER_Z0, UPPER_D, "on the perpendicular wall")
            cursor = a1
        if bath:
            continue
        # counters
        skip = sorted([(a["at"], a["at"] + a["width"]) for a in apps if a["type"] in ("fridge", "range", "pantry", "wall_oven")])
        for c0, c1 in subtract((s, e), skip):
            if c1 - c0 > 0.02:
                add(loc, "Countertop section", c1 - c0, None, BASE_D + 0.025, spec.get("style", {}).get("counter", "white_quartz"))
                add(loc, "Backsplash, subway tile", c1 - c0, UPPER_Z0 - 0.94, None)
        if run.get("uppers", True):
            wins = [(w["u0"] - 0.03, w["u1"] + 0.03) for w in spec.get("windows", []) if w["wall"] == run["wall"]]
            for f0, f1 in subtract((s, e), blocked_upper + wins):
                if f1 - f0 >= 0.25:
                    for dw in split(f1 - f0):
                        add(loc, "Upper cabinet", dw, UPPER_Z1 - UPPER_Z0, UPPER_D)
                    add(loc, "Under-cabinet light strip", f1 - f0 - 0.1)
    isl = spec.get("island")
    if isl:
        add("Island", "Island base, door fronts one side", isl["width"], 0.9, isl["depth"])
        add("Island", "Island countertop", isl["width"] + 0.2, None, isl["depth"] + 0.2, spec.get("style", {}).get("counter", "white_quartz"))
        for _ in range(int(isl.get("pendants", 0))):
            add("Island", "Pendant light", 0.28)
        for _ in range(int(isl.get("stools", 0))):
            add("Island", "Counter stool", 0.36)
    for pen in spec.get("peninsulas", []):
        add("Peninsula", "Peninsula base, door fronts one side", pen["length"], 0.9, pen["width"])
        add("Peninsula", "Peninsula countertop, seating overhang", pen["length"] + 0.03, None, pen["width"] + 0.28, spec.get("style", {}).get("counter", "white_quartz"))
        for _ in range(int(pen.get("stools", 0))):
            add("Peninsula", "Counter stool", 0.36)
        for _ in range(int(pen.get("pendants", 0))):
            add("Peninsula", "Pendant light", 0.28)
    return rows


def summarize(rows):
    counts = {}
    for r in rows:
        key = (r["item"], r["width_in"])
        counts[key] = counts.get(key, 0) + 1
    return sorted(((item, w, n) for (item, w), n in counts.items()), key=lambda x: (x[0], x[1]))
