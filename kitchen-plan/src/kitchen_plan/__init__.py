"""Kitchen and bathroom floor plan plus cabinet takeoff from a JSON spec.

Pure standard library. Ported from renderbench's plan.py and takeoff.py
(the Blender-free half of that pipeline) with validation, unit conversion,
and a quote summary added for agent use.
"""

from .plan import render_svg
from .summary import summarize
from .takeoff import takeoff
from .units import to_metres
from .validate import validate

__all__ = ["render_svg", "takeoff", "summarize", "to_metres", "validate"]
