"""
============================================================================
GAD / BAFFLE PIPELINE
============================================================================
Single, self-contained script that takes the two source drawings:

    <PREFIX>_GAD.DXF               - the full General Arrangement Drawing
    <PREFIX>_CUTTING MATERIAL.DXF  - the sheet-metal nesting/cutting layout

...and runs the complete pipeline that was built up step by step in this
project, end to end, in one run:

  STEP 1  Detect every "capsule / stadium" shape (two semicircular arcs
          joined by two straight sides - the slotted heating-element-sleeve
          holes) in the GAD drawing.
  STEP 2  Work out each capsule's (width, overall_length) and collapse them
          to the unique sizes present -> <PREFIX>_capsule_sizes.json
  STEP 3  Render the whole GAD sheet with every capsule boxed in red
          -> <PREFIX>_capsules_highlighted.png (+ one zoomed-in PNG per cluster)
  STEP 4  Take the capsule size with the highest occurrence count, and for
          every instance of it find the smallest circle (real CIRCLE entity
          OR a circle reconstructed from two/more ARC entities that sweep
          the full 360 degrees) that fully encloses it
          -> <PREFIX>_highest_count_capsule_circle.png / .json
  STEP 5  Export that circle plus every entity inside it as its own,
          standalone DXF -> <PREFIX>_highest_count_capsule_circle_extract.dxf
  STEP 6  In the CUTTING MATERIAL drawing, find the "full baffle": the
          radius that shows up BOTH as a complete (uncut) CIRCLE and as a
          large, non-full ARC (the segmental/cut baffle) somewhere in the
          file. The full CIRCLE at that radius, plus every hole inside it,
          is exported as its own DXF + JSON, with no text/notes
          -> <PREFIX>_full_baffle.dxf / _full_baffle.json
  STEP 7  Overlay the Step 5 capsule-circle DXF and the Step 6 full-baffle
          DXF on one plot (both aligned to their own outer-circle center),
          with no scaling of either drawing - they are compared exactly as
          drawn,
          save that as a PNG, and write a JSON "diff" (radius difference,
          capsule/hole counts, nearest-neighbour center offsets)
          -> <PREFIX>_gadscaled_vs_baffle_overlay.png / _gadscaled_vs_baffle_diff.json
  STEP 8  Merge the Step 2/4/6/7 JSON outputs into one combined summary
          JSON: every nearest-neighbour match from Step 7 gets the winning
          capsule's own size (width/overall_length/radius/straight_length)
          attached to it, alongside the enclosing-circle and baffle-size
          context, so the whole capsule-vs-hole comparison can be read
          from a single file
          -> <PREFIX>_merged_capsule_baffle_match.json

----------------------------------------------------------------------------
HOW TO RUN
----------------------------------------------------------------------------
The two source DXFs are now passed in as command-line arguments instead of
being hard-coded. Everything else - every output filename and the output
folder itself - is generated automatically from the GAD file's name.

    python gad_baffle_pipeline.py <GAD_DXF_PATH> <CUTTING_DXF_PATH>

Example:

    python gad_baffle_pipeline.py "2193000995_GAD.DXF" "2193000995_CUTTING MATERIAL.DXF"

The "prefix" (job/drawing number) is taken as everything in the GAD file's
name before its first underscore, e.g. "2193000995_GAD.DXF" -> "2193000995".
Every output file is then named "<prefix>_<thing>.<ext>", e.g.:

    2193000995_capsule_sizes.json
    2193000995_capsules_highlighted.png
    2193000995_highest_count_capsule_circle.png
    2193000995_highest_count_capsule_circle.json
    2193000995_highest_count_capsule_circle_extract.dxf
    2193000995_full_baffle.dxf
    2193000995_full_baffle.json
    2193000995_gadscaled_vs_baffle_overlay.png
    2193000995_gadscaled_vs_baffle_diff.json
    2193000995_merged_capsule_baffle_match.json

...and all of them are written into a single output folder named
"<prefix>_output", e.g. "2193000995_output/", created next to where the
script is run (use --output-dir to override the location/name).

Optional flags:

    --output-dir PATH   write outputs to this folder instead of the
                         auto-generated "<prefix>_output"
    --prefix TEXT        use this prefix instead of the one derived from
                         the GAD filename (useful if your filenames don't
                         follow the "<PREFIX>_GAD.DXF" convention)
============================================================================
"""

import argparse
import json
import math
import os

import ezdxf
import matplotlib

matplotlib.use("Agg")  # render to files, no interactive window needed
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle, Arc as MplArc
from matplotlib.lines import Line2D

from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.importer import Importer
from ezdxf import bbox as ezdxf_bbox


# ============================================================================
# TUNING CONSTANTS - the tolerances/thresholds every step is built on
# ============================================================================

SIZE_ROUND = 2                     # decimal places used when rounding/deduping sizes
TOL = 0.08                         # point-matching tolerance (drawing units) for "is there a
                                    # line between these two points"
RADIUS_TOL = 0.05                  # relative radius-match tolerance when pairing two arcs
ANGLE_LO, ANGLE_HI = 178.0, 182.0  # a (possibly merged) arc run with an included angle in this
                                    # window counts as "roughly a semicircle" (a capsule end-cap)
ANGLE_MERGE_TOL = 1.0              # (degrees) arcs on the same circle whose angular spans touch
                                    # or overlap within this tolerance are merged into one run -
                                    # this is what lets an end-cap drawn as several arc pieces
                                    # (e.g. two 90 degree quarter-circles) be recognized the same
                                    # way a single 180 degree arc entity would be

CIRCLE_FIT_TOL = 0.5               # slack when checking if a circle covers a set of points
CIRCLE_EXTRACT_TOL = 0.5           # slack when checking if an entity's bbox lies inside a circle

DISC_RADIUS_THRESHOLD = 30.0       # (baffle step) entities bigger than this are "disc boundaries",
                                    # not small bolt/tie-rod holes
FULL_SWEEP_TOL_DEG = 2.0           # (baffle step) an arc within this many degrees of 360 counts
                                    # as "a full circle", not a cut/segmental one
BAFFLE_EXCLUDE_TYPES = {           # entity types that are never geometry we want to keep -
    "TEXT", "MTEXT", "INSERT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "MULTILEADER", "HATCH",
}


# ============================================================================
# ARGUMENT HANDLING - derive the job prefix, output folder and every
# output filename from the two input DXF paths passed on the command line
# ============================================================================

def derive_prefix(gad_path):
    """
    Derive the job/drawing-number prefix from the GAD file's name: everything
    before its first underscore, e.g. "2193000995_GAD.DXF" -> "2193000995".
    Falls back to the filename stem (no extension) if there's no underscore.
    """
    stem = os.path.splitext(os.path.basename(gad_path))[0]
    return stem.split("_")[0] if "_" in stem else stem


