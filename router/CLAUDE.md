# Routing — rules for this directory

## Routing philosophy

Routing is done **by hand**. Traces are placed as explicit `add_track` segments in mm coordinates, chosen by reasoning about pad geometry, corridors, keep-outs, and patterns found in the `route_examples` database.

The A\* auto-router (`route` / `route_tap` actions) is a **last resort**, not the default. Its output is usually low-quality — wasted copper, ugly corners, tangles across adjacent nets. A human-quality routing session calls `route` rarely or never.

`route` is permitted only when either:
- (a) the net has ≤2 pads in an open region with no nearby foreign nets, **or**
- (b) hand-placement has been attempted and documented as infeasible for this specific net — written out before calling `route`.

Calling `route` without that justification is a process failure.

Once a route exists and passes clearance, it is kept. **Do not "polish" working routes by replacing them with hand-drawn segments** — the A\* router's corners exist because of real constraints you cannot see by eye. The hook is the exit, not a defect. Replacing a valid route with a straighter one that collides with a pad wastes an hour and breaks trust in the tools.

## Required reading

Before routing anything, read `utilities/router/ROUTER_NOTES.md` — the authoritative reference for the route server API, grid model, strategy notes, and KiCad IPC workflow.


## Clearance is authoritative

Clearance checking reads `grid.occupy[layer][y, x]` — the rasterised router grid. Each pad is rendered there from its real KiCad polygon and dilated by the design-rule clearance. **If a cell is marked as a foreign net, the trace is invalid.** There is no argument about pad size, pad radius, or distance arithmetic — the grid is the source of truth.

`handle_add_track` runs this check before accepting any manual segment and returns `{"ok": false, "error": "clearance_violation", ...}` on refusal. If you see that error, the segment is wrong — adjust the sketch, do not fight the checker.

The A\* router uses the same grid, so anything the router produces is cell-safe by construction. This is why "polishing" router output with hand segments tends to fail: the router's corners are navigating around dilation cells you cannot see in the render.

## Example failure to avoid

SPI_NSS was auto-routed correctly on the first call with a legal hook around U4 (25.2mm, clean, zero violations). The agent decided the hook was "ugly" and hand-placed a straight diagonal to replace it. The diagonal crossed U4.4 / U4.5 (LORA_DIO0), triggering a multi-hour chain through the clearance checker, the two-pass grid build, and the endpoint-exemption logic before arriving back at the same conclusion: the hook was the only valid exit from XU1.2 at 1.27mm pad pitch.

**Lesson:** a valid route with a corner is not a defect. Do not polish working routes.

## Process guide

Use the `hit_watchdog` tool as your step-by-step checklist. Follow its prompts in order and pass PASS / FAIL / YES / NO as requested. The watchdog exists to keep the routing session on the rails — do not skip steps, do not run ahead of it.
