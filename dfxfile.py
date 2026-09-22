"""
dxf_pipeline_all_in_one.py
===========================
Single-file combination of split_dxf.py + trace_dxf.py + overlay_dxf.py +
pipeline.py.

End-to-end: point at a DXF sheet -> split it into its individual
sub-drawings -> pick out FULL_BAFFLE, SEGMENTAL_BAFFLE_A and
SEGMENTAL_BAFFLE_B -> overlay each segmental baffle on top of the full
baffle (different colors, aligned at 0,0) -> save the overlay images.
Also traces (plots) every split sub-drawing individually to scale.

Usage:
    python dxf_pipeline_all_in_one.py "INPUT.dxf" [-o OUTDIR] [-t THRESHOLD]
"""

import argparse
import re
import sys
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.addons import importer

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Arc as MplArc
from matplotlib.lines import Line2D

# ---------------------------------------------------------------------------
# split_dxf.py
# ---------------------------------------------------------------------------

LABEL_TYPES = {"INSERT", "TEXT", "MTEXT"}


def sanitize(name: str, fallback: str) -> str:
    name = name.strip().rstrip(":").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or fallback


def entity_label_text(entity):
    t = entity.dxftype()
    if t == "MTEXT":
        text = entity.plain_text().strip()
        return text.splitlines()[0] if text else None
    if t == "TEXT":
        return entity.dxf.text.strip() or None
    if t == "INSERT":
        for sub in entity.virtual_entities():
            label = entity_label_text(sub)
            if label:
                return label
    return None


def expand(box, margin):
    return (
        box.extmin.x - margin,
        box.extmin.y - margin,
        box.extmax.x + margin,
        box.extmax.y + margin,
    )


def boxes_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def cluster_entities(entities, entity_boxes, threshold):
    n = len(entities)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    expanded = [expand(b, threshold / 2) for b in entity_boxes]
    for i in range(n):
        for j in range(i + 1, n):
            if boxes_overlap(expanded[i], expanded[j]):
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def cluster_bbox_centroid(idxs, entity_boxes):
    minx = min(entity_boxes[i].extmin.x for i in idxs)
    miny = min(entity_boxes[i].extmin.y for i in idxs)
    maxx = max(entity_boxes[i].extmax.x for i in idxs)
    maxy = max(entity_boxes[i].extmax.y for i in idxs)
    return (minx + maxx) / 2, (miny + maxy) / 2


def split_dxf(input_path: Path, outdir: Path, threshold: float):
    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()

    geometry = [e for e in msp if e.dxftype() not in LABEL_TYPES]
    labels = [e for e in msp if e.dxftype() in LABEL_TYPES]

    if not geometry:
        print("No geometry entities found in modelspace; nothing to check.")
        return []

    geometry_boxes = [bbox.extents([e], fast=True) for e in geometry]
    clusters = cluster_entities(geometry, geometry_boxes, threshold)

    centroids = [cluster_bbox_centroid(idxs, geometry_boxes) for idxs in clusters]
    label_boxes = {}
    for lab in labels:
        if lab.dxftype() == "INSERT":
            insert = lab.dxf.insert
            cx, cy = insert.x, insert.y
        elif hasattr(lab.dxf, "insert"):
            insert = lab.dxf.insert
            cx, cy = insert.x, insert.y
        else:
            b = bbox.extents([lab], fast=True)
            cx = (b.extmin.x + b.extmax.x) / 2
            cy = (b.extmin.y + b.extmax.y) / 2
        label_boxes[lab] = (cx, cy)

    cluster_labels = [[] for _ in clusters]
    for lab in labels:
        lx, ly = label_boxes[lab]
        best_i, best_d = 0, None
        for i, (cx, cy) in enumerate(centroids):
            d = (lx - cx) ** 2 + (ly - cy) ** 2
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        cluster_labels[best_i].append(lab)

    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for i, geo_idxs in enumerate(clusters, start=1):
        cluster_entities_list = [geometry[j] for j in geo_idxs] + cluster_labels[i - 1]

        name = None
        for lab in cluster_labels[i - 1]:
            name = entity_label_text(lab)
            if name:
                break
        base_name = sanitize(name, f"SubDrawing_{i}") if name else f"SubDrawing_{i}"

        new_doc = ezdxf.new(dxfversion=doc.dxfversion, units=doc.units)
        new_msp = new_doc.modelspace()
        imp = importer.Importer(doc, new_doc)
        imp.import_entities(cluster_entities_list, new_msp)
        imp.finalize()

        out_path = outdir / f"{base_name}.dxf"
        suffix = 2
        while out_path.exists():
            out_path = outdir / f"{base_name}_{suffix}.dxf"
            suffix += 1

        new_doc.saveas(str(out_path))
        written.append(out_path)
        print(f"Wrote {out_path}  ({len(geo_idxs)} geometry entities, "
              f"{len(cluster_labels[i - 1])} label entities)")

    return written