def build_paths(gad_dxf_path, cutting_dxf_path, output_dir=None, prefix=None):
    """
    Build the dict of every path the pipeline reads from / writes to, given
    just the two input DXF paths (plus optional overrides for the output
    folder and the prefix used to name every output file).

    Returns a dict with keys: gad_dxf, cutting_dxf, output_dir, and one key
    per output artifact (capsule_sizes_json, capsules_highlighted_png,
    highest_count_png, highest_count_json, circle_extract_dxf,
    baffle_dxf, baffle_json, trace_diff_png, trace_diff_json,
    merged_match_json) - each already prefixed and placed inside output_dir.
    """
    prefix = prefix or derive_prefix(gad_dxf_path)
    output_dir = output_dir or f"{prefix}_output"
    os.makedirs(output_dir, exist_ok=True)

    def out(name):
        return os.path.join(output_dir, f"{prefix}_{name}")

    return {
        "prefix": prefix,
        "output_dir": output_dir,
        "gad_dxf": gad_dxf_path,
        "cutting_dxf": cutting_dxf_path,
        "capsule_sizes_json": out("capsule_sizes.json"),
        "capsules_highlighted_png": out("capsules_highlighted.png"),
        "highest_count_png": out("highest_count_capsule_circle.png"),
        "highest_count_json": out("highest_count_capsule_circle.json"),
        "circle_extract_dxf": out("highest_count_capsule_circle_extract.dxf"),
        "baffle_dxf": out("full_baffle.dxf"),
        "baffle_json": out("full_baffle.json"),
        "trace_diff_png": out("gadscaled_vs_baffle_overlay.png"),
        "trace_diff_json": out("gadscaled_vs_baffle_diff.json"),
        "merged_match_json": out("merged_capsule_baffle_match.json"),
    }


# ============================================================================
# SMALL SHARED GEOMETRY HELPERS
# ============================================================================

def dist(p, q):
    """Plain 2D Euclidean distance between two (x, y) points."""
    return math.hypot(p[0] - q[0], p[1] - q[1])


def arc_point(center, radius, angle_deg):
    """A point on a circle (`center`, `radius`) at the given angle (degrees)."""
    a = math.radians(angle_deg)
    return (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a))


def _merge_circular_intervals(intervals, gap_tol_deg=ANGLE_MERGE_TOL):
    """
    Merge a list of (start_deg, end_deg) angular intervals that lie on the same
    circle into the smallest set of contiguous runs, treating the circle as
    circular (a run may cross the 0/360 boundary).

    Standard trick for circular interval merging: pick a "cut" angle that sits
    in the widest gap between any two interval boundaries (so no interval
    straddles it), rotate everything so that cut is at angle 0, merge as a
    plain 1D interval list (no wraparound to worry about any more), then
    rotate the result back.
    """
    if not intervals:
        return []

    boundaries = sorted({round(s % 360, 6) for s, e in intervals} | {round(e % 360, 6) for s, e in intervals})
    if len(boundaries) == 1:
        cut = boundaries[0]
    else:
        best_gap, cut = -1.0, 0.0
        for i in range(len(boundaries)):
            a, b = boundaries[i], boundaries[(i + 1) % len(boundaries)]
            gap = (b - a) % 360 or 360.0
            if gap > best_gap:
                best_gap, cut = gap, (a + gap / 2) % 360

    rotated = []
    for s, e in intervals:
        sweep = (e - s) % 360 or 360.0
        rs = (s - cut) % 360
        rotated.append((rs, rs + sweep))
    rotated.sort()

    merged = []
    for s, e in rotated:
        if merged and s <= merged[-1][1] + gap_tol_deg:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    return [((s + cut) % 360, (s + cut) % 360 + (e - s)) for s, e in merged]


def merge_adjacent_arcs(arcs, center_round=2, radius_round=2):
    """
    Group ARC entities that share the same (center, radius) - i.e. lie on the
    same circle - and merge every run of angularly-adjacent arcs within each
    group into a single combined arc run. This generalizes capsule end-cap
    detection to however many pieces a drawing happens to split a rounded end
    into: a single 180 degree arc, two 90 degree quarter-circles, three 60
    degree thirds, etc. all become one merged ~180 degree run here.

    Returns a flat list of dicts: {"r", "center", "start_angle", "end_angle",
    "p1", "p2"} - one per merged run.
    """
    groups = {}
    for a in arcs:
        cc = (a.dxf.center.x, a.dxf.center.y)
        key = (round(cc[0], center_round), round(cc[1], center_round), round(a.dxf.radius, radius_round))
        groups.setdefault(key, []).append(a)

    runs = []
    for (cx, cy, r), members in groups.items():
        intervals = [(m.dxf.start_angle % 360, m.dxf.end_angle % 360) for m in members]
        for s, e in _merge_circular_intervals(intervals):
            center = (cx, cy)
            runs.append({
                "r": r,
                "center": center,
                "start_angle": s,
                "end_angle": e,
                "p1": arc_point(center, r, s),
                "p2": arc_point(center, r, e),
            })
    return runs


# ============================================================================
# STEP 1-2 : find capsule/stadium shapes in the GAD drawing, and their sizes
# ============================================================================

