"""
DXF processing: split a DXF sheet into its individual sub-drawings, then
overlay the segmental baffles on the full baffle to visually check they
match (bolt pattern, outline, aligned at origin).

Ported from the standalone dfxfile.py (split_dxf.py + trace_dxf.py +
overlay_dxf.py + pipeline.py combined) with no behavioural changes to the
geometry/clustering/overlay logic -- only the output location and return
value changed, so pipeline_service.py can persist the results.

Unlike the PDF pipelines, this produces no Gemini-extracted parameters --
the deliverable is the pair of overlay PNGs (SEGMENTAL_BAFFLE_A vs
FULL_BAFFLE, SEGMENTAL_BAFFLE_B vs FULL_BAFFLE) that persistence_service
stores as (label, path) rows for the frontend to render as images.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import bbox
from ezdxf.addons import importer

import matplotlib
matplotlib.use("Agg")  # headless -- this runs inside a FastAPI background task, no display
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Arc as MplArc
from matplotlib.lines import Line2D

LABEL_TYPES = {"INSERT", "TEXT", "MTEXT"}
CLUSTER_THRESHOLD = 20.0

# A CIRCLE/ARC's axis-aligned bbox corner sticks out ~0.41x its radius past
# the actual curve. For small holes that's negligible, but a big "outer
# boundary" circle/arc (radius tens of units) can bbox-overlap an unrelated
# nearby part's boundary shape at a corner even though the curves themselves
# never come close. Only entities with a bbox diagonal above this are
# treated as "large" and clustered by true curve-to-curve distance instead
# of the (lenient, bbox-based) default -- small dense hole clusters keep
# using the lenient bbox test, which is what correctly merges them.
LARGE_ROUND_BBOX_DIAG = 50.0

TARGETS = {
    "full": "FULL_BAFFLE",
    "seg_a": "SEGMENTAL_BAFFLE_A",
    "seg_b": "SEGMENTAL_BAFFLE_B",
}


@dataclass
class DxfPipelineResult:
    split_files: list[Path]
    overlays: list[tuple[str, Path]]  # (label, png_path)


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------

def _sanitize(name: str, fallback: str) -> str:
    name = name.strip().rstrip(":").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or fallback


def _entity_label_text(entity):
    t = entity.dxftype()
    if t == "MTEXT":
        text = entity.plain_text().strip()
        return text.splitlines()[0] if text else None
    if t == "TEXT":
        return entity.dxf.text.strip() or None
    if t == "INSERT":
        for sub in entity.virtual_entities():
            label = _entity_label_text(sub)
            if label:
                return label
    return None


def _entity_sample_points(entity, n_arc_points: int = 24) -> "np.ndarray":
    """Sample points along an entity's actual curve, not its bounding box.

    A CIRCLE/ARC's axis-aligned bbox is a full square around it, so two
    separate circular parts sitting near each other can have overlapping
    boxes at a corner even though the curves themselves never come close --
    that false overlap used to merge unrelated sub-drawings into one
    cluster. Sampling the true curve and clustering on point-to-point
    distance instead avoids that."""
    t = entity.dxftype()
    if t == "LINE":
        return np.array([[entity.dxf.start.x, entity.dxf.start.y], [entity.dxf.end.x, entity.dxf.end.y]])
    if t == "CIRCLE":
        c, r = entity.dxf.center, entity.dxf.radius
        angles = np.linspace(0, 2 * np.pi, n_arc_points, endpoint=False)
        return np.column_stack([c.x + r * np.cos(angles), c.y + r * np.sin(angles)])
    if t == "ARC":
        c, r = entity.dxf.center, entity.dxf.radius
        a1, a2 = np.radians(entity.dxf.start_angle), np.radians(entity.dxf.end_angle)
        if a2 < a1:
            a2 += 2 * np.pi
        angles = np.linspace(a1, a2, n_arc_points)
        return np.column_stack([c.x + r * np.cos(angles), c.y + r * np.sin(angles)])
    if t == "LWPOLYLINE":
        pts = [(x, y) for x, y, *_ in entity.get_points()]
        if pts:
            return np.array(pts)
    b = bbox.extents([entity], fast=True)
    return np.array([[b.extmin.x, b.extmin.y], [b.extmin.x, b.extmax.y], [b.extmax.x, b.extmin.y], [b.extmax.x, b.extmax.y]])


def _min_point_distance(pa: "np.ndarray", pb: "np.ndarray") -> float:
    if len(pa) == 0 or len(pb) == 0:
        return float("inf")
    diff = pa[:, None, :] - pb[None, :, :]
    return float(np.sqrt((diff ** 2).sum(axis=2)).min())


def _expand(box, margin):
    return (
        box.extmin.x - margin,
        box.extmin.y - margin,
        box.extmax.x + margin,
        box.extmax.y + margin,
    )


def _boxes_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _is_large_round(entity, box) -> bool:
    if entity.dxftype() not in ("CIRCLE", "ARC"):
        return False
    diag = ((box.extmax.x - box.extmin.x) ** 2 + (box.extmax.y - box.extmin.y) ** 2) ** 0.5
    return diag > LARGE_ROUND_BBOX_DIAG


def _cluster_entities(entities, entity_boxes, entity_points, threshold):
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

    is_large_round = [_is_large_round(e, b) for e, b in zip(entities, entity_boxes)]
    expanded = [_expand(b, threshold / 2) for b in entity_boxes]

    for i in range(n):
        for j in range(i + 1, n):
            if is_large_round[i] or is_large_round[j]:
                close = _min_point_distance(entity_points[i], entity_points[j]) <= threshold
            else:
                close = _boxes_overlap(expanded[i], expanded[j])
            if close:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _cluster_bbox_centroid(idxs, entity_boxes):
    minx = min(entity_boxes[i].extmin.x for i in idxs)
    miny = min(entity_boxes[i].extmin.y for i in idxs)
    maxx = max(entity_boxes[i].extmax.x for i in idxs)
    maxy = max(entity_boxes[i].extmax.y for i in idxs)
    return (minx + maxx) / 2, (miny + maxy) / 2


def _split_dxf(input_path: Path, outdir: Path, threshold: float) -> list[Path]:
    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()

    geometry = [e for e in msp if e.dxftype() not in LABEL_TYPES]
    labels = [e for e in msp if e.dxftype() in LABEL_TYPES]
    if not geometry:
        return []

    geometry_boxes = [bbox.extents([e], fast=True) for e in geometry]
    geometry_points = [_entity_sample_points(e) for e in geometry]
    clusters = _cluster_entities(geometry, geometry_boxes, geometry_points, threshold)

    centroids = [_cluster_bbox_centroid(idxs, geometry_boxes) for idxs in clusters]
    label_boxes = {}
    for lab in labels:
        if lab.dxftype() == "INSERT" or hasattr(lab.dxf, "insert"):
            insert = lab.dxf.insert
            cx, cy = insert.x, insert.y
        else:
            b = bbox.extents([lab], fast=True)
            cx = (b.extmin.x + b.extmax.x) / 2
            cy = (b.extmin.y + b.extmax.y) / 2
        label_boxes[lab] = (cx, cy)

    cluster_labels: list[list] = [[] for _ in clusters]
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
            name = _entity_label_text(lab)
            if name:
                break
        base_name = _sanitize(name, f"SubDrawing_{i}") if name else f"SubDrawing_{i}"

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

    return written


# ---------------------------------------------------------------------------
# geometry helpers shared by trace + overlay
# ---------------------------------------------------------------------------

def _load_geometry(doc):
    msp = doc.modelspace()
    return [e for e in msp if e.dxftype() in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE")]


def _geometry_bbox(geo):
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
        elif t in ("CIRCLE", "ARC"):
            c, r = e.dxf.center, e.dxf.radius
            upd(c.x - r, c.y - r)
            upd(c.x + r, c.y + r)
        elif t == "LWPOLYLINE":
            for x, y, *_ in e.get_points():
                upd(x, y)
    return minx, miny, maxx, maxy


def _draw_geometry(ax, geo, dx, dy, color, linewidth=1.2, alpha=1.0):
    for e in geo:
        t = e.dxftype()
        if t == "LINE":
            x1, y1 = e.dxf.start.x + dx, e.dxf.start.y + dy
            x2, y2 = e.dxf.end.x + dx, e.dxf.end.y + dy
            ax.plot([x1, x2], [y1, y2], color=color, linewidth=linewidth, alpha=alpha)
        elif t == "CIRCLE":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            ax.add_patch(Circle((cx, cy), e.dxf.radius, fill=False, edgecolor=color, linewidth=linewidth, alpha=alpha))
        elif t == "ARC":
            cx, cy = e.dxf.center.x + dx, e.dxf.center.y + dy
            r = e.dxf.radius
            ax.add_patch(MplArc((cx, cy), 2 * r, 2 * r, theta1=e.dxf.start_angle, theta2=e.dxf.end_angle,
                                 edgecolor=color, linewidth=linewidth, alpha=alpha))
        elif t == "LWPOLYLINE":
            pts = [(x + dx, y + dy) for x, y, *_ in e.get_points()]
            if e.closed and pts:
                pts.append(pts[0])
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=color, linewidth=linewidth, alpha=alpha)


def _trace_file(path: Path, outdir: Path) -> Path | None:
    doc = ezdxf.readfile(str(path))
    geo = _load_geometry(doc)
    if not geo:
        return None

    minx, miny, maxx, maxy = _geometry_bbox(geo)
    dx, dy = -minx, -miny
    width, height = maxx - minx, maxy - miny

    fig, ax = plt.subplots(figsize=(9, 9))
    _draw_geometry(ax, geo, dx, dy, "black")

    ax.plot(0, 0, marker="+", color="red", markersize=14, markeredgewidth=2, zorder=5)
    ax.annotate("(0, 0)", (0, 0), textcoords="offset points", xytext=(8, -12), color="red", fontsize=9)

    margin = max(width, height) * 0.08 + 1
    ax.set_xlim(-margin, width + margin)
    ax.set_ylim(-margin, height + margin)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    ax.axhline(0, color="gray", linewidth=0.6)
    ax.axvline(0, color="gray", linewidth=0.6)
    ax.set_xlabel("X (drawing units)")
    ax.set_ylabel("Y (drawing units)")
    ax.set_title(f"{path.stem}\noverall size: {width:.2f} x {height:.2f} units (origin at bottom-left corner)")

    out_path = outdir / f"{path.stem}_trace.png"
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


def _overlay(path_a: Path, path_b: Path, out_path: Path, label_a: str, label_b: str,
             color_a: str = "red", color_b: str = "blue") -> Path:
    doc_a = ezdxf.readfile(str(path_a))
    doc_b = ezdxf.readfile(str(path_b))
    geo_a = _load_geometry(doc_a)
    geo_b = _load_geometry(doc_b)

    minx_a, miny_a, maxx_a, maxy_a = _geometry_bbox(geo_a)
    minx_b, miny_b, maxx_b, maxy_b = _geometry_bbox(geo_b)
    dx_a, dy_a = -minx_a, -miny_a
    dx_b, dy_b = -minx_b, -miny_b

    fig, ax = plt.subplots(figsize=(9, 9))
    _draw_geometry(ax, geo_b, dx_b, dy_b, color_b, linewidth=1.4, alpha=0.85)
    _draw_geometry(ax, geo_a, dx_a, dy_a, color_a, linewidth=1.4, alpha=0.85)

    ax.plot(0, 0, marker="+", color="black", markersize=14, markeredgewidth=2, zorder=5)
    ax.annotate("(0, 0)", (0, 0), textcoords="offset points", xytext=(8, -12), color="black", fontsize=9)

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
    return out_path


def _normalize_label(text: str) -> str:
    """Strip everything but letters/digits so label matching survives
    punctuation variants in the DXF text (e.g. "SEGMENTAL BAFFLE - A" ->
    sanitized filename "SEGMENTAL_BAFFLE_-_A..." still matches "SEGMENTAL_BAFFLE_A")."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _find_part(split_files: list[Path], keyword: str) -> Path | None:
    keyword = _normalize_label(keyword)
    for f in split_files:
        if keyword in _normalize_label(f.stem):
            return f
    return None


