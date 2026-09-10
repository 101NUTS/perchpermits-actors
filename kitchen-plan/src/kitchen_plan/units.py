"""Convert a spec written in feet or inches into metres.

Only length fields are touched. Everything else (types, names, colours,
counts) passes through untouched. The converted spec is a deep copy.
"""

from __future__ import annotations

import copy

FACTORS = {"m": 1.0, "ft": 0.3048, "in": 0.0254}

_ROOM = ("width", "depth", "height")
_WINDOW = ("u0", "u1", "z0", "z1")
_OPENING = ("u0", "u1", "z1")
_RUN = ("start", "end")
_APPLIANCE = ("at", "width", "depth")
_ISLAND = ("x", "y", "width", "depth")
_PENINSULA = ("at", "width", "length")


def _scale(d: dict, keys: tuple[str, ...], f: float) -> None:
    for k in keys:
        if isinstance(d.get(k), (int, float)) and not isinstance(d[k], bool):
            d[k] = round(d[k] * f, 6)


def to_metres(spec: dict, units: str = "m") -> dict:
    if units not in FACTORS:
        raise ValueError(f"units must be one of {', '.join(FACTORS)} (got {units!r})")
    f = FACTORS[units]
    out = copy.deepcopy(spec)
    if f == 1.0:
        return out
    if isinstance(out.get("room"), dict):
        _scale(out["room"], _ROOM, f)
    for w in out.get("windows") or []:
        _scale(w, _WINDOW, f)
    for o in out.get("openings") or []:
        _scale(o, _OPENING, f)
    for run in out.get("cabinet_runs") or []:
        _scale(run, _RUN, f)
        for a in run.get("appliances") or []:
            _scale(a, _APPLIANCE, f)
    if isinstance(out.get("island"), dict):
        _scale(out["island"], _ISLAND, f)
    for p in out.get("peninsulas") or []:
        _scale(p, _PENINSULA, f)
    return out