def detect_capsules(msp):
    """
    Find capsule/stadium shapes in `msp` (a DXF modelspace/layout).

    A capsule here means: two rounded end-caps, each roughly a semicircle
    (included angle between ANGLE_LO and ANGLE_HI degrees) once same-circle
    arc pieces are merged together (see merge_adjacent_arcs - this is what
    makes an end-cap drawn as a single 180 degree ARC and one drawn as two
    90 degree quarter-circles both count the same way), with matching
    radius, whose open ends are joined by straight connectors - either a
    LINE entity, or an *open* LWPOLYLINE (some capsule sides in these files
    are flattened into a many-vertex polyline instead of a single LINE, so
    we treat a polyline's first and last vertex the same way we'd treat a
    LINE's start/end).

    Steps:
      1. Merge angularly-adjacent same-circle arcs into combined runs, and
         keep only the runs whose total swept angle is ~180 degrees ("semis").
      2. Build a lookup of every straight connector's two endpoints
         (from LINE entities and from open LWPOLYLINE entities).
      3. Try every pair of semicircle runs with a matching radius; if a
         straight connector exists between their "p1" ends AND another
         one exists between their "p2" ends (in either matching order),
         the two runs + two connectors form a closed capsule outline.

    Returns:
      (capsules, semicircle_count)
        capsules            - list of (arc_a_dict, arc_b_dict) pairs, where
                               each arc dict is {"r", "p1", "p2", "center",
                               "start_angle", "end_angle"}.
        semicircle_count    - how many semicircular end-cap runs were found
                               in total (only used for the console progress
                               message).
    """
    arcs = [e for e in msp if e.dxftype() == "ARC"]
    lines = [e for e in msp if e.dxftype() == "LINE"]
    open_polylines = [e for e in msp if e.dxftype() == "LWPOLYLINE" and not e.closed]

    # --- 1. merge same-circle arc pieces into runs, keep only the ~semicircular ones ---
    semis = []
    for run in merge_adjacent_arcs(arcs):
        included = (run["end_angle"] - run["start_angle"]) % 360 or 360.0
        if ANGLE_LO <= included <= ANGLE_HI:
            semis.append(run)

    # --- 2. build the "straight connector" endpoint lookup ---
    line_pts = [((l.dxf.start[0], l.dxf.start[1]), (l.dxf.end[0], l.dxf.end[1])) for l in lines]
    for pl in open_polylines:
        pts = pl.get_points("xy")
        if len(pts) >= 2:
            line_pts.append((pts[0], pts[-1]))  # only the polyline's two open ends matter here

    def line_exists(p, q):
        """True if some straight connector runs (approximately) from point p to point q."""
        for s, e in line_pts:
            if (dist(s, p) < TOL and dist(e, q) < TOL) or (dist(s, q) < TOL and dist(e, p) < TOL):
                return True
        return False

    # --- 3. pair up semicircle arcs that are joined by two straight connectors ---
    capsules = []
    used = set()
    n = len(semis)
    for i in range(n):
        if i in used:
            continue
        a = semis[i]
        for j in range(i + 1, n):
            if j in used:
                continue
            b = semis[j]
            # radii must match (within a relative tolerance) - a capsule has one radius
            if abs(a["r"] - b["r"]) > RADIUS_TOL * max(a["r"], b["r"], 1e-6):
                continue
            # the two arcs' open ends can be wired up in either rotational order
            pairing1 = line_exists(a["p1"], b["p1"]) and line_exists(a["p2"], b["p2"])
            pairing2 = line_exists(a["p1"], b["p2"]) and line_exists(a["p2"], b["p1"])
            if pairing1 or pairing2:
                capsules.append((a, b))
                used.add(i)
                used.add(j)
                break  # arc `a` is now spoken for, move on to the next unused arc

    return capsules, len(semis)


def capsule_dimensions(a, b):
    """
    Turn a capsule pair (a, b) - two matching semicircle-arc dicts - into
    its physical dimensions:
      radius          - average of the two arcs' radii (they should match)
      straight_length - distance between the two arc centers (the length
                        of the capsule's straight sides)
      overall_length  - tip-to-tip length (straight_length + one radius on
                        each end)
      width           - the capsule's short-axis size (2 * radius)
    """
    radius = (a["r"] + b["r"]) / 2
    straight_length = dist(a["center"], b["center"])
    overall_length = straight_length + 2 * radius
    width = 2 * radius
    return width, overall_length, straight_length, radius


def export_capsule_sizes_json(capsules, path, ndigits=SIZE_ROUND):
    """
    Compute (width, overall_length) for every capsule, collapse them down to
    the unique sizes present (rounded to `ndigits`), and write a JSON summary:
    each unique size once, with a `count` of how many capsules share it.
    """
    sizes = {}
    for a, b in capsules:
        width, overall_length, straight_length, radius = capsule_dimensions(a, b)
        key = (round(width, ndigits), round(overall_length, ndigits))
        entry = sizes.setdefault(key, {
            "width": key[0],
            "overall_length": key[1],
            "radius": round(radius, ndigits),
            "straight_length": round(straight_length, ndigits),
            "count": 0,
        })
        entry["count"] += 1

    unique_sizes = sorted(sizes.values(), key=lambda s: (s["width"], s["overall_length"]))

    with open(path, "w") as f:
        json.dump({
            "unique_capsule_count": len(unique_sizes),
            "total_capsules": len(capsules),
            "sizes": unique_sizes,
        }, f, indent=2)

    print(f"Saved: {path} ({len(unique_sizes)} unique size(s) out of {len(capsules)} capsules)")
    return unique_sizes


# ============================================================================
# STEP 3 : render the GAD sheet with every capsule boxed in red
# ============================================================================

def capsule_box_corners(a, b):
    """
    Build a small rectangle (4 corner points) that tightly frames one
    capsule, for drawing a highlight box around it.

    The rectangle is built in the capsule's own local axes: `ux, uy` points
    along the capsule (from arc `a`'s center to arc `b`'s center), and
    `px, py` is perpendicular to that. `half_len` extends the box half a
    radius past each arc's tip, and `pad` extends it slightly wider than
    the capsule's radius, so the highlight box has a little breathing room.
    """
    r = max(a["r"], b["r"])
    cx = (a["center"][0] + b["center"][0]) / 2
    cy = (a["center"][1] + b["center"][1]) / 2
    dx = b["center"][0] - a["center"][0]
    dy = b["center"][1] - a["center"][1]
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm   # unit vector along the capsule's long axis
    px, py = -uy, ux                # unit vector perpendicular to it
    pad = r * 1.35
    half_len = norm / 2 + r * 0.25
    return [
        (cx - ux * half_len - px * pad, cy - uy * half_len - py * pad),
        (cx - ux * half_len + px * pad, cy - uy * half_len + py * pad),
        (cx + ux * half_len + px * pad, cy + uy * half_len + py * pad),
        (cx + ux * half_len - px * pad, cy + uy * half_len - py * pad),
    ]


def draw_capsule_highlight(ax, a, b, color="red", linewidth=2.2):
    """Draw a capsule_box_corners() rectangle onto a matplotlib axis as an outline (no fill)."""
    poly = Polygon(capsule_box_corners(a, b), closed=True, fill=False,
                    edgecolor=color, linewidth=linewidth, zorder=10)
    ax.add_patch(poly)


def render_capsules_highlighted(doc, msp, capsules, out_path):
    """
    Render the full DXF layout using ezdxf's built-in matplotlib backend
    (this draws every entity - lines, text, dimensions, etc. - exactly as
    a CAD viewer would), then overlay a red highlight box around every
    detected capsule, and save the result as one big PNG.
    """
    fig = plt.figure(figsize=(24, 18), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp, finalize=True)

    for a, b in capsules:
        draw_capsule_highlight(ax, a, b, color="red")

    ax.autoscale(False)
    fig.savefig(out_path)
    plt.close(fig)
    print(f"Saved: {out_path}")
    return out_path


# ============================================================================
# STEP 4 : highest-count capsule size -> its smallest enclosing circle
# ============================================================================

def _angle_coverage_is_full(intervals, tol_deg=1.0):
    """
    Given a list of (start_deg, end_deg) angle intervals (end > start),
    return True if, laid end to end, they cover the *entire* 360-degree
    circle with no gap bigger than `tol_deg`.

    How: duplicate every interval shifted by +360 degrees (so an interval
    that wraps past 0/360 still merges correctly with one on the other
    side), sort all the interval start points, then merge any intervals
    that touch or overlap (classic "merge intervals"). If any merged run
    is close enough to 360 degrees long, the arcs form a closed ring.
    """
    pts = []
    for s, e in intervals:
        pts.append((s, e))
        pts.append((s + 360, e + 360))
    pts.sort()
    merged = []
    for s, e in pts:
        if merged and s <= merged[-1][1] + tol_deg:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return any(e - s >= 360 - tol_deg for s, e in merged)


