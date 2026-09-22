# MySQL Database — Schema & Query Reference

Covers the 4 tables backing this app: `projects` / `project_drawings` /
`extraction_results` / `comparison_results` (added per `Marathon Drawing
Comparator(DB_PLAN).csv`). There is no job-orchestration table — comparison
requests run synchronously and return the finished report directly, and
extraction status lives on `project_drawings.is_extracted` per drawing.

Tables are created/updated via Alembic migrations (`alembic/versions/`), not
by hand — the `CREATE TABLE` statements below are the actual DDL MySQL
produced from those migrations (pulled with `SHOW CREATE TABLE`), given here
so you can see/run them directly in a SQL client.

## 1. How to connect and run these queries

**Option A — MySQL Workbench / any GUI client**
Connect with the host/port/user/password/database from your `.env`'s
`DATABASE_URL` (`mysql+pymysql://user:password@host:port/dbname`), then
paste any query below into a SQL tab and run it.

**Option B — `mysql` command-line client** (if installed)
```bash
mysql -h localhost -P 3306 -u root -p drawing_validator
# it will prompt for the password, then you're in a mysql> prompt
```
Then paste any query below and end it with `;`.

**Option C — from this project's Python env** (no separate MySQL client needed)
```bash
python -c "
from app.db.session import SessionLocal
from sqlalchemy import text
db = SessionLocal()
for row in db.execute(text('SELECT * FROM projects')):
    print(row)
db.close()
"
```

## 2. Schema (actual DDL)

### `projects`
```sql
CREATE TABLE `projects` (
  `id` int NOT NULL AUTO_INCREMENT,
  `so_no` varchar(100) DEFAULT NULL,
  `name` varchar(255) NOT NULL,
  `description` text,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```
One row per project. Currently every job attaches to a single auto-created
`name = 'Default Project'` row (no project-management UI yet).

### `project_drawings`
```sql
CREATE TABLE `project_drawings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `project_id` int NOT NULL,
  `title` varchar(512) NOT NULL,
  `path` varchar(1024) NOT NULL,
  `type` smallint NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_project_drawings_project_id` (`project_id`),
  KEY `ix_project_drawings_project_type` (`project_id`,`type`),
  CONSTRAINT `project_drawings_ibfk_1` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```
One row per uploaded PDF. `type`: `0` = GAD, `1` = sub-drawing.
`path` is the file's location on disk (not a MySQL BLOB — the PDF itself
still lives on the filesystem under `uploads/`).

### `extraction_results`
```sql
CREATE TABLE `extraction_results` (
  `id` int NOT NULL AUTO_INCREMENT,
  `project_id` int NOT NULL,
  `drawing_id` int NOT NULL,
  `parameter` varchar(255) NOT NULL,
  `value` text NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_extraction_results_project_id` (`project_id`),
  KEY `ix_extraction_results_drawing_id` (`drawing_id`),
  KEY `ix_extraction_results_drawing_parameter` (`drawing_id`,`parameter`),
  CONSTRAINT `extraction_results_ibfk_1` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`),
  CONSTRAINT `extraction_results_ibfk_2` FOREIGN KEY (`drawing_id`) REFERENCES `project_drawings` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```
The Gemini extraction JSON for one drawing, flattened into one row per leaf
field instead of stored as a raw JSON blob (so every field stays indexable/
queryable). `parameter` naming scheme, applied by
`app/services/persistence_service.py::flatten_extraction`:

| Source field                  | `parameter` pattern                                              |
|--------------------------------|-------------------------------------------------------------------|
| `drawing_category`             | `DRAWING_CATEGORY`                                                |
| `title_block.*`                | `TITLE_BLOCK.DRAWING_NO`, `.TITLE`, `.CUSTOMER`, `.REVISION`      |
| `part_specifications[i]`       | `SPEC.<parameter as extracted>` e.g. `SPEC.MATERIAL`              |
| `bill_of_materials[n]`         | `BOM.<n>.SR_NO`, `.DESCRIPTION`, `.QTY`, `.MATERIAL`, `.SIZE`, `.REMARKS`, `.BOM_TABLE` |
| `dimensional_callouts[n]`      | `CALLOUT.<n>.LABEL`, `CALLOUT.<n>.CATEGORY`                       |
| `notes[n]`                     | `NOTE.<n>`                                                        |

`<n>` is the row's 1-based position in that list, so e.g. two BOM tables
that reuse SR.NO `1` still get distinct keys (`BOM.1...` vs `BOM.27...`).

### `comparison_results`
```sql
CREATE TABLE `comparison_results` (
  `id` int NOT NULL AUTO_INCREMENT,
  `project_id` int NOT NULL,
  `drawing_id` int NOT NULL,
  `parameter` varchar(255) NOT NULL,
  `gad_value` text NOT NULL,
  `subd_value` text NOT NULL,
  `status` varchar(16) NOT NULL,
  `mismatch_acceptance` tinyint(1) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_comparison_results_project_id` (`project_id`),
  KEY `ix_comparison_results_drawing_id` (`drawing_id`),
  KEY `ix_comparison_results_status` (`status`),
  KEY `ix_comparison_results_drawing_parameter` (`drawing_id`,`parameter`),
  CONSTRAINT `comparison_results_ibfk_1` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`),
  CONSTRAINT `comparison_results_ibfk_2` FOREIGN KEY (`drawing_id`) REFERENCES `project_drawings` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```