# ---------------------------------------------------------------------------
# trace_dxf.py
# ---------------------------------------------------------------------------

def load_geometry(doc):
    msp = doc.modelspace()
    geo = [e for e in msp if e.dxftype() in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE")]
    return geo


def geometry_bbox(geo):
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def upd(x, y):
        nonlocal minx, miny, maxx, maxy
        minx, miny = min(minx, x), min(miny, y)
        maxx, maxy = max(maxx, x), max(maxy, y)

    for e in geo:
        t = e.dxftype()
        if t == "LINE":
            upd(e.dxf.start.x, e.dxf.start.y)
            upd(e.dxf.end.x, e.dxf.end.y)
        elif t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            upd(c.x - r, c.y - r)
            upd(c.x + r, c.y + r)
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            upd(c.x - r, c.y - r)
            upd(c.x + r, c.y + r)
        elif t == "LWPOLYLINE":
            for x, y, *_ in e.get_points():
                upd(x, y)
    return minx, miny, maxx, maxy


def trace_file(path: Path, outdir: Path):
    doc = ezdxf.readfile(str(path))
    geo = load_geometry(doc)
    if not geo:
        print(f"  no drawable geometry found in {path.name}, skipping")
        return None

    minx, miny, maxx, maxy = geometry_bbox(geo)
    dx, dy = -minx, -miny
    width, height = maxx - minx, maxy - miny

    fig, ax = plt.subplots(figsize=(9, 9))

    for e in geo:
        t = e.dxftype()
        if t == "LINE":
            x1, y1 = e.dxf.start.x + dx, e.dxf.start.y + dy
            x2, y2 = e.dxf.end.x + dx, e.dxf.end.y + dy
            ax.plot([x1, x2], [y1, y2], color="black", linewidth=1.2)
        elif t == "CIRCLE":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            ax.add_patch(Circle((cx, cy), e.dxf.radius, fill=False,
                                 edgecolor="black", linewidth=1.0))
        elif t == "ARC":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            r = e.dxf.radius
            ax.add_patch(MplArc((cx, cy), 2 * r, 2 * r,
                                 theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                 edgecolor="black", linewidth=1.2))
        elif t == "LWPOLYLINE":
            pts = [(x + dx, y + dy) for x, y, *_ in e.get_points()]
            if e.closed and pts:
                pts.append(pts[0])
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color="black", linewidth=1.2)

    ax.plot(0, 0, marker="+", color="red", markersize=14, markeredgewidth=2, zorder=5)
    ax.annotate("(0, 0)", (0, 0), textcoords="offset points", xytext=(8, -12),
                color="red", fontsize=9)

    margin = max(width, height) * 0.08 + 1
    ax.set_xlim(-margin, width + margin)
    ax.set_ylim(-margin, height + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.axhline(0, color="gray", linewidth=0.6)
    ax.axvline(0, color="gray", linewidth=0.6)
    ax.set_xlabel("X (drawing units)")
    ax.set_ylabel("Y (drawing units)")
    ax.set_title(f"{path.stem}\noverall size: {width:.2f} x {height:.2f} units "
                 f"(origin at bottom-left corner)")

    out_path = outdir / f"{path.stem}_trace.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    print(f"  {path.name}: {len(geo)} entities, bbox {width:.2f} x {height:.2f} -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# overlay_dxf.py
# ---------------------------------------------------------------------------

def draw_geometry(ax, geo, dx, dy, color, linewidth=1.2, alpha=1.0):
    for e in geo:
        t = e.dxftype()
        if t == "LINE":
            x1, y1 = e.dxf.start.x + dx, e.dxf.start.y + dy
            x2, y2 = e.dxf.end.x + dx, e.dxf.end.y + dy
            ax.plot([x1, x2], [y1, y2], color=color, linewidth=linewidth, alpha=alpha)
        elif t == "CIRCLE":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            ax.add_patch(Circle((cx, cy), e.dxf.radius, fill=False,
                                 edgecolor=color, linewidth=linewidth, alpha=alpha))
        elif t == "ARC":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            r = e.dxf.radius
            ax.add_patch(MplArc((cx, cy), 2 * r, 2 * r,
                                 theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                 edgecolor=color, linewidth=linewidth, alpha=alpha))
        elif t == "LWPOLYLINE":
            pts = [(x + dx, y + dy) for x, y, *_ in e.get_points()]
            if e.closed and pts:
                pts.append(pts[0])
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha)