def collect_circles(msp, center_round=2, radius_round=2):
    """
    Collect every "circle" in the drawing that a covering-circle search
    should consider:
      (a) every real CIRCLE entity, and
      (b) every full circle that was instead drawn as two-or-more ARC
          entities sharing the same center/radius which together sweep
          the complete 360 degrees (common in these files, e.g. a ring
          drawn as two 180-degree-ish halves).

    Returns a flat list of plain dicts: {"center": (x, y), "radius": r}.
    """
    circles = []
    seen = set()

    # (a) real CIRCLE entities
    for e in msp:
        if e.dxftype() == "CIRCLE":
            cc = (e.dxf.center.x, e.dxf.center.y)
            key = (round(cc[0], center_round), round(cc[1], center_round), round(e.dxf.radius, radius_round))
            if key not in seen:
                seen.add(key)
                circles.append({"center": cc, "radius": e.dxf.radius})

    # (b) group every ARC by its (center, radius), so arcs that belong to
    #     the same ring end up in the same bucket regardless of which
    #     angular slice of the ring each one draws
    arc_groups = {}
    for e in msp:
        if e.dxftype() == "ARC":
            cc = (e.dxf.center.x, e.dxf.center.y)
            key = (round(cc[0], center_round), round(cc[1], center_round), round(e.dxf.radius, radius_round))
            arc_groups.setdefault(key, []).append(e)

    # for each (center, radius) group with 2+ arcs, check whether their
    # angular spans, merged together, cover the full circle
    for key, arclist in arc_groups.items():
        if len(arclist) < 2 or key in seen:
            continue
        intervals = []
        for a in arclist:
            sa = a.dxf.start_angle % 360
            ea = a.dxf.end_angle % 360
            if ea <= sa:
                ea += 360  # handle the arc wrapping past 0 degrees
            intervals.append((sa, ea))
        if _angle_coverage_is_full(intervals):
            seen.add(key)
            circles.append({"center": (key[0], key[1]), "radius": key[2]})

    return circles


def capsule_sample_points(a, b, npts=10):
    """
    Sample `npts + 1` evenly-spaced points along each of a capsule's two
    arcs (so 2*(npts+1) points total). Used to test "does this circle fully
    contain the capsule" - checking sampled points is a cheap stand-in for
    checking the arc's whole continuous curve.
    """
    pts = []
    for arc in (a, b):
        c = arc["center"]
        r = arc["r"]
        sa = math.radians(arc["start_angle"])
        ea = math.radians(arc["end_angle"])
        if ea < sa:
            ea += 2 * math.pi
        for k in range(npts + 1):
            t = sa + (ea - sa) * k / npts
            pts.append((c[0] + r * math.cos(t), c[1] + r * math.sin(t)))
    return pts


def find_enclosing_circle(points, circles, tol=CIRCLE_FIT_TOL):
    """
    Return the *smallest* circle (from `circles`, a list of {"center",
    "radius"} dicts) whose interior fully contains every point in `points`
    (i.e. every point's distance from the circle's center is <= its radius
    + `tol`). Returns None if no circle covers all the points.
    """
    best = None
    for circ in circles:
        cc = circ["center"]
        cr = circ["radius"]
        if all(dist(cc, p) <= cr + tol for p in points):
            if best is None or cr < best["radius"]:
                best = circ
    return best


def find_highest_count_group_with_circles(capsules, circles):
    """
    1. Group all detected capsules by their rounded (width, overall_length).
    2. Pick the size group with the most members (the "highest count" size).
    3. For every capsule in that group, find its smallest enclosing circle
       (from `collect_circles`'s output).

    Returns:
      (best_key, results)
        best_key - the (width, overall_length) tuple of the winning size
        results  - list of {"pair": (a, b), "circle": circle_or_None} for
                   every capsule in that size group
    """
    groups = {}
    for a, b in capsules:
        width, overall_length, _, _ = capsule_dimensions(a, b)
        key = (round(width, SIZE_ROUND), round(overall_length, SIZE_ROUND))
        groups.setdefault(key, []).append((a, b))

    best_key = max(groups, key=lambda k: len(groups[k]))
    best_group = groups[best_key]

    results = []
    for a, b in best_group:
        pts = capsule_sample_points(a, b)
        circle = find_enclosing_circle(pts, circles)
        results.append({"pair": (a, b), "circle": circle})

    return best_key, results


def render_and_save_highest_count_group(doc, msp, best_key, results, png_path, json_path):
    """
    Re-render the DXF layout fresh (its own figure), draw a red highlight
    box around every capsule in the highest-count size group, draw its
    enclosing circle(s) in blue, zoom the view to fit them, and save both
    a PNG and a JSON summary (capsule centers + their enclosing circle).
    """
    width, overall_length = best_key

    fig2 = plt.figure(figsize=(14, 14), dpi=200)
    ax2 = fig2.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax2)).draw_layout(msp, finalize=True)

    xs, ys = [], []
    drawn_circles = set()   # avoids drawing the exact same circle twice
    capsule_records = []

    for item in results:
        a, b = item["pair"]
        circle = item["circle"]
        draw_capsule_highlight(ax2, a, b, color="red")
        for pt in (a["p1"], a["p2"], b["p1"], b["p2"], a["center"], b["center"]):
            xs.append(pt[0])
            ys.append(pt[1])

        circle_info = None
        if circle is not None:
            cc = circle["center"]
            cr = circle["radius"]
            circle_info = {"center": [round(cc[0], SIZE_ROUND), round(cc[1], SIZE_ROUND)],
                           "radius": round(cr, SIZE_ROUND)}
            key = (round(cc[0], 3), round(cc[1], 3), round(cr, 3))
            if key not in drawn_circles:
                drawn_circles.add(key)
                ax2.add_patch(Circle(cc, cr, fill=False, edgecolor="deepskyblue",
                                      linewidth=2.5, zorder=9))
            xs.extend([cc[0] - cr, cc[0] + cr])
            ys.extend([cc[1] - cr, cc[1] + cr])

        mid = ((a["center"][0] + b["center"][0]) / 2, (a["center"][1] + b["center"][1]) / 2)
        capsule_records.append({
            "capsule_center": [round(mid[0], SIZE_ROUND), round(mid[1], SIZE_ROUND)],
            "enclosing_circle": circle_info,
        })

    pad = 40
    ax2.set_xlim(min(xs) - pad, max(xs) + pad)
    ax2.set_ylim(min(ys) - pad, max(ys) + pad)
    fig2.savefig(png_path)
    plt.close(fig2)
    print(f"Saved: {png_path}")

    with open(json_path, "w") as f:
        json.dump({
            "capsule_size": {"width": width, "overall_length": overall_length, "count": len(results)},
            "capsules": capsule_records,
            "unique_enclosing_circles": [
                {"center": list(k[:2]), "radius": k[2]} for k in
                {(r["enclosing_circle"]["center"][0], r["enclosing_circle"]["center"][1],
                  r["enclosing_circle"]["radius"])
                 for r in capsule_records if r["enclosing_circle"] is not None}
            ],
        }, f, indent=2)
    print(f"Saved: {json_path}")

    return capsule_records


