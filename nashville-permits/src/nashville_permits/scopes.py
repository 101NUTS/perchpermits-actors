"""Scope presets: what kind of work a permit describes.

Each preset has three parts:

* ``server``   coarse substrings pushed into the ArcGIS WHERE clause so the
               server does most of the narrowing;
* ``positive`` regexes, at least one must match the combined text
               (type + subtype + purpose);
* ``negative`` regexes stripped from the text before the positive test, so
               boilerplate like "not to add a second kitchen" cannot match.

The kitchen preset is ported from renderbench's leads/pull_permits.py, which
was tuned against 3,558 Nashville permits. The trade presets follow the
permit-pulse rule of matching on type/subtype first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scope:
    key: str
    label: str
    server: tuple[str, ...]
    positive: tuple[str, ...]
    negative: tuple[str, ...] = ()
    _pos: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)
    _neg: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_pos", tuple(re.compile(p, re.I) for p in self.positive))
        object.__setattr__(self, "_neg", tuple(re.compile(p, re.I) for p in self.negative))

    def matches(self, text: str) -> bool:
        if not text:
            return False
        for n in self._neg:
            text = n.sub(" ", text)
        return any(p.search(text) for p in self._pos)


_KITCHEN_POS = (
    r"kitchen\s+(remodel|renovat|reno\b|update|cabinet|island|counter|layout|reconfig|expansion|addition|refinish)",
    r"(remodel|renovat|renovation|reno|update|updating|refinish|reconfigur|redo|gut)\w*\s+(of\s+|the\s+|existing\s+|to\s+)*(the\s+)?(existing\s+)?kitchen",
    r"new\s+kitchen",
    r"kitchen\s*(/|and|&)\s*(bath|laundry|living|dining)",
    r"(bath|bathrooms?|laundry)\s*(/|and|&)\s*kitchen",
    r"kitchen\s+cabinets?",
    r"cabinetry",
    r"open(ing)?\s+(up\s+)?(the\s+)?kitchen",
    r"load\s*bearing\s+wall\s+(in|between)\s+(the\s+)?kitchen",
    r"relocat\w*\s+(the\s+)?kitchen",
    r"kitchen\s+(and\s+)?bath",
    r"including\s+(the\s+)?kitchen",
    r"include\w*\s+(a\s+|the\s+|all\s+)?(new\s+)?kitchen",
    r"kitchen\s+area",
)
_KITCHEN_NEG = (
    r"second\s+kitchen", r"one\s+kitchen\s+allowed", r"2nd\s+kitchen", r"outdoor\s+kitchen",
    r"kitchenette", r"commercial\s+kitchen", r"restaurant", r"kitchen\s+exhaust",
    r"hood\s+suppression", r"not\s+to\s+add\s+a\s+kitchen", r"no\s+kitchen",
)

SCOPES: dict[str, Scope] = {
    s.key: s
    for s in [
        Scope("any", "Any scope", (), (r".",)),
        Scope("kitchen", "Kitchen remodel", ("KITCHEN",), _KITCHEN_POS, _KITCHEN_NEG),
        Scope(
            "bathroom", "Bathroom remodel", ("BATH",),
            (r"bath(room)?s?\s+(remodel|renovat|reno\b|update|addition|expansion)",
             r"(remodel|renovat|update|redo|gut)\w*\s+(of\s+|the\s+|existing\s+)*(the\s+)?bath",
             r"new\s+(master\s+|primary\s+|half\s+|full\s+)?bath", r"master\s+bath", r"primary\s+bath",
             r"bath(room)?\s*(/|and|&)\s*kitchen", r"kitchen\s*(/|and|&)\s*bath"),
            (r"bath\s*house", r"birdbath"),
        ),
        Scope(
            "roofing", "Roofing and siding", ("ROOF", "SIDING", "SHINGLE"),
            (r"\bre-?roof", r"\broof(ing)?\b", r"\bshingle", r"\bsiding\b", r"\btpo\b", r"membrane"),
            (r"roof\s*deck", r"roof\s*top\s+(bar|patio|terrace)", r"waterproof"),
        ),
        Scope(
            "pool", "Swimming pool or spa", ("POOL", "SPA", "HOT TUB"),
            (r"\bpools?\b", r"hot\s+tub", r"\bspa\b"),
            (r"carpool", r"pool\s*house\s+only", r"liquor"),
        ),
        Scope(
            "fence", "Fence or retaining wall", ("FENC", "RETAINING"),
            (r"\bfenc", r"retaining\s+wall"),
        ),
        Scope(
            "deck", "Deck, porch, or patio", ("DECK", "PORCH", "PATIO"),
            (r"\bdecks?\b", r"\bporch", r"\bpatio", r"\bpergola", r"screened?\s+(in\s+)?porch"),
            (r"roof\s*deck", r"pool\s+deck"),
        ),
        Scope(
            "addition", "Addition to an existing house", ("ADDITION", "ADD "),
            (r"\baddition", r"\badd(ing)?\s+(a\s+|an\s+)?(\d+\s*(sq|sf)|bedroom|bathroom|room|story|floor|second)"),
            (r"in\s+addition\s+to", r"no\s+addition"),
        ),
        Scope(
            "new_construction", "New single-family or duplex build", ("NEW", "CONSTRUCT"),
            (r"new\s+(single[- ]family|sf\b|residen|dwelling|duplex|townho|home|house|construction)",
             r"construct\w*\s+(a\s+)?(new\s+)?(single[- ]family|residen|dwelling|duplex|townho|home|house)"),
            (r"no\s+new\s+construction",),
        ),
        Scope(
            "adu", "Accessory dwelling unit / DADU", ("ACCESSORY", "DADU", "ADU"),
            (r"\badu\b", r"\bdadu\b", r"accessory\s+dwelling", r"detached\s+accessory", r"garage\s+apartment", r"mother[- ]in[- ]law"),
        ),
        Scope(
            "garage", "Garage or carport", ("GARAGE", "CARPORT"),
            (r"\bgarage", r"\bcarport"),
            (r"garage\s+apartment", r"garage\s+sale"),
        ),
        Scope(
            "demolition", "Demolition", ("DEMO",),
            (r"\bdemol", r"\bdemo\b", r"tear\s*down", r"raze"),
            (r"interior\s+demo", r"demo\s+(of\s+)?(non[- ]?load|walls?)"),
        ),
        Scope(
            "solar", "Solar installation", ("SOLAR", "PHOTOVOLTAIC", "PV "),
            (r"\bsolar", r"photovoltaic", r"\bpv\b"),
        ),
        Scope(
            "hvac", "Mechanical / HVAC", ("MECHANICAL", "HVAC", "FURNACE", "HEAT PUMP"),
            (r"\bhvac", r"mechanical", r"furnace", r"heat\s+pump", r"air\s+condition", r"\bmini[- ]split"),
        ),
        Scope(
            "electrical", "Electrical", ("ELECTRIC",),
            (r"\belectric", r"\bpanel\s+(upgrade|replace)", r"\bservice\s+upgrade", r"ev\s+charg"),
        ),
        Scope(
            "plumbing", "Plumbing / gas", ("PLUMB", "GAS", "WATER HEATER", "SEWER"),
            (r"\bplumb", r"\bgas\s+(line|pipe|service)", r"water\s+heater", r"\bsewer", r"tankless"),
        ),
        Scope(
            "foundation", "Foundation / structural repair", ("FOUNDATION", "STRUCTURAL", "PIER"),
            (r"\bfoundation", r"structural\s+repair", r"\bpiers?\b", r"underpin", r"crawl\s*space\s+(repair|encapsul)"),
            (r"new\s+foundation\s+for\s+new",),
        ),
        Scope(
            "windows_doors", "Window and door replacement", ("WINDOW", "DOOR"),
            (r"\bwindows?\b", r"\bdoors?\b"),
            (r"garage\s+door\s+opener",),
        ),
        Scope(
            "interior_remodel", "General interior remodel", ("REMODEL", "RENOVAT", "REHAB", "INTERIOR"),
            (r"\bremodel", r"\brenovat", r"\brehab", r"interior\s+(alteration|renovation|remodel|finish)"),
        ),
        Scope(
            "commercial", "Commercial work", ("COMMERCIAL", "TENANT", "RESTAURANT", "RETAIL", "OFFICE"),
            (r"commercial", r"tenant\s+(finish|build|improvement)", r"restaurant", r"retail", r"\boffice", r"warehouse", r"\bshell\b"),
        ),
        Scope(
            "multifamily", "Multi-family / townhomes", ("MULTI", "TOWNH", "APARTMENT", "CONDO"),
            (r"multi[- ]?family", r"townho", r"apartment", r"condo", r"\bunits?\b"),
        ),
        Scope(
            "short_term_rental", "Short-term rental", ("SHORT TERM", "STRP", "RENTAL"),
            (r"short[- ]term\s+rental", r"\bstrp?\b", r"owner[- ]occupied\s+rental", r"not\s+owner[- ]occupied"),
        ),
        Scope(
            "sign", "Signage", ("SIGN",),
            (r"\bsign(age|s)?\b", r"\bbillboard"),
            (r"design", r"assign", r"signature", r"signed"),
        ),
    ]
}

RESIDENTIAL_RE = re.compile(
    r"single[- ]family|\bsf\b|residen|dwelling|duplex|townho|condo|accessory\s+dwelling|\badu\b|\bdadu\b|house|home\b",
    re.I,
)
COMMERCIAL_RE = re.compile(r"commercial|tenant|restaurant|retail|office|warehouse|industrial|hotel|church|school", re.I)


def classify(text: str, presets: list[str] | None = None) -> list[str]:
    """Return the keys of every preset (except 'any') that matches `text`."""
    keys = presets or [k for k in SCOPES if k != "any"]
    return [k for k in keys if k != "any" and SCOPES[k].matches(text)]


def is_residential(text: str) -> bool | None:
    """True/False when the text says so, None when it is ambiguous."""
    if not text:
        return None
    r, c = bool(RESIDENTIAL_RE.search(text)), bool(COMMERCIAL_RE.search(text))
    if r and not c:
        return True
    if c and not r:
        return False
    return None