One row per `Finding` from the GAD-vs-sub comparison step. `drawing_id`
points at the **matched sub-drawing's** `project_drawings` row (the GAD
side of the comparison is just `gad_value`, no separate FK to the GAD
drawing). `status` is one of `MATCH` / `MISMATCH` / `GAD_ONLY` / `SUB_ONLY`.
`mismatch_acceptance` defaults to `0` (false) — flip it to `1` once someone
has manually reviewed and accepted a flagged mismatch.

### `jobcard_comparison_results`
(migration `9d4e2b7c1a58`; model `app/models/jobcard_comparison_result.py`)

One row per `Finding` from comparing a **heating-element sub-drawing**
against the **job card** (Run Comparison tab → job card card → "Compare with
sub drawing"). Columns: `project_id`, `sub_drawing_id` and
`job_card_drawing_id` (all FKs; the last two point at `project_drawings`),
`parameter`, `jobcard_value`, `subd_value`, `status` (`MATCH` / `MISMATCH` /
`JOBCARD_ONLY` / `SUB_ONLY`), `mismatch_acceptance`, `created_at`. Kept apart
from `comparison_results` because that table is GAD-shaped. Indexed on
`sub_drawing_id`, `job_card_drawing_id`, `project_id`, `status` and
(`sub_drawing_id`, `parameter`). Re-comparing the same (sub, job card) pair
deletes that pair's earlier rows before inserting the new ones, so there is
always one current result per pair (no history). Browse reads them via
`jobcard_comparisons` on `GET /projects/{id}/drawings/{drawing_id}`.

### `gad_dxf_validation_runs` / `gad_dxf_validation_matches`
(migration `a3c5e8f10b72`; models `app/models/gad_dxf_validation.py`)

Results of the Run Comparison tab's **GAD vs DXF Validation** card: a GAD DXF
(filename contains "GAD") checked against a cutting-sheet DXF (any other DXF)
by `app/services/gad_dxf_service.py` -- pure geometry, no LLM.

- `gad_dxf_validation_runs`: one row per run. `project_id`, `gad_drawing_id`
  and `cutting_drawing_id` (FKs; the latter two point at DXF rows of
  `project_drawings`), `output_dir` / `overlay_path` (relative to the uploads
  folder, served under `/uploads`), the outer radii, `capsule_count`,
  `hole_count`, `count_diff`, `mean_center_offset`, `max_center_offset`,
  `capsule_width`, `capsule_overall_length`, `created_at`.
- `gad_dxf_validation_matches`: one row per capsule. `run_id` (FK),
  `capsule_index`, capsule/hole centre x,y, `capsule_radius`, `hole_radius`,
  `center_offset`, `status` (`MATCH` when the radii are within 0.05, else
  `MISMATCH`) and `mismatch_acceptance`.

Re-validating the same (GAD, cutting) pair deletes the earlier run, its match
rows and its output folder, so there is one current run per pair. Browse reads
them via `gad_dxf_validations` on `GET /projects/{id}/drawings/{drawing_id}`.

## 3. Entity-relationship overview

```
projects (1) ──< project_drawings (many)
                       │
                       ├──< extraction_results (many)   [one row per extracted field]
                       └──< comparison_results (many)   [one row per finding, drawing_id = the SUB drawing]

projects (1) ──< extraction_results (many)    [denormalized project_id, same as via project_drawings]
projects (1) ──< comparison_results (many)    [denormalized project_id, same as via project_drawings]
```

## 4. Useful validation / exploration queries

```sql
-- All projects
SELECT * FROM projects;

-- All drawings for a project, GAD first
SELECT id, title, type, path
FROM project_drawings
WHERE project_id = 1
ORDER BY type, id;

-- Every extracted field for one drawing (e.g. drawing_id = 1, the GAD)
SELECT parameter, value
FROM extraction_results
WHERE drawing_id = 1
ORDER BY parameter;

-- Just the BOM rows extracted from a drawing
SELECT parameter, value
FROM extraction_results
WHERE drawing_id = 1 AND parameter LIKE 'BOM.%'
ORDER BY parameter;

-- Just the title block
SELECT parameter, value
FROM extraction_results
WHERE drawing_id = 1 AND parameter LIKE 'TITLE_BLOCK.%';

-- All comparison findings for one sub-drawing, worst first
SELECT parameter, gad_value, subd_value, status, mismatch_acceptance
FROM comparison_results
WHERE drawing_id = 4
ORDER BY FIELD(status, 'MISMATCH', 'GAD_ONLY', 'SUB_ONLY', 'MATCH');

-- Every MISMATCH across the whole project, with drawing title
SELECT pd.title AS drawing, cr.parameter, cr.gad_value, cr.subd_value, cr.mismatch_acceptance
FROM comparison_results cr
JOIN project_drawings pd ON pd.id = cr.drawing_id
WHERE cr.project_id = 1 AND cr.status = 'MISMATCH';

-- Accept a mismatch (mark it reviewed/OK) by its row id
UPDATE comparison_results SET mismatch_acceptance = 1 WHERE id = 42;

-- Row counts sanity check
SELECT
  (SELECT COUNT(*) FROM projects) AS projects,
  (SELECT COUNT(*) FROM project_drawings) AS drawings,
  (SELECT COUNT(*) FROM extraction_results) AS extraction_rows,
  (SELECT COUNT(*) FROM comparison_results) AS comparison_rows;
```

## 5. Regenerating this schema from scratch

The tables are managed by Alembic, not manual SQL:

```bash
python -m alembic upgrade head    # creates/updates all tables to latest schema
python -m alembic downgrade -1    # rolls back the most recent migration
```

The migration file:
- `alembic/versions/3f9c2a7e1b04_add_project_mysql_tables.py` — `projects`, `project_drawings`, `extraction_results`, `comparison_results`