# ============================================================================
# STEP 5 : export the highest-count-group circle + everything inside it
# ============================================================================

ARC_SAMPLE_COUNT = 24  # points sampled along an ARC's sweep for the containment check below


def entity_inside_circle(entity, center, radius, tol=CIRCLE_EXTRACT_TOL):
    """
    True if `entity` lies entirely within the circle at `center`/`radius`,
    tested against the entity's *actual* geometry rather than its
    axis-aligned bounding box.

    The previous implementation checked the 4 corners of the entity's
    bounding box. For anything that isn't axis-aligned - a diagonal LINE,
    an ARC, an off-axis LWPOLYLINE segment - two of those four corners are
    empty space, not real geometry, and can sit noticeably farther from
    `center` than the entity itself ever does. Near the extraction
    boundary this caused real, fully-enclosed entities to be dropped: a
    diagonal capsule-side LINE (breaking that capsule's pairing in
    STEP 7's re-detection) and a small hole CIRCLE in the baffle (missing
    from full_baffle.dxf/json) both had bounding-box corners that poked
    just past `radius + tol` even though the entities themselves were
    comfortably inside.

    This version tests the right thing per entity type instead:
      CIRCLE       - exact: dist(centers) + entity_radius <= radius + tol
                     (a circle is fully inside another iff this holds -
                     no approximation needed)
      LINE         - exact: both endpoints within radius + tol (a straight
                     segment's point farthest from `center` is always one
                     of its two endpoints, since distance-to-a-fixed-point
                     is a convex function along a line)
      LWPOLYLINE   - exact, by the same reasoning: every vertex within
                     radius + tol (checking each straight sub-segment via
                     its two endpoints)
      ARC          - densely sampled along the actual sweep (not the
                     bounding box of its full underlying circle): an arc
                     only bulges out along the portion of the circle it
                     actually covers, so this stays much closer to the
                     arc's true extent than a bounding box does
      anything else - falls back to the previous bounding-box-corner
                     check, so unusual entity types are still handled.
    """
    cx, cy = center

    def pt_ok(p):
        return dist((cx, cy), p) <= radius + tol

    t = entity.dxftype()

    if t == "CIRCLE":
        c = (entity.dxf.center.x, entity.dxf.center.y)
        return dist((cx, cy), c) + entity.dxf.radius <= radius + tol

    if t == "LINE":
        s = (entity.dxf.start.x, entity.dxf.start.y)
        e = (entity.dxf.end.x, entity.dxf.end.y)
        return pt_ok(s) and pt_ok(e)

    if t == "LWPOLYLINE":
        pts = entity.get_points("xy")
        if not pts:
            return False
        return all(pt_ok(p) for p in pts)

    if t == "ARC":
        c = (entity.dxf.center.x, entity.dxf.center.y)
        r = entity.dxf.radius
        sweep = (entity.dxf.end_angle - entity.dxf.start_angle) % 360 or 360.0
        pts = [arc_point(c, r, entity.dxf.start_angle + sweep * k / ARC_SAMPLE_COUNT)
               for k in range(ARC_SAMPLE_COUNT + 1)]
        return all(pt_ok(p) for p in pts)

    # fallback for any other entity type: previous bounding-box-corner check
    bb = ezdxf_bbox.extents([entity], fast=True)
    if not bb.has_data:
        return False
    corners = [
        (bb.extmin.x, bb.extmin.y), (bb.extmin.x, bb.extmax.y),
        (bb.extmax.x, bb.extmin.y), (bb.extmax.x, bb.extmax.y),
    ]
    return all(pt_ok(p) for p in corners)


def save_dxf_within_circle(doc, msp, circle, out_path, tol=CIRCLE_EXTRACT_TOL):
    """
    Build a brand-new, standalone DXF file containing `circle` itself, plus
    every entity from `msp` whose bounding box lies fully inside it.

    Uses ezdxf's Importer (rather than a raw entity.copy()) because the
    Importer also pulls across the resources those entities depend on -
    their layers, linetypes, etc. - so the exported file is self-contained
    and opens cleanly in any DXF viewer.
    """
    inside_entities = [e for e in msp if entity_inside_circle(e, circle["center"], circle["radius"], tol)]

    new_doc = ezdxf.new(dxfversion=doc.dxfversion)
    new_msp = new_doc.modelspace()

    importer = Importer(doc, new_doc)
    importer.import_entities(inside_entities, target_layout=new_msp)
    importer.finalize()

    new_msp.add_circle(
        center=circle["center"],
        radius=circle["radius"],
        dxfattribs={"layer": "0", "color": 5},  # color 5 = blue
    )

    new_doc.saveas(out_path)
    print(f"Saved: {out_path} ({len(inside_entities)} entities + enclosing circle)")
    return out_path, len(inside_entities)


# ============================================================================
# STEP 6 : extract the FULL BAFFLE from the cutting-material layout
# ============================================================================

def arc_sweep(entity):
    """The angle (degrees, 0-360) that an ARC entity sweeps through."""
    return (entity.dxf.end_angle - entity.dxf.start_angle) % 360


def find_baffle_radius(msp):
    """
    Work out the "baffle radius" with no text/label reading at all.

    The cutting layout contains several disc-shaped parts: some as full
    CIRCLE entities, some as ARC entities with a chord cut off ("segmental"
    baffles). The FULL BAFFLE and the SEGMENTAL BAFFLE share the same
    nominal radius, so: any radius that shows up BOTH as a complete CIRCLE
    and as a large, non-full ARC is the baffle's radius.
    """
    full_circle_radii = set()
    cut_arc_radii = set()

    for e in msp:
        if e.dxftype() == "CIRCLE" and e.dxf.radius > DISC_RADIUS_THRESHOLD:
            full_circle_radii.add(round(e.dxf.radius, 1))
        elif e.dxftype() == "ARC" and e.dxf.radius > DISC_RADIUS_THRESHOLD:
            if arc_sweep(e) < 360 - FULL_SWEEP_TOL_DEG:
                cut_arc_radii.add(round(e.dxf.radius, 1))

    shared = full_circle_radii & cut_arc_radii
    if not shared:
        raise RuntimeError("Could not find a radius shared by a full CIRCLE and a cut ARC "
                            "(no full-vs-segmental baffle pair detected).")
    return sorted(shared)[0]


