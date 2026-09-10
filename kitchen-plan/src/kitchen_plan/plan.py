"""Dimensioned 2D floor plan of a kitchen or bathroom spec as SVG.

Ported from renderbench/plan.py. Coordinates: world x east, y north,
origin at the south-west floor corner; `u` runs left to right along a wall
as seen from inside the room.
"""
import html

S = 90.0          # px per metre
M = 80.0          # margin for dimension lines
WALL_T = 0.12
LABELS = {"fridge": "REF", "sink": "SINK", "range": "RANGE", "dishwasher": "DW", "pantry": "PANTRY",
          "wall_oven": "OVENS", "cooktop": "COOKTOP", "corner": "LAZY\nSUSAN",
          "vanity": "VANITY", "toilet": "WC", "tub": "TUB", "shower": "SHOWER"}
DEPTHS = {"fridge": 0.7, "range": 0.65, "vanity": 0.53, "toilet": 0.7, "tub": 0.76}
WALL_NAME = {"north": "Back wall", "south": "Front wall", "east": "Right wall", "west": "Left wall"}


def ft_in(m):
    total = int(round(m / 0.0254))
    ft, inch = divmod(total, 12)
    return f'{inch}"' if ft == 0 else f"{ft}'{inch}\""


class Plan:
    def __init__(self, spec):
        self.spec = spec
        r = spec["room"]
        self.W, self.D = r["width"], r["depth"]
        self.parts = []

    # world (x east, y north) -> svg (y down)
    def sx(self, x):
        return M + x * S

    def sy(self, y):
        return M + (self.D - y) * S

    def to_world(self, wall, u, v):
        if wall == "north":
            return (u, self.D - v)
        if wall == "south":
            return (self.W - u, v)
        if wall == "west":
            return (v, u)
        if wall == "east":
            return (self.W - v, self.D - u)
        raise ValueError(wall)

    def wall_len(self, wall):
        return self.W if wall in ("north", "south") else self.D

    def rect_wall(self, wall, u0, u1, v0, v1, fill, stroke="#333", label="", dash=False, sw=1.2):
        a = self.to_world(wall, u0, v0)
        b = self.to_world(wall, u1, v1)
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        self.rect_world(x0, y0, x1, y1, fill, stroke, label, dash, sw)

    def rect_world(self, x0, y0, x1, y1, fill, stroke="#333", label="", dash=False, sw=1.2):
        X, Y = self.sx(x0), self.sy(y1)
        w, h = (x1 - x0) * S, (y1 - y0) * S
        d = ' stroke-dasharray="4 3"' if dash else ""
        self.parts.append(f'<rect x="{X:.1f}" y="{Y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
        if label:
            lines = label.split("\n")
            fs = 10 if min(w, h) > 40 else 8
            cy = Y + h / 2 - (len(lines) - 1) * fs * 0.6
            for i, ln in enumerate(lines):
                self.parts.append(f'<text x="{X + w / 2:.1f}" y="{cy + i * fs * 1.2:.1f}" font-size="{fs}" text-anchor="middle" '
                                  f'dominant-baseline="middle" font-family="Segoe UI, Arial" fill="#222">{html.escape(ln)}</text>')

    def text(self, x, y, s, size=11, anchor="middle", rotate=0, color="#222", weight="normal"):
        rot = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate else ""
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-family="Segoe UI, Arial" '
                          f'fill="{color}" font-weight="{weight}"{rot}>{html.escape(s)}</text>')

    def dim_h(self, x0, x1, y_svg, label):
        a, b = self.sx(x0), self.sx(x1)
        self.parts.append(f'<line x1="{a:.1f}" y1="{y_svg:.1f}" x2="{b:.1f}" y2="{y_svg:.1f}" stroke="#666" stroke-width="1"/>')
        for x in (a, b):
            self.parts.append(f'<line x1="{x:.1f}" y1="{y_svg - 5:.1f}" x2="{x:.1f}" y2="{y_svg + 5:.1f}" stroke="#666" stroke-width="1"/>')
        self.text((a + b) / 2, y_svg - 4, label, 10, color="#444")

    def dim_v(self, y0, y1, x_svg, label):
        a, b = self.sy(y1), self.sy(y0)
        self.parts.append(f'<line x1="{x_svg:.1f}" y1="{a:.1f}" x2="{x_svg:.1f}" y2="{b:.1f}" stroke="#666" stroke-width="1"/>')
        for y in (a, b):
            self.parts.append(f'<line x1="{x_svg - 5:.1f}" y1="{y:.1f}" x2="{x_svg + 5:.1f}" y2="{y:.1f}" stroke="#666" stroke-width="1"/>')
        self.text(x_svg - 4, (a + b) / 2, label, 10, rotate=-90, color="#444")

    def build(self):
        spec = self.spec
        W, D, t = self.W, self.D, WALL_T
        # floor and walls
        self.rect_world(-t, -t, W + t, D + t, "#555", "#333")
        self.rect_world(0, 0, W, D, "#fbfaf7", "#333")
        # openings and windows as gaps in the wall band
        for w in spec.get("windows", []):
            self.rect_wall(w["wall"], w["u0"], w["u1"], -t, 0, "#dff0ff", "#333")
            self.rect_wall(w["wall"], w["u0"], w["u1"], -t / 2 - 0.01, -t / 2 + 0.01, "#333", "#333", sw=0.5)
        for o in spec.get("openings", []):
            self.rect_wall(o["wall"], o["u0"], o["u1"], -t - 0.002, 0.002, "#fbfaf7", "#fbfaf7", sw=0)
        # cabinet runs
        for run in spec.get("cabinet_runs", []):
            wall = run["wall"]
            s, e = run["start"], run["end"]
            apps = sorted(run.get("appliances", []), key=lambda a: a["at"])
            bath = run.get("kind") == "bath"
            if not bath:
                self.rect_wall(wall, s, e, 0, 0.6, "#ffffff", "#333")
            if run.get("uppers", not bath):
                self.rect_wall(wall, s, e, 0, 0.35, "none", "#888", dash=True, sw=0.8)
            for a in apps:
                a0, a1 = a["at"], a["at"] + a["width"]
                kind = a["type"]
                if kind == "corner":
                    left = a0 <= s + 0.01
                    if left:
                        self.rect_wall(wall, a0, a0 + 0.6, 0.6, 0.9, "#f1efe8", "#333")
                    else:
                        self.rect_wall(wall, a1 - 0.6, a1, 0.6, 0.9, "#f1efe8", "#333")
                    self.rect_wall(wall, a0, a1, 0, 0.6, "#f1efe8", "#333", LABELS[kind])
                    continue
                depth = a.get("depth", DEPTHS.get(kind, 0.9 if kind == "shower" else 0.6))
                fill = "#e8eef5" if kind in ("fridge", "range", "dishwasher", "wall_oven", "cooktop", "toilet", "tub", "shower") else "#f1efe8"
                self.rect_wall(wall, a0, a1, 0, depth, fill, "#333", LABELS.get(kind, kind.upper()))
                if kind == "sink":
                    self.rect_wall(wall, a0 + 0.08, a1 - 0.08, 0.08, 0.5, "#dfe7ee", "#333", sw=0.8)
                if kind in ("range", "cooktop"):
                    for bu, bv in ((0.27, 0.3), (0.73, 0.3), (0.27, 0.7), (0.73, 0.7)):
                        x, y = self.to_world(wall, a0 + bu * (a1 - a0), 0.05 + bv * 0.5)
                        self.parts.append(f'<circle cx="{self.sx(x):.1f}" cy="{self.sy(y):.1f}" r="5" fill="none" stroke="#333" stroke-width="0.8"/>')
            # position ticks along the run
            for a in apps:
                x, y = self.to_world(wall, a["at"], 0.72)
                self.text(self.sx(x), self.sy(y), ft_in(a["at"] - s), 8, color="#666")
        # island / peninsulas
        isl = spec.get("island")
        if isl:
            x0, x1 = isl["x"] - isl["width"] / 2, isl["x"] + isl["width"] / 2
            y0, y1 = isl["y"] - isl["depth"] / 2, isl["y"] + isl["depth"] / 2
            self.rect_world(x0 - 0.1, y0 - 0.1, x1 + 0.1, y1 + 0.1, "#ffffff", "#333", "", sw=0.8)
            self.rect_world(x0, y0, x1, y1, "#f1efe8", "#333", f"ISLAND\n{ft_in(isl['width'])} x {ft_in(isl['depth'])}")
            for i in range(int(isl.get("stools", 0))):
                sx = x0 + (i + 0.5) * (isl["width"] / isl["stools"])
                self.parts.append(f'<circle cx="{self.sx(sx):.1f}" cy="{self.sy(y0 - 0.38):.1f}" r="{0.18 * S:.1f}" fill="#fff" stroke="#333" stroke-width="0.8"/>')
        for pen in spec.get("peninsulas", []):
            wall, at, w, ln = pen["wall"], pen["at"], pen["width"], pen["length"]
            self.rect_wall(wall, at, at + w, 0, ln, "#f1efe8", "#333", f"PENINSULA\n{ft_in(ln)} x {ft_in(w)}")
            left = pen.get("doors_side", "left") == "left"
            su = at + w + 0.38 if left else at - 0.38
            for i in range(int(pen.get("stools", 0))):
                sv = 0.15 + (i + 0.5) * ((ln - 0.3) / pen["stools"])
                x, y = self.to_world(wall, su, sv)
                self.parts.append(f'<circle cx="{self.sx(x):.1f}" cy="{self.sy(y):.1f}" r="{0.18 * S:.1f}" fill="#fff" stroke="#333" stroke-width="0.8"/>')
        # dimensions and wall names
        self.dim_h(0, W, M - 26, ft_in(W))
        self.dim_v(0, D, M - 26, ft_in(D))
        self.text(self.sx(W / 2), self.sy(D) - 50, WALL_NAME["north"], 10, color="#777")
        self.text(self.sx(W / 2), self.sy(0) + 30, WALL_NAME["south"], 10, color="#777")
        self.text(self.sx(0) - 54, self.sy(D / 2), WALL_NAME["west"], 10, rotate=-90, color="#777")
        self.text(self.sx(W) + 30, self.sy(D / 2), WALL_NAME["east"], 10, rotate=90, color="#777")
        r = spec["room"]
        self.text(self.sx(W / 2), self.sy(0) + 50, f"Ceiling {ft_in(r['height'])}   Scale: plan, not to print scale", 9, color="#777")

    def svg(self):
        self.build()
        width = M * 2 + self.W * S
        height = M * 2 + self.D * S
        body = "\n".join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" width="{width:.0f}" height="{height:.0f}">'
                f'<rect width="100%" height="100%" fill="#ffffff"/>{body}</svg>')


def render_svg(spec: dict, title: str | None = None) -> str:
    """Return the SVG document for a validated spec in metres."""
    p = Plan(spec)
    svg = p.svg()
    label = title or spec.get("title") or spec.get("name")
    if label:
        svg = svg.replace("</svg>", f'<text x="{M:.0f}" y="{M - 48:.0f}" font-size="13" font-family="Segoe UI, Arial" fill="#222" font-weight="bold">{html.escape(str(label))}</text></svg>')
    return svg