def run_dxf_split(input_dxf: str, outdir: str, threshold: float = CLUSTER_THRESHOLD) -> list[Path]:
    """Extraction-stage only: split the sheet into its sub-part .dxf files.
    No trace, no overlay -- those happen later in run_dxf_validate() once the
    user explicitly asks for validation, keeping the Upload tab's Extraction
    step to "parse what was uploaded" and nothing more."""
    return _split_dxf(Path(input_dxf), Path(outdir), threshold)


def run_dxf_validate(split_files: list[Path], outdir: str) -> DxfPipelineResult:
    """Validate-stage: trace each split sub-part, then overlay the segmental
    baffles on the full baffle. Takes the split files produced by an earlier
    run_dxf_split() call -- does not re-split."""
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)

    if not split_files:
        return DxfPipelineResult(split_files=[], overlays=[])

    for f in split_files:
        _trace_file(f, outdir_path)

    full = _find_part(split_files, TARGETS["full"])
    seg_a = _find_part(split_files, TARGETS["seg_a"])
    seg_b = _find_part(split_files, TARGETS["seg_b"])

    overlays: list[tuple[str, Path]] = []
    if seg_a and full:
        label = "SEGMENTAL_BAFFLE_A_vs_FULL_BAFFLE"
        path = _overlay(seg_a, full, outdir_path / f"overlay_{label}.png",
                         label_a="SEGMENTAL_BAFFLE_A", label_b="FULL_BAFFLE", color_a="red", color_b="blue")
        overlays.append((label, path))
    if seg_b and full:
        label = "SEGMENTAL_BAFFLE_B_vs_FULL_BAFFLE"
        path = _overlay(seg_b, full, outdir_path / f"overlay_{label}.png",
                         label_a="SEGMENTAL_BAFFLE_B", label_b="FULL_BAFFLE", color_a="green", color_b="blue")
        overlays.append((label, path))

    return DxfPipelineResult(split_files=split_files, overlays=overlays)
