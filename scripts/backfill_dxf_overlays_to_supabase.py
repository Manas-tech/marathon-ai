"""
One-off: uploads every DXF overlay/highlight PNG that's currently only on
local disk (from `extraction_results` OVERLAY.*.PATH rows and
`gad_dxf_validation_runs.overlay_path`) into Supabase Storage, and rewrites
those DB rows to point at the new Storage URL -- so the images survive a
host with no persistent disk (Render free tier).

Run once, after SUPABASE_URL / SUPABASE_SERVICE_KEY are set in .env:
    python scripts/backfill_dxf_overlays_to_supabase.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services import storage_service

settings = get_settings()


def main() -> None:
    if not storage_service.enabled():
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY not set -- nothing to do.")
        return

    db = SessionLocal()
    migrated = skipped = failed = 0

    # 1. extraction_results: OVERLAY.<n>.PATH rows (DXF split-baffle overlays)
    rows = db.execute(
        text("SELECT id, value FROM extraction_results WHERE parameter LIKE 'OVERLAY.%.PATH'")
    ).fetchall()
    for row_id, value in rows:
        if value.startswith(("http://", "https://")):
            skipped += 1
            continue
        local_path = settings.upload_dir_path / value
        if not local_path.exists():
            print(f"  [extraction_results id={row_id}] missing on disk, skipping: {local_path}")
            failed += 1
            continue
        url = storage_service.upload_png(local_path, value)
        if not url:
            failed += 1
            continue
        db.execute(text("UPDATE extraction_results SET value = :url WHERE id = :id"), {"url": url, "id": row_id})
        migrated += 1
        print(f"  [extraction_results id={row_id}] -> {url}")

    # 2. gad_dxf_validation_runs.overlay_path (GAD-vs-cutting-sheet overlay)
    runs = db.execute(text("SELECT id, overlay_path FROM gad_dxf_validation_runs")).fetchall()
    for run_id, overlay_path in runs:
        if overlay_path.startswith(("http://", "https://")):
            skipped += 1
            continue
        local_path = settings.upload_dir_path / overlay_path
        if not local_path.exists():
            print(f"  [gad_dxf_validation_runs id={run_id}] missing on disk, skipping: {local_path}")
            failed += 1
            continue
        url = storage_service.upload_png(local_path, overlay_path)
        if not url:
            failed += 1
            continue
        db.execute(text("UPDATE gad_dxf_validation_runs SET overlay_path = :url WHERE id = :id"), {"url": url, "id": run_id})
        migrated += 1
        print(f"  [gad_dxf_validation_runs id={run_id}] -> {url}")

    db.commit()
    db.close()
    print(f"\nDone. migrated={migrated} already_remote={skipped} failed={failed}")


if __name__ == "__main__":
    main()
