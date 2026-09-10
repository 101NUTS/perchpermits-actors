# Kitchen & Bath Floor Plan + Cabinet Takeoff

Give it a short JSON description of a kitchen or bathroom. Get back, in about two seconds:

1. **A dimensioned floor plan** as SVG: walls, windows, doorways, cabinet runs, every appliance and fixture in place, island or peninsula with seating, wall labels, and overall dimensions in feet and inches.
2. **A cabinet takeoff** as CSV: every base cabinet, upper, filler, corner unit, pantry, oven tower, counter section, backsplash run, appliance opening, and bath fixture, with widths in inches. The same list a kitchen designer hands to a cabinet shop for a quote.
3. **Quote-ready totals** as JSON: linear feet of base, upper, and tall cabinets, countertop and backsplash square feet, appliance and fixture lists, seating and light counts.

No CAD, no rendering engine, no accounts on design tools. One call, one price.

Typical callers: an agent turning a homeowner's description into a first layout and budget, a lead-gen flow that wants a plan image for an outreach email, a contractor's estimating script, an AI interior design assistant that needs real dimensions rather than a picture.

## Quick start

Minimum input, a one-wall kitchen in metres:

```json
{
  "spec": {
    "room": { "width": 4.2, "depth": 3.6, "height": 2.7 },
    "cabinet_runs": [
      { "wall": "north", "start": 0, "end": 4.2, "appliances": [
        { "type": "fridge", "at": 0.0, "width": 0.9 },
        { "type": "sink", "at": 1.9, "width": 0.8 },
        { "type": "range", "at": 3.2, "width": 0.76 }
      ] }
    ],
    "island": { "x": 2.1, "y": 1.5, "width": 2.0, "depth": 0.9, "pendants": 2, "stools": 3 }
  }
}
```

The same room in feet (`"units": "ft"`), an L-shaped kitchen with a corner unit:

```json
{
  "units": "ft",
  "spec": {
    "title": "L kitchen, 14 x 12",
    "room": { "width": 14, "depth": 12, "height": 9 },
    "windows": [ { "wall": "north", "u0": 5, "u1": 8, "z0": 3.5, "z1": 6.5 } ],
    "cabinet_runs": [
      { "wall": "north", "start": 0, "end": 14, "appliances": [
        { "type": "corner", "at": 0, "width": 3 },
        { "type": "sink", "at": 5.5, "width": 2.5 },
        { "type": "dishwasher", "at": 8, "width": 2 },
        { "type": "range", "at": 10.5, "width": 2.5 }
      ] },
      { "wall": "west", "start": 0, "end": 9, "appliances": [
        { "type": "fridge", "at": 6, "width": 3 }
      ] }
    ]
  }
}
```

A hall bathroom:

```json
{
  "spec": {
    "room": { "width": 2.5, "depth": 3.0, "height": 2.44 },
    "cabinet_runs": [
      { "wall": "north", "kind": "bath", "start": 0, "end": 2.5, "uppers": false, "appliances": [
        { "type": "tub", "at": 0.0, "width": 1.52 },
        { "type": "toilet", "at": 1.85, "width": 0.5 }
      ] },
      { "wall": "west", "kind": "bath", "start": 0, "end": 3.0, "uppers": false, "appliances": [
        { "type": "vanity", "at": 0.85, "width": 1.22 }
      ] }
    ]
  }
}
```

Check a spec without building (free apart from the start fee):

```json
{ "validateOnly": true, "spec": { "...": "..." } }
```

## Coordinate system

- The room is a rectangle. Origin is the south-west floor corner. `width` runs east, `depth` runs north.
- Walls are `north`, `south`, `east`, `west`. On each wall, `u` measures **left to right as seen from inside the room**, starting at 0.
- A cabinet run occupies `start..end` along its wall. Appliances sit `at` a position along that run, each `width` wide. Anything not covered by an appliance is filled with cabinets automatically.
- The island is placed by its centre `x, y` in room coordinates. A peninsula is attached to a wall at `at` and sticks `length` into the room.

## Spec reference