def extract_full_baffle(dxf_path, out_dxf_path, out_json_path):
    """
    Load the cutting-material DXF, find the full baffle's outer radius
    (find_baffle_radius), then for every full CIRCLE at that radius:
      - collect every small hole (CIRCLE/ARC/LINE) whose bounding box lies
        fully inside it, skipping any other disc boundary and skipping
        every text/annotation entity type (BAFFLE_EXCLUDE_TYPES)
      - record its center, outer radius, and the list of holes
    Save the outer circle + its holes as a standalone DXF, and a JSON
    summary of the radius/hole geometry.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    baffle_radius = find_baffle_radius(msp)

    # there may be more than one full-baffle instance nested in the layout
    outer_circles = [e for e in msp if e.dxftype() == "CIRCLE" and abs(e.dxf.radius - baffle_radius) < 0.5]
    if not outer_circles:
        raise RuntimeError(f"No full CIRCLE found at the detected baffle radius {baffle_radius}.")

    new_doc = ezdxf.new(dxfversion=doc.dxfversion)
    new_msp = new_doc.modelspace()
    importer = Importer(doc, new_doc)

    baffles_json = []
    all_entities_to_import = []

    for outer in outer_circles:
        center = (outer.dxf.center.x, outer.dxf.center.y)
        radius = outer.dxf.radius

        holes = []
        for e in msp:
            if e is outer or e.dxftype() in BAFFLE_EXCLUDE_TYPES:
                continue
            if e.dxftype() not in ("CIRCLE", "ARC", "LINE"):
                continue
            if e.dxftype() == "CIRCLE" and e.dxf.radius >= radius - 0.5:
                continue  # this is some other disc boundary, not a hole - skip it
            if entity_inside_circle(e, center, radius):
                holes.append(e)

        all_entities_to_import.append(outer)
        all_entities_to_import.extend(holes)

        hole_records = [
            {
                "type": "CIRCLE",
                "center": [round(h.dxf.center.x, SIZE_ROUND), round(h.dxf.center.y, SIZE_ROUND)],
                "radius": round(h.dxf.radius, SIZE_ROUND),
            }
            for h in holes if h.dxftype() == "CIRCLE"
        ]

        baffles_json.append({
            "center": [round(center[0], SIZE_ROUND), round(center[1], SIZE_ROUND)],
            "outer_radius": round(radius, SIZE_ROUND),
            "outer_diameter": round(radius * 2, SIZE_ROUND),
            "hole_count": len(hole_records),
            "unique_hole_radii": sorted({r["radius"] for r in hole_records}),
            "holes": hole_records,
        })

    importer.import_entities(all_entities_to_import, target_layout=new_msp)
    importer.finalize()
    new_doc.saveas(out_dxf_path)
    print(f"Saved: {out_dxf_path} ({len(all_entities_to_import)} entities, "
          f"{len(outer_circles)} full-baffle instance(s))")

    with open(out_json_path, "w") as f:
        json.dump({
            "baffle_radius": round(baffle_radius, SIZE_ROUND),
            "baffle_diameter": round(baffle_radius * 2, SIZE_ROUND),
            "instance_count": len(baffles_json),
            "baffles": baffles_json,
        }, f, indent=2)
    print(f"Saved: {out_json_path}")

    return out_dxf_path, out_json_path


# ============================================================================
# STEP 7 : overlay the capsule-circle DXF and the full-baffle DXF, and diff
#          them - no scaling of either drawing, compared exactly as drawn
# ============================================================================

def draw_entities(ax, msp, shift=(0.0, 0.0), color="red", linewidth=1.2):
    """
    Hand-draw CIRCLE/ARC/LINE/LWPOLYLINE entities from `msp` onto a
    matplotlib axis, offset by `shift`. Used instead of ezdxf's own
    renderer here because we need to (a) translate two different drawings
    into one shared coordinate frame and (b) force each drawing to a
    single overlay color, neither of which the built-in renderer does.
    """
    dx, dy = shift
    for e in msp:
        t = e.dxftype()
        if t == "CIRCLE":
            c = e.dxf.center
            ax.add_patch(Circle((c.x + dx, c.y + dy), e.dxf.radius, fill=False,
                                 edgecolor=color, linewidth=linewidth, zorder=5))
        elif t == "ARC":
            c = e.dxf.center
            ax.add_patch(MplArc((c.x + dx, c.y + dy), 2 * e.dxf.radius, 2 * e.dxf.radius,
                                 theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                 edgecolor=color, linewidth=linewidth, zorder=5))
        elif t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            ax.plot([s.x + dx, en.x + dx], [s.y + dy, en.y + dy], color=color, linewidth=linewidth, zorder=5)
        elif t == "LWPOLYLINE":
            pts = e.get_points("xy")
            xs = [p[0] + dx for p in pts]
            ys = [p[1] + dy for p in pts]
            if e.closed and pts:
                xs.append(xs[0])
                ys.append(ys[0])
            ax.plot(xs, ys, color=color, linewidth=linewidth, zorder=5)


def trace_and_diff_dxfs(gad_path, baffle_path, out_png, out_json, highlight_capsule_index=None):
    """
    Overlay the capsule-cluster DXF and the full-baffle DXF on the same
    plot, and diff them. Neither drawing is resized - both are compared
    exactly as they were drawn/extracted.

    Steps:
      1. Load both DXFs. In each one, the largest CIRCLE entity is that
         drawing's own "outer boundary" circle.
      2. Both drawings live in different, unrelated coordinate systems (the
         GAD sheet vs. the cutting layout), so align them by shifting each
         one so its own outer-circle center sits at (0, 0). After that
         shift they're directly comparable.
      3. Re-detect the capsules in the capsule DXF, and read every hole
         CIRCLE out of the baffle DXF, both translated into the shared
         (0, 0)-centered frame.
      4. For every capsule, find its nearest baffle hole (straight-line
         distance) and record that as the "diff": if the two designs truly
         match, capsule centers should sit at consistent, explainable
         offsets from real holes (not scattered randomly).
      5. Draw both drawings on one matplotlib axis - capsules in red, baffle
         holes in blue - and save as a PNG. If `highlight_capsule_index` is
         given (the `capsule_index` from a match record), that capsule and
         its matched hole are additionally ringed in yellow so a single
         match can be pointed out on the image (used by the Streamlit app's
         "click a match to highlight it" feature).
    """
    doc_g = ezdxf.readfile(gad_path)
    msp_g = doc_g.modelspace()
    doc_b = ezdxf.readfile(baffle_path)
    msp_b = doc_b.modelspace()

    # --- 1. find each drawing's own outer boundary circle ---
    outer_g = max((e for e in msp_g if e.dxftype() == "CIRCLE"), key=lambda e: e.dxf.radius)
    outer_b = max((e for e in msp_b if e.dxftype() == "CIRCLE"), key=lambda e: e.dxf.radius)
    cg = (outer_g.dxf.center.x, outer_g.dxf.center.y)
    rg = outer_g.dxf.radius
    cb = (outer_b.dxf.center.x, outer_b.dxf.center.y)
    rb = outer_b.dxf.radius

    # --- 2 & 3. capsule centers and hole centers, both shifted to a shared (0,0) frame ---
    capsules_g, _ = detect_capsules(msp_g)
    capsule_centers = [
        ((a["center"][0] + b["center"][0]) / 2 - cg[0], (a["center"][1] + b["center"][1]) / 2 - cg[1])
        for a, b in capsules_g
    ]
    holes_b = [
        {"center": (e.dxf.center.x - cb[0], e.dxf.center.y - cb[1]), "radius": e.dxf.radius}
        for e in msp_b if e.dxftype() == "CIRCLE" and e is not outer_b
    ]

    # --- 4. nearest-neighbour diff: closest baffle hole for every capsule ---
    matches = []
    offsets = []
    for idx, cc in enumerate(capsule_centers):
        best_idx, best_d = None, None
        for h_idx, h in enumerate(holes_b):
            d = dist(cc, h["center"])
            if best_d is None or d < best_d:
                best_d, best_idx = d, h_idx
        offsets.append(best_d)
        matches.append({
            "capsule_index": idx,
            "capsule_center": [round(cc[0], SIZE_ROUND), round(cc[1], SIZE_ROUND)],
            "nearest_hole_center": [round(holes_b[best_idx]["center"][0], SIZE_ROUND),
                                     round(holes_b[best_idx]["center"][1], SIZE_ROUND)] if best_idx is not None else None,
            "nearest_hole_radius": round(holes_b[best_idx]["radius"], SIZE_ROUND) if best_idx is not None else None,
            "center_offset": round(best_d, SIZE_ROUND) if best_d is not None else None,
        })

    # --- 5. overlay render ---
    fig = plt.figure(figsize=(10, 10), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.set_facecolor("#1e2228")
    fig.patch.set_facecolor("#1e2228")
    draw_entities(ax, msp_g, shift=(-cg[0], -cg[1]), color="red")
    draw_entities(ax, msp_b, shift=(-cb[0], -cb[1]), color="deepskyblue")

    legend_handles = [
        Line2D([0], [0], color="red", lw=2, label=f"{os.path.basename(gad_path)} (capsules)"),
        Line2D([0], [0], color="deepskyblue", lw=2, label=f"{os.path.basename(baffle_path)} (baffle holes)"),
    ]

    # highlight the selected match with a single soft, translucent yellow
    # circle that encloses both the capsule and its matched hole together -
    # deliberately bigger than the real geometry so it visibly wraps around
    # the actual shapes instead of floating over a bare coordinate point
    if highlight_capsule_index is not None and 0 <= highlight_capsule_index < len(matches):
        m = matches[highlight_capsule_index]
        cap_c = capsule_centers[highlight_capsule_index]

        # a circle centered on the capsule's own center, sized just a bit
        # past its own (short-axis) radius - NOT its full length, which
        # would balloon the highlight far past the capsule's actual footprint
        _, _, _, cap_radius = capsule_dimensions(*capsules_g[highlight_capsule_index])
        cap_hl_r = cap_radius * 1.3
        hl_center, hl_radius = cap_c, cap_hl_r

        if m["nearest_hole_center"] is not None:
            hole_c = tuple(m["nearest_hole_center"])
            hole_r = (m["nearest_hole_radius"] or 3.0) * 1.3

            # smallest circle that encloses both the capsule circle and the
            # hole circle, so the whole match is pointed out as one shape
            d = dist(cap_c, hole_c)
            if d + hole_r <= cap_hl_r:
                hl_center, hl_radius = cap_c, cap_hl_r
            elif d + cap_hl_r <= hole_r:
                hl_center, hl_radius = hole_c, hole_r
            else:
                hl_radius = (d + cap_hl_r + hole_r) / 2
                t = (hl_radius - cap_hl_r) / d
                hl_center = (cap_c[0] + (hole_c[0] - cap_c[0]) * t,
                             cap_c[1] + (hole_c[1] - cap_c[1]) * t)

        ax.add_patch(Circle(hl_center, hl_radius, facecolor="yellow", edgecolor="none",
                             alpha=0.30, zorder=20))
        ax.add_patch(Circle(hl_center, hl_radius, fill=False, edgecolor="yellow",
                             linewidth=2.0, alpha=0.9, zorder=21))
        legend_handles.append(Line2D([0], [0], color="yellow", lw=2,
                                      label=f"selected: capsule #{highlight_capsule_index}"))

    lim = max(rg, rb) * 1.15
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.legend(handles=legend_handles, loc="upper right", facecolor="#1e2228", labelcolor="white")
    fig.savefig(out_png)
    plt.close(fig)
    print(f"Saved: {out_png}")

    diff = {
        "gad_path": gad_path,
        "baffle_path": baffle_path,
        "outer_radius_gad": round(rg, SIZE_ROUND),
        "outer_radius_baffle": round(rb, SIZE_ROUND),
        "outer_radius_diff": round(rg - rb, SIZE_ROUND),
        "capsule_count": len(capsule_centers),
        "hole_count": len(holes_b),
        "count_diff": len(capsule_centers) - len(holes_b),
        "mean_center_offset": round(sum(offsets) / len(offsets), SIZE_ROUND) if offsets else None,
        "max_center_offset": round(max(offsets), SIZE_ROUND) if offsets else None,
        "matches": matches,
    }
    with open(out_json, "w") as f:
        json.dump(diff, f, indent=2)
    print(f"Saved: {out_json}")

    return out_png, out_json


# ============================================================================
# STEP 8 : merge the Step 2 / 4 / 6 / 7 JSON outputs into one combined
#          summary JSON - every match gets the winning capsule's own size
#          (width/overall_length/radius/straight_length) attached to it
# ============================================================================

def export_merged_match_json(paths):
    """
    Read back the four JSON files already written by Steps 2, 4, 6 and 7
    (capsule_sizes_json, highest_count_json, baffle_json, trace_diff_json)
    and merge them into one combined JSON:

      - top-level `capsule_size`            : the winning capsule size's own
                                               width/overall_length/radius/
                                               straight_length/count (the
                                               size group Step 4 picked - the
                                               same group `capsule_count` in
                                               the diff refers to)
      - top-level `capsule_enclosing_circle`: the circle from Step 4/5 that
                                               all of those capsules sit in
      - top-level `baffle_size`             : outer radius/diameter, hole
                                               count and unique hole radii
                                               from Step 6
      - every entry in `matches`            : the same nearest-neighbour
                                               match from Step 7, PLUS the
                                               capsule's own
                                               capsule_width/
                                               capsule_overall_length/
                                               capsule_radius/
                                               capsule_straight_length
                                               (identical on every entry,
                                               since every capsule being
                                               matched here is the one
                                               winning size)

    Writes <PREFIX>_merged_capsule_baffle_match.json and returns its path.
    """
    with open(paths["capsule_sizes_json"]) as f:
        sizes = json.load(f)
    with open(paths["highest_count_json"]) as f:
        hc = json.load(f)
    with open(paths["baffle_json"]) as f:
        baffle = json.load(f)
    with open(paths["trace_diff_json"]) as f:
        diff = json.load(f)

    # the size group whose count matches the diff's capsule_count is the
    # "winning" (highest-count) capsule size that Step 4 selected
    winning_size = next((s for s in sizes["sizes"] if s["count"] == diff["capsule_count"]), None)
    if winning_size is None:
        # fall back to the size Step 4 itself recorded, in case counts
        # diverge (e.g. a capsule was dropped/added between steps)
        winning_size = {
            "width": hc["capsule_size"]["width"],
            "overall_length": hc["capsule_size"]["overall_length"],
            "radius": round(hc["capsule_size"]["width"] / 2, SIZE_ROUND),
            "straight_length": None,
            "count": hc["capsule_size"]["count"],
        }

    enclosing_circle = hc["unique_enclosing_circles"][0] if hc["unique_enclosing_circles"] else None
    baffle_info = baffle["baffles"][0] if baffle["baffles"] else None

    enriched_matches = [
        {
            **m,
            "capsule_width": winning_size["width"],
            "capsule_overall_length": winning_size["overall_length"],
            "capsule_radius": winning_size["radius"],
            "capsule_straight_length": winning_size["straight_length"],
        }
        for m in diff["matches"]
    ]

    merged = {
        "gad_path": diff["gad_path"],
        "baffle_path": diff["baffle_path"],

        "capsule_size": winning_size,
        "capsule_enclosing_circle": enclosing_circle,

        "baffle_size": {
            "outer_radius": baffle_info["outer_radius"] if baffle_info else None,
            "outer_diameter": baffle_info["outer_diameter"] if baffle_info else None,
            "hole_count": baffle_info["hole_count"] if baffle_info else None,
            "unique_hole_radii": baffle_info["unique_hole_radii"] if baffle_info else None,
        },

        "outer_radius_gad": diff["outer_radius_gad"],
        "outer_radius_baffle": diff["outer_radius_baffle"],
        "outer_radius_diff": diff["outer_radius_diff"],
        "capsule_count": diff["capsule_count"],
        "hole_count": diff["hole_count"],
        "count_diff": diff["count_diff"],
        "mean_center_offset": diff["mean_center_offset"],
        "max_center_offset": diff["max_center_offset"],

        "matches": enriched_matches,
    }

    out_path = paths["merged_match_json"]
    with open(out_path, "w") as f:
        json.dump(merged, f, indent=2)
    print(f"Saved: {out_path}")
    return out_path


# ============================================================================
# PIPELINE ENTRY POINT - runs every step, in order, on the two source DXFs
# ============================================================================

def run_pipeline(paths):
    """Run the full pipeline using the resolved `paths` dict from build_paths()."""
    print(f"Prefix:     {paths['prefix']}")
    print(f"Output dir: {paths['output_dir']}")

    # ---- STEP 1-3 : capsules in the GAD drawing ----
    print("\n=== STEP 1-3: detecting capsules in the GAD drawing ===")
    gad_doc = ezdxf.readfile(paths["gad_dxf"])
    gad_msp = gad_doc.modelspace()

    capsules, semi_count = detect_capsules(gad_msp)
    print(f"Found {len(capsules)} capsule/stadium shapes out of {semi_count} semicircular arcs.")

    export_capsule_sizes_json(capsules, paths["capsule_sizes_json"])
    render_capsules_highlighted(gad_doc, gad_msp, capsules, paths["capsules_highlighted_png"])

    # ---- STEP 4-5 : highest-count size -> its enclosing circle -> standalone DXF ----
    print("\n=== STEP 4-5: highest-count capsule size and its enclosing circle ===")
    circles = collect_circles(gad_msp)
    best_key, results = find_highest_count_group_with_circles(capsules, circles)
    print(f"Highest-count capsule size: width={best_key[0]}, overall_length={best_key[1]} "
          f"({len(results)} instances)")

    capsule_records = render_and_save_highest_count_group(
        gad_doc, gad_msp, best_key, results,
        paths["highest_count_png"], paths["highest_count_json"],
    )

    # pick the enclosing circle used by the most capsules in that group (normally all of them)
    circle_counts = {}
    for r in capsule_records:
        ci = r["enclosing_circle"]
        if ci is not None:
            key = (ci["center"][0], ci["center"][1], ci["radius"])
            circle_counts[key] = circle_counts.get(key, 0) + 1
    if not circle_counts:
        raise RuntimeError("No enclosing circle found for the highest-count capsule group.")
    best_circle_key = max(circle_counts, key=circle_counts.get)
    target_circle = {"center": (best_circle_key[0], best_circle_key[1]), "radius": best_circle_key[2]}
    save_dxf_within_circle(gad_doc, gad_msp, target_circle, paths["circle_extract_dxf"])

    # ---- STEP 6 : full baffle from the cutting-material layout ----
    print("\n=== STEP 6: extracting the full baffle from the cutting-material drawing ===")
    extract_full_baffle(paths["cutting_dxf"], paths["baffle_dxf"], paths["baffle_json"])

    # ---- STEP 7 : overlay + diff the capsule DXF against the full baffle DXF ----
    # No scaling of either drawing - they are compared exactly as extracted.
    print("\n=== STEP 7: tracing the capsule DXF against the full baffle DXF ===")
    trace_and_diff_dxfs(paths["circle_extract_dxf"], paths["baffle_dxf"],
                         paths["trace_diff_png"], paths["trace_diff_json"])

    # ---- STEP 8 : merge Steps 2/4/6/7's JSON outputs into one combined summary ----
    print("\n=== STEP 8: merging capsule + baffle JSON outputs ===")
    export_merged_match_json(paths)

    print(f"\nPipeline complete. All outputs written to: {paths['output_dir']}")


def main():
    parser = argparse.ArgumentParser(
        description="GAD / baffle capsule-matching pipeline. Takes the GAD DXF and the "
                    "cutting-material DXF as input; every output file name and the output "
                    "folder are generated automatically from the GAD file's name."
    )
    parser.add_argument("gad_dxf", help="Path to the GAD drawing, e.g. 2193000995_GAD.DXF")
    parser.add_argument("cutting_dxf", help='Path to the cutting-material drawing, e.g. '
                                             '"2193000995_CUTTING MATERIAL.DXF"')
    parser.add_argument("--output-dir", default=None,
                        help='Folder to write outputs into (default: "<prefix>_output")')
    parser.add_argument("--prefix", default=None,
                        help="Override the auto-derived output-filename prefix")
    args = parser.parse_args()

    paths = build_paths(args.gad_dxf, args.cutting_dxf, output_dir=args.output_dir, prefix=args.prefix)
    run_pipeline(paths)


if __name__ == "__main__":
    main()