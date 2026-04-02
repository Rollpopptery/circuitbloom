# Session Lessons — 2026-04-02

## KiCad Socket

KiCad may only expose `api.sock` (not a numbered `api-XXXXX.sock`).
Pass it explicitly: `capture_kicad?socket=ipc:///tmp/kicad/api.sock`

Board file path from KiCad API: `board.get_project().path + "/" + board.name`
(`board.name` is just the filename, not a full path.)

## Viewer Auto-Refresh

`location.reload()` can serve cached pages. Fixed by using
`location.replace(path + '?v=' + version)` for cache-busting.

## add_track Has No Collision Checking

`add_track` places a raw segment — no grid validation, no clearance check.
A track can cross foreign nets silently.

**Workflow for manual segments:**
1. Place segment with `add_track`
2. Check `/clearance` immediately
3. If violations, delete and try a different path

The `route` action uses A* and respects the grid. `add_track` does not.

## Via Placement Must Be Reachable

Placing a via on the far side of an existing trace from the pad means the
stub connecting them will cross that trace. Visually "nearby" is not the
same as "reachable without crossing."

**Wrong approach:** Pick coordinates that look clear of pads, guess.

**Right approach:** Use `find_via_spot` — BFS flood-fill from the pad on its
layer through empty/same-net cells. First cell where a via fits on both
layers is guaranteed reachable without crossing any foreign trace.

## Push/Capture Sync

`push_kicad` adds tracks to KiCad — it does not replace them. If the server
state has tracks removed but KiCad still has the originals, pushing creates
duplicates. 

**Before pushing changed routes:** delete the affected tracks from KiCad first
(via kipy `board.remove_items()`), then push, then re-capture.

## Editing a Subsection of a Route

To edit specific segments without losing the rest of the net:

1. **Push current state to KiCad** — `push_kicad` so KiCad has everything
2. **Delete the bad segments in KiCad** — use kipy to surgically remove
   specific tracks/vias by filtering on net name and position:
   ```python
   board = kicad.get_board()
   bad = [t for t in board.get_tracks()
          if t.net.name == "GND"
          and min(t.start.x, t.end.x)/1e6 < 125
          and 76 < t.start.y/1e6 < 86]
   commit = board.begin_commit()
   board.remove_items(bad)
   board.push_commit(commit, "Remove bad GND leg")
   ```
3. **Re-capture from KiCad** — `capture_kicad` rebuilds the grid from
   KiCad's current state, which now has the clean board
4. **Re-route the section** — use `route`, `add_track`, or manual
   via+stub placement on the fresh grid

**Why this order matters — what goes wrong otherwise:**

- Deleting on the server then pushing does NOT work. `push_kicad` only
  adds tracks — it doesn't remove anything from KiCad. So KiCad still has
  the old segments and you get duplicates. We lost significant time to this.
- `unroute` is too coarse — it removes ALL tracks for a net. If you've
  routed 12 GND connections and one is bad, unroute destroys all of them.
- The server grid drifts from KiCad reality after manual edits. Always
  re-capture after KiCad changes so the grid matches what's actually there.

**Rule: KiCad is the source of truth. Edit there first, then re-capture.**

## Correct Workflow: Route New Section and Transfer to KiCad

Proven workflow for the XU2.3-C1.2 GND connection:

1. **Clean KiCad first** — delete bad segments via kipy `board.remove_items()`
2. **Re-capture** — `capture_kicad` to rebuild grid from clean KiCad state
3. **Find via spots** — `find_via_spot` with conservative clearance (margin=20 = 2mm)
4. **Mark and visually confirm** — use `mark` to preview in viewer before placing
5. **Place vias** — `place_via` at the confirmed spots
6. **Route stubs and link** — use `route` (not `add_track`) for F.Cu stubs and B.Cu link
7. **Push ONLY new items to KiCad** — use `push_tracks`/`push_vias` from
   `kicad_route.py` with just the new segments, NOT `push_kicad` which sends everything:
   ```python
   from kicad_route import push_tracks, push_vias
   push_tracks(socket, new_tracks, origin_x, origin_y)
   push_vias(socket, new_vias, origin_x, origin_y)
   ```
8. **Re-capture** — sync KiCad back to the viewer/grid

**Key: never use `push_kicad` for partial edits. Extract only new items
from server state and push those selectively.**

## Deleting Specific Segments

`unroute` removes ALL tracks for a net. For surgical edits:
- `delete_tracks` with a bounding box to remove specific segments
- `delete_via` with a bounding box to remove specific vias
- Use kipy directly for KiCad-side deletion by position

## Via Placement Strategy

Use `find_via_spot` with conservative clearance first, then tighten if needed:

1. Try `margin=20` (2mm clearance) — comfortable spacing
2. If no spot found, try `margin=10` (1mm) 
3. Last resort `margin=5` (0.5mm) — tight but may be acceptable