| Key | Fields | Notes |
|---|---|---|
| `room` | `width`, `depth`, `height` | Required. |
| `title` | string | Printed on the plan. |
| `style.counter` | `white_quartz`, `marble`, `butcher_block`, `black_granite` | Labels the countertop lines in the takeoff. Default `white_quartz`. |
| `windows[]` | `wall`, `u0`, `u1`, `z0`, `z1` | Uppers are skipped across windows. |
| `openings[]` | `wall`, `u0`, `u1`, `z1` | Doorways and cased openings. `z1` is head height, default 2.05 m. |
| `cabinet_runs[]` | `wall`, `start`, `end`, `uppers` (default true), `kind` (`bath` for fixture runs), `appliances[]` | See types below. |
| `appliances[]` | `type`, `at`, `width`, optional `depth` (shower) | |
| `island` | `x`, `y`, `width`, `depth`, `pendants`, `stools` | Warns when clearance to a counter is under 0.9 m. |
| `peninsulas[]` | `wall`, `at`, `width`, `length`, `doors_side` (`left`/`right`), `stools`, `pendants` | Counter attached to a wall, doors one side, seating the other. |

Appliance and fixture types:

| Type | What the takeoff produces |
|---|---|
| `fridge` | Refrigerator opening plus cabinet above. |
| `range` | Range opening plus wall-mount hood. |
| `cooktop` | Base cabinets under the cooktop, cooktop cutout, hood. |
| `wall_oven` | Full-height double-oven tower. |
| `sink` | Sink base plus undermount sink. |
| `dishwasher` | Dishwasher opening. |
| `pantry` | Full-height pantry with split doors. |
| `corner` | Lazy-susan corner base with 45-degree door plus corner upper. Must sit at the start or end of its run; end the perpendicular run 0.9 m from the corner. |
| `vanity` | Vanity bases in 30 in. modules, countertop, one or two basins, mirror, light bar. Bath runs only. |
| `toilet`, `tub`, `shower` | Fixture, tile surround, valve set, glass for showers. Bath runs only. |

Fields the renderer in the original pipeline uses (`lighting`, `cameras`, `style.cabinet_color`, and so on) are accepted and ignored, so a spec written for a photoreal render works here unchanged.

## Output

Files in the run's key-value store, with public URLs returned in `OUTPUT`:

| Key | Content |
|---|---|
| `plan.svg` | The floor plan. Scales cleanly to any size; convert to PNG or PDF with any SVG tool. |
| `takeoff.csv` | Columns `location, item, width_in, height_in, depth_in, note`. |
| `summary.json` | The totals below. |
| `OUTPUT` | `{ ok, warnings[], summary, plan_svg_url, takeoff_csv_url, takeoff_rows }`. |

The takeoff lines are also pushed to the run's dataset, one item per line.

Summary fields:

```json
{
  "room": { "width": "13'9\"", "depth": "11'10\"", "ceiling": "8'10\"", "floor_area_sqft": 162.7, "width_m": 4.2, "depth_m": 3.6, "height_m": 2.7 },
  "room_type": "kitchen",
  "cabinet_count": 9,
  "base_cabinet_linear_ft": 14.9,
  "upper_cabinet_linear_ft": 6.6,
  "tall_cabinet_linear_ft": 0.0,
  "countertop_sqft": 43.3,
  "backsplash_sqft": 6.0,
  "wall_tile_sqft": 0.0,
  "counter_finish": "white_quartz",
  "appliances": ["range", "range hood", "refrigerator", "kitchen sink"],
  "fixtures": [],
  "seating": 3,
  "light_fixtures": 4,
  "line_items": 27,
  "item_counts": [ { "item": "Base cabinet, drawer over door", "count": 5 }, "..." ]
}
```

## Validation

Every spec is checked before anything is drawn. A run with errors fails with the list in its status message and in `OUTPUT`, and is **not charged**. Warnings (tight island clearance, unusual appliance widths, dimensions that look like the wrong units) are returned alongside a successful build.

Common errors and what they mean:

| Message | Fix |
|---|---|
| `... falls outside the run` | `at + width` must be within `start..end`. |
| `... overlaps ...` | Two appliances share space on the same run. |
| `a corner unit must sit at the start or end of its run` | Move the corner to `at: start` or `at: end - width`. |
| `... needs the run to have "kind": "bath"` | Vanity, toilet, tub, and shower go in a bath run. |
| `island extends outside the room` | Check `x`, `y` are the island centre, not a corner. |

## Pricing

One `plan` event per successful build. Validate-only runs and failed validations are not charged.

## Limits

Rectangular rooms only. No angled walls, no vaulted ceilings, no open shelving, no wall cabinets without a base run beneath. Rendering, 3D models, and photoreal images are a separate, slower product.

## Running locally

```bash
python -m src.kitchen_plan.cli tests/fixtures/kitchen_example.json --out out/example
python -m unittest tests.test_kitchen_plan
```

The `kitchen_plan` package is standard library only.
