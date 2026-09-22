"""Render a ComparisonReport as readable Markdown.

Ported from the original report.py with no behavioural changes.
"""
from __future__ import annotations
from app.schemas.drawing import ComparisonReport

STATUS_ICON = {"MATCH": "✅", "MISMATCH": "❌", "GAD_ONLY": "ℹ️", "SUB_ONLY": "ℹ️"}
OVERALL_ICON = {"CONSISTENT": "✅ CONSISTENT", "DISCREPANCIES_FOUND": "❌ DISCREPANCIES FOUND",
                "COULD_NOT_MATCH": "⚠️ COULD NOT MATCH TO GAD"}


def render_markdown(report: ComparisonReport) -> str:
    lines = []
    lines.append(f"# Drawing Comparison Report")
    lines.append("")
    lines.append(f"**GAD:** `{report.gad_file}`  (Drawing No. {report.gad_drawing_no or 'n/a'})")
    lines.append("")

    total_mismatches = sum(
        1 for part in report.parts for f in part.findings if f.status == "MISMATCH"
    )
    lines.append(f"**Summary:** {len(report.parts)} sub-drawing(s) checked, "
                 f"{total_mismatches} mismatch(es) found.")
    lines.append("")
    lines.append("---")

    for part in report.parts:
        lines.append("")
        lines.append(f"## {part.part_name}  —  {OVERALL_ICON.get(part.overall_status, part.overall_status)}")
        lines.append(f"*Sub-drawing file: `{part.sub_drawing_file}`*")
        if part.matched_bom_row:
            b = part.matched_bom_row
            lines.append(f"*Matched GAD BOM row: SR {b.sr_no} — {b.description} "
                         f"| Qty {b.qty} | {b.material} | {b.size}*")
        lines.append("")

        if not part.findings:
            lines.append("_No comparable parameters found._")
            continue

        lines.append("| Status | Parameter | GAD Value | Sub-Drawing Value |")
        lines.append("|---|---|---|---|")
        # Show mismatches first, then matches, then info
        order = {"MISMATCH": 0, "GAD_ONLY": 1, "SUB_ONLY": 1, "MATCH": 2}
        for f in sorted(part.findings, key=lambda f: order.get(f.status, 3)):
            icon = STATUS_ICON.get(f.status, "")
            lines.append(
                f"| {icon} {f.status} | {f.parameter} | {f.gad_value or '—'} | "
                f"{f.sub_value or '—'} |"
            )

    lines.append("")
    lines.append("---")
    lines.append("_Generated automatically from vision-based extraction of the source PDFs. "
                 "Verify all ❌ MISMATCH findings against controlled documents before acting on them._")
    return "\n".join(lines)