Also use `min_radius=15` (1.5mm) so the via isn't too close to the pad.
Mark the candidate in the viewer before committing to visually confirm.

Both XU2.3 and C1.2 vias placed successfully at 2mm clearance on first try.
XU2.3 via at (12.9, 11.3), C1.2 via at (9.0, 22.5).

## Straightening a Dog Leg

To replace a dog leg (e.g. pad→kink→via) with a clean straight segment:

1. **Identify the bad segments** — query server state, look for unnecessary
   intermediate points between pad and via
2. **Delete bad segments from KiCad** — use kipy, filter by net + position.
   Be surgical: match only the dog leg segments, not nearby legitimate traces
3. **Push the replacement** — a single straight segment via `push_tracks`
4. **Re-capture** to sync

**Watch out for collateral damage:** When filtering tracks by position,
nearby legitimate traces can match. We accidentally deleted the XU3→C1 trace
because it shared an endpoint with the remnants. Had to push it back.

**Lesson:** After deleting from KiCad, verify what was removed before
re-capturing. Print each deletion and sanity-check that only the intended
segments were hit.

## Reviewing Traces for Dog Legs

After routing, review the full trace for inefficient angles/dog legs before
moving on. The router's A* pathfinding can produce unnecessary jogs —
especially near pads and vias where it snaps to grid.

**What to look for:**
- Three segments that could be two (or one) — intermediate points that
  don't change direction meaningfully
- Short jogs perpendicular to the main trace direction
- Angles that aren't clean 0/45/90 when they should be

**Process:**
1. Query the segments for the route section
2. Mark suspicious points in the viewer to visually confirm
3. Delete and replace with cleaner geometry if needed

Do this review before pushing to KiCad — easier to fix in server state
than after transfer.

## T-Junction Strategy: Tap into Existing Traces

Instead of routing a new via+stub to a pad that already has a trace,
T-junction into the existing trace. This is shorter, cleaner, and avoids
creating unnecessary vias.

**How to find the T-junction point:**
1. Use `/nearest_track?net=GND&x=N&y=N` to find the closest point on an
   existing trace from where the new route needs to connect
2. Place a via at that point on the existing trace
3. Route B.Cu from the other via to the T-junction via
4. Remove the old dedicated via and stub that are no longer needed

**Example — XU2.3 to C1.2 GND:**
- XU2.3 already has a F.Cu trace running north to C2 at (11.94,14)→(11.3,7.3)
- Instead of a separate via at (12.9,11.3) with a -70° stub from XU2,
  place a via at (11.56,10.0) directly on the existing trace
- B.Cu runs from C1 via (9.0,22.5) to T-junction via (11.56,10.0)
- One fewer via, no awkward stub angle, shorter B.Cu run

**Rule: before placing a new via near a pad, check if there's an existing
trace on the same net that can be tapped with a T-junction.**

**Result:** Replaced 3 segments + 2 vias (including a -70° stub) with
1 B.Cu segment + 1 T-junction via. Clean straight diagonal from the
existing XU2→C2 trace down to the C1 via. Visually confirmed clean.

**Steps taken:**
1. Identified existing GND trace near XU2 using `/nearest_track`
2. Marked T-junction candidate in viewer to visually confirm
3. Deleted old via (12.9,11.3), stub, and B.Cu bend segments from KiCad via kipy
4. Pushed new T-junction via at (11.56,10.0) and single B.Cu segment to KiCad
5. Re-captured to sync

**How to sense a cleaner route without seeing it:**
- Fewer vias (each adds impedance and DRC risk)
- Fewer segments (less complexity)
- No odd angles (-70° replaced with reuse of existing trace)
- T-junction reuses existing copper instead of adding redundant paths

## Duplicate Tracks from Repeated Pushes

Multiple push cycles can leave duplicate tracks in KiCad (identical start/end
on the same net). These are invisible in the viewer but cause DRC issues.
After any cleanup, re-capture and check for segments that appear twice in
the server state — those are duplicates in KiCad that need removing.

## Save KiCad PCB Regularly

User saves the .kicad_pcb file in KiCad after confirmed route changes.
This is important — KiCad IPC edits (via kipy) are in-memory until saved.
A crash or accidental close loses all pushed routes. Save after each
successful routing section is confirmed visually.

## API Additions This Session

- `GET /drc` / `POST {"action": "drc"}` — KiCad CLI design rule check
- `GET /find_via_spot?net=X&x=N&y=N` — BFS via placement finder
- `POST {"action": "add_track"}` — manual single segment
- `POST {"action": "delete_tracks"}` — remove segments by bounding box
- `POST {"action": "delete_via"}` — remove vias by bounding box
- `--board` flag auto-populated from KiCad capture