def overlay(path_a: Path, path_b: Path, out_path: Path,
            label_a: str = None, label_b: str = None,
            color_a: str = "red", color_b: str = "blue"):
    doc_a = ezdxf.readfile(str(path_a))
    doc_b = ezdxf.readfile(str(path_b))
    geo_a = load_geometry(doc_a)
    geo_b = load_geometry(doc_b)

    minx_a, miny_a, maxx_a, maxy_a = geometry_bbox(geo_a)
    minx_b, miny_b, maxx_b, maxy_b = geometry_bbox(geo_b)
    dx_a, dy_a = -minx_a, -miny_a
    dx_b, dy_b = -minx_b, -miny_b

    label_a = label_a or path_a.stem
    label_b = label_b or path_b.stem

    fig, ax = plt.subplots(figsize=(9, 9))

    draw_geometry(ax, geo_b, dx_b, dy_b, color_b, linewidth=1.4, alpha=0.85)
    draw_geometry(ax, geo_a, dx_a, dy_a, color_a, linewidth=1.4, alpha=0.85)

    ax.plot(0, 0, marker="+", color="black", markersize=14, markeredgewidth=2, zorder=5)
    ax.annotate("(0, 0)", (0, 0), textcoords="offset points", xytext=(8, -12),
                color="black", fontsize=9)

    width = max(maxx_a - minx_a, maxx_b - minx_b)
    height = max(maxy_a - miny_a, maxy_b - miny_b)
    margin = max(width, height) * 0.08 + 1
    ax.set_xlim(-margin, width + margin)
    ax.set_ylim(-margin, height + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.axhline(0, color="gray", linewidth=0.6)
    ax.axvline(0, color="gray", linewidth=0.6)
    ax.set_xlabel("X (drawing units)")
    ax.set_ylabel("Y (drawing units)")
    ax.set_title(f"Overlay: {label_a} vs {label_b}\n(both aligned at origin 0,0)")

    legend_lines = [
        Line2D([0], [0], color=color_a, lw=2, label=label_a),
        Line2D([0], [0], color=color_b, lw=2, label=label_b),
    ]
    ax.legend(handles=legend_lines, loc="upper right")

    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    print(f"  {label_a} ({color_a}) + {label_b} ({color_b}) -> {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# pipeline.py
# ---------------------------------------------------------------------------

TARGETS = {
    "full": "FULL_BAFFLE",
    "seg_a": "SEGMENTAL_BAFFLE_A",
    "seg_b": "SEGMENTAL_BAFFLE_B",
}


def find_part(split_files, keyword: str):
    keyword = keyword.upper()
    for f in split_files:
        if keyword in f.stem.upper():
            return f
    return None


def run_pipeline(input_dxf: Path, outdir: Path, threshold: float):
    print(f"[1/4] Splitting '{input_dxf.name}' into sub-drawings...")
    split_files = split_dxf(input_dxf, outdir, threshold)
    if not split_files:
        print("No sub-drawings were produced; aborting.", file=sys.stderr)
        return []

    print(f"\n[2/4] Tracing each of the {len(split_files)} sub-drawing(s)...")
    for f in split_files:
        trace_file(f, outdir)

    print("\n[3/4] Locating FULL_BAFFLE, SEGMENTAL_BAFFLE_A, SEGMENTAL_BAFFLE_B...")
    full = find_part(split_files, TARGETS["full"])
    seg_a = find_part(split_files, TARGETS["seg_a"])
    seg_b = find_part(split_files, TARGETS["seg_b"])

    for name, path in [("FULL_BAFFLE", full), ("SEGMENTAL_BAFFLE_A", seg_a),
                        ("SEGMENTAL_BAFFLE_B", seg_b)]:
        print(f"   {name}: {'FOUND -> ' + str(path) if path else 'NOT FOUND'}")

    print("\n[4/4] Overlaying segments on the full baffle...")
    outputs = []
    if seg_a and full:
        outputs.append(overlay(seg_a, full, outdir / "overlay_SEGMENTAL_BAFFLE_A_vs_FULL_BAFFLE.png",
                                color_a="red", color_b="blue"))
    else:
        print("   skipped SEGMENTAL_BAFFLE_A overlay (missing part)")

    if seg_b and full:
        outputs.append(overlay(seg_b, full, outdir / "overlay_SEGMENTAL_BAFFLE_B_vs_FULL_BAFFLE.png",
                                color_a="green", color_b="blue"))
    else:
        print("   skipped SEGMENTAL_BAFFLE_B overlay (missing part)")

    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="input DXF sheet to split")
    parser.add_argument("-o", "--outdir", type=Path, default=None,
                         help="output directory (default: <input_stem>_split)")
    parser.add_argument("-t", "--threshold", type=float, default=20.0,
                         help="clustering gap threshold in drawing units (default: 20)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    outdir = args.outdir or args.input.with_name(args.input.stem + "_split")
    outputs = run_pipeline(args.input, outdir, args.threshold)

    print(f"\nDone. {len(outputs)} overlay image(s) written to {outdir}")
    for o in outputs:
        print(f"   {o}")


if __name__ == "__main__":
    main()
    