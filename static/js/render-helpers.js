// Shared rendering helpers used by browse.js, upload.js and run-comparison.js.
// Loaded before all three so its functions are plain globals, same pattern
// as project-bar.js's window.ActiveProject.

function esc(v) {
  return (v ?? "-").toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const STATUS_TAG_CLASS = {
  CONSISTENT: "status-consistent",
  DISCREPANCIES_FOUND: "status-discrepancies_found",
  COULD_NOT_MATCH: "status-could_not_match",
};

const FINDING_ROW_CLASS = {
  MISMATCH: "row-mismatch",
  MATCH: "row-match",
  GAD_ONLY: "row-info",
  JOBCARD_ONLY: "row-info",
  SUB_ONLY: "row-info",
};

const FINDING_ROW_ORDER = { MISMATCH: 0, GAD_ONLY: 1, JOBCARD_ONLY: 1, SUB_ONLY: 1, MATCH: 2 };

// findings items come from two different response shapes -- the fresh
// ComparisonReportOut (sub_value, from run-comparison.js) and the persisted
// ComparisonFindingItem (subd_value, from browse.js) -- fall back across
// both so this one table renderer works for either.
// opts.leftLabel/leftKey/acceptKind let the same table render job card vs
// sub findings (jobcard_value, accept-checkbox PATCHing the jobcard-results
// endpoint) as well as the default GAD vs sub ones.
function findingsTableHtml(findings, opts = {}) {
  const { leftLabel = "GAD Value", leftKey = "gad_value", acceptKind = "" } = opts;
  if (!findings.length) {
    return `<p class="empty-note">No comparable parameters found.</p>`;
  }
  const rows = [...findings].sort((a, b) => (FINDING_ROW_ORDER[a.status] ?? 3) - (FINDING_ROW_ORDER[b.status] ?? 3));
  return (
    `<table class="compare-table"><thead><tr><th>Status</th><th>Parameter</th>` +
    `<th>${esc(leftLabel)}</th><th>Sub-Drawing Value</th><th>Accept</th></tr></thead><tbody>` +
    rows
      .map((f) => {
        const subValue = f.sub_value ?? f.subd_value;
        const accepted = !!f.mismatch_acceptance;
        const canAccept = f.status === "MISMATCH" && f.id != null;
        const acceptCell = canAccept
          ? `<input type="checkbox" class="mismatch-accept-checkbox" data-id="${f.id}" data-kind="${acceptKind}" ${accepted ? "checked" : ""} />`
          : "";
        return (
          `<tr class="${FINDING_ROW_CLASS[f.status] || ""}${accepted ? " row-accepted" : ""}">` +
          `<td>${esc(f.status)}</td><td>${esc(f.parameter)}</td>` +
          `<td>${esc(f[leftKey])}</td><td>${esc(subValue)}</td>` +
          `<td class="accept-cell">${acceptCell}</td></tr>`
        );
      })
      .join("") +
    `</tbody></table>`
  );
}

// Delegated so it works for checkboxes rendered into any container (Run
// Comparison tab's result view, Browse tab's drawing detail) without each
// caller having to wire up its own listener.
document.addEventListener("change", async (e) => {
  const checkbox = e.target;
  if (!checkbox.matches || !checkbox.matches(".mismatch-accept-checkbox")) return;

  const id = checkbox.dataset.id;
  const accepted = checkbox.checked;
  checkbox.disabled = true;
  try {
    const kind = checkbox.dataset.kind;
    const endpoint =
      kind === "jobcard" ? `/api/v1/comparisons/jobcard-results/${id}`
      : kind === "gaddxf" ? `/api/v1/dxf/gad-validate/matches/${id}`
      : `/api/v1/comparisons/results/${id}`;
    const res = await fetch(endpoint, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mismatch_acceptance: accepted }),
    });
    if (!res.ok) throw new Error(`Failed to update (HTTP ${res.status})`);
    checkbox.closest("tr")?.classList.toggle("row-accepted", accepted);
  } catch (err) {
    checkbox.checked = !accepted;
    alert(err.message);
  } finally {
    checkbox.disabled = false;
  }
});

// Renders a full ComparisonReport (GAD + all its parts) into a container element.
function renderComparisonReport(containerEl, report, usage) {
  const parts = [];

  const totalMismatches = report.parts.reduce(
    (sum, p) => sum + p.findings.filter((f) => f.status === "MISMATCH").length,
    0
  );
  parts.push(
    `<div class="summary-line">GAD: ${esc(report.gad_file)} | Drawing No: ${esc(report.gad_drawing_no || "n/a")} | ` +
    `${report.parts.length} sub-drawing(s) checked | ${totalMismatches} mismatch(es) found</div>`
  );

  if (usage) {
    parts.push(
      `<div class="usage-box">Token usage: ${usage.total_tokens.toLocaleString()} total ` +
      `(in=${usage.input_tokens.toLocaleString()}, out=${usage.output_tokens.toLocaleString()}) ` +
      `— est. cost $${usage.estimated_cost_usd.toFixed(4)}</div>`
    );
  }

  for (const part of report.parts) {
    parts.push(`<div class="compare-part">`);
    parts.push(`<h3>${esc(part.part_name)}</h3>`);
    parts.push(`<span class="status-tag ${STATUS_TAG_CLASS[part.overall_status] || ""}">${esc(part.overall_status).replace(/_/g, " ")}</span>`);
    parts.push(`<div class="part-file">Sub-drawing file: ${esc(part.sub_drawing_file)}</div>`);
    if (part.matched_bom_row) {
      const b = part.matched_bom_row;
      parts.push(
        `<div class="bom-line">Matched GAD BOM row: SR ${esc(b.sr_no)} — ${esc(b.description)} | ` +
        `Qty ${esc(b.qty)} | ${esc(b.material)} | ${esc(b.size)}</div>`
      );
    }
    parts.push(findingsTableHtml(part.findings));
    parts.push(`</div>`);
  }

  containerEl.innerHTML = parts.join("");
}

// reports: array of JobCardComparisonReportOut (one per compared sub-drawing).
function renderJobCardComparison(containerEl, reports, usage) {
  const parts = [];

  const totalMismatches = reports.reduce(
    (sum, r) => sum + r.findings.filter((f) => f.status === "MISMATCH").length,
    0
  );
  parts.push(
    `<div class="summary-line">Job card vs ${reports.length} sub-drawing(s) | ${totalMismatches} mismatch(es) found</div>`
  );

  if (usage) {
    parts.push(
      `<div class="usage-box">Token usage: ${usage.total_tokens.toLocaleString()} total ` +
      `(in=${usage.input_tokens.toLocaleString()}, out=${usage.output_tokens.toLocaleString()}) ` +
      `— est. cost $${usage.estimated_cost_usd.toFixed(4)}</div>`
    );
  }

  for (const r of reports) {
    parts.push(`<div class="compare-part">`);
    parts.push(`<h3>${esc(r.sub_drawing_file)}</h3>`);
    parts.push(`<span class="status-tag ${STATUS_TAG_CLASS[r.overall_status] || ""}">${esc(r.overall_status).replace(/_/g, " ")}</span>`);
    parts.push(`<div class="part-file">Job card: ${esc(r.job_card_file)}</div>`);
    parts.push(findingsTableHtml(r.findings, { leftLabel: "Job Card Value", leftKey: "jobcard_value", acceptKind: "jobcard" }));
    parts.push(`</div>`);
  }

  containerEl.innerHTML = parts.join("");
}

// ---------------------------------------------------------------------------
// GAD DXF vs cutting-sheet DXF validation (Run Comparison card 4, Browse tab,
// View-files modal). Mirrors gad_dxf_service.is_gad_dxf_name -- keep in sync.
// ---------------------------------------------------------------------------

function isGadDxfName(filename) {
  return (filename || "").toLowerCase().replace(/[^a-z0-9]/g, "").includes("gad");
}

function fmtNum(v) {
  return v == null ? "-" : String(Math.round(Number(v) * 100) / 100);
}

// Renders one validation run (GadDxfRunOut) into containerEl: the overlay
// image (swappable to a per-capsule highlight) and a match table.
// Safe to call repeatedly on the same container -- listeners live on a fresh
// inner root each time.
function renderGadDxfValidation(containerEl, run) {
  const rows = run.matches
    .map((m) => {
      const accepted = !!m.mismatch_acceptance;
      const canAccept = m.status === "MISMATCH";
      const acceptCell = canAccept
        ? `<input type="checkbox" class="mismatch-accept-checkbox" data-id="${m.id}" data-kind="gaddxf" ${accepted ? "checked" : ""} />`
        : "";
      return (
        `<tr class="${FINDING_ROW_CLASS[m.status] || ""}${accepted ? " row-accepted" : ""}" data-index="${m.capsule_index}">` +
        `<td>#${m.capsule_index}</td><td>${esc(m.status)}</td>` +
        `<td>${fmtNum(m.capsule_radius)}</td><td>${fmtNum(m.hole_radius)}</td><td>${fmtNum(m.center_offset)}</td>` +
        `<td class="accept-cell">${acceptCell}</td>` +
        `<td><button type="button" class="gaddxf-hl-btn" data-index="${m.capsule_index}" data-state="highlight">Highlight</button></td></tr>`
      );
    })
    .join("");

  containerEl.innerHTML =
    `<div class="gaddxf-root">` +
      `<div class="summary-line">GAD DXF: ${esc(run.gad_file)} <span class="compared-vs">vs</span> Cutting DXF: ${esc(run.cutting_file)}</div>` +
      `<div class="gaddxf-layout">` +
        `<div class="gaddxf-image">` +
          `<div class="gaddxf-image-bar"><span data-role="caption">Overlay: GAD capsules (red) on baffle holes (blue)</span></div>` +
          `<div class="gaddxf-image-stage">` +
            `<img data-role="img" src="${run.overlay_url}" alt="GAD vs baffle overlay" ` +
              `data-lightbox-src="${run.overlay_url}" data-lightbox-label="GAD vs baffle overlay" />` +
          `</div>` +
          `<div class="gaddxf-error hidden" data-role="error"></div>` +
        `</div>` +
        `<div class="gaddxf-matches">` +
          (rows
            ? `<table class="compare-table"><thead><tr><th>Capsule</th><th>Status</th><th>Capsule R</th><th>Hole R</th>` +
              `<th>Offset</th><th>Accept</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
            : `<p class="empty-note">No capsule matches stored.</p>`) +
        `</div>` +
      `</div>` +
    `</div>`;

  const root = containerEl.querySelector(".gaddxf-root");
  const $ = (role) => root.querySelector(`[data-role="${role}"]`);
  const img = $("img"), caption = $("caption"), errBox = $("error");
  const defaultCaption = caption.textContent;

  function setButtonState(btn, active) {
    btn.dataset.state = active ? "active" : "highlight";
    btn.textContent = active ? "Clear" : "Highlight";
    btn.classList.toggle("gaddxf-hl-btn-active", active);
  }

  function showImage(url, text, selectedIndex) {
    img.classList.add("loading");
    img.onload = img.onerror = () => img.classList.remove("loading");
    img.src = url;
    img.dataset.lightboxSrc = url;
    img.dataset.lightboxLabel = text;
    caption.textContent = text;
    root.querySelectorAll("tr.row-selected").forEach((tr) => tr.classList.remove("row-selected"));
    if (selectedIndex != null) root.querySelector(`tr[data-index="${selectedIndex}"]`)?.classList.add("row-selected");
  }

  root.addEventListener("click", async (e) => {
    const btn = e.target.closest(".gaddxf-hl-btn");
    if (!btn) return;
    const index = btn.dataset.index;
    errBox.classList.add("hidden");

    // Clicking the currently-active row's own button clears it -- no
    // separate "Clear highlight" button needed.
    if (btn.dataset.state === "active") {
      showImage(run.overlay_url, defaultCaption, null);
      setButtonState(btn, false);
      return;
    }

    // Only one highlight is shown at a time -- reset any other row's button.
    root.querySelectorAll('.gaddxf-hl-btn[data-state="active"]').forEach((b) => setButtonState(b, false));

    btn.disabled = true;
    btn.textContent = "Rendering...";
    try {
      const res = await fetch(`/api/v1/dxf/gad-validate/${run.id}/highlight/${index}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Could not render highlight (HTTP ${res.status})`);
      showImage(body.image_url, `Highlighting capsule #${index}`, index);
      setButtonState(btn, true);
      img.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      errBox.textContent = err.message;
      errBox.classList.remove("hidden");
      setButtonState(btn, false);
    } finally {
      btn.disabled = false;
    }
  });
}

// Appends a heading plus one rendered validation run per entry to parentEl
// (Browse detail pane and the View-files modal).
function appendGadDxfValidations(parentEl, runs, headingText) {
  if (!runs || !runs.length) return;
  const h = document.createElement("h4");
  h.textContent = headingText || "GAD vs DXF validation";
  parentEl.appendChild(h);
  for (const run of runs) {
    const box = document.createElement("div");
    box.className = "gaddxf-embedded";
    parentEl.appendChild(box);
    renderGadDxfValidation(box, run);
  }
}

// Browse tab: persisted job card vs sub-drawing comparisons for one drawing.
// viewing is "JOB_CARD" or "SUB" (the drawing being shown); each group's
// counterpart is the drawing on the other side.
function renderJobCardComparisonGroups(parts, groups, viewing, title) {
  if (!groups || !groups.length) {
    if (viewing === "JOB_CARD") {
      parts.push(`<h4>Comparison with sub drawing</h4>`);
      parts.push(`<p class="empty-note">No comparison against a sub drawing has been run for this job card.</p>`);
    }
    return;
  }

  parts.push(viewing === "JOB_CARD" ? `<h4>Comparison with sub drawing</h4>` : `<h4>Comparison with job card</h4>`);
  for (const g of groups) {
    const jobCardName = viewing === "JOB_CARD" ? title : g.counterpart.title;
    const subName = viewing === "JOB_CARD" ? g.counterpart.title : title;
    parts.push(
      `<div class="compared-files">` +
        `<span class="compared-file-tag drawing-type-job_card">JOB CARD</span> ${esc(jobCardName)}` +
        `<span class="compared-vs">vs</span>` +
        `<span class="compared-file-tag drawing-type-sub">SUB</span> ${esc(subName)}` +
      `</div>`
    );
    parts.push(findingsTableHtml(g.findings, { leftLabel: "Job Card Value", leftKey: "jobcard_value", acceptKind: "jobcard" }));
  }
}

function renderJobCardExtraction(parts, jc) {
  if (!jc) {
    parts.push(`<p class="empty-note">No extraction data stored for this job card.</p>`);
    return;
  }

  parts.push(`<h4>Extraction result</h4>`);

  const dm = jc.document_meta || {};
  const dmEntries = Object.entries(dm).filter(([, v]) => v);
  if (dmEntries.length) {
    parts.push(`<h5>Document info</h5><table class="detail-table"><tbody>`);
    for (const [k, v] of dmEntries) {
      parts.push(`<tr><td class="kcell">${esc(k.replace(/_/g, " "))}</td><td>${esc(v)}</td></tr>`);
    }
    parts.push(`</tbody></table>`);
  }

  if (jc.parameters && jc.parameters.length) {
    parts.push(
      `<h5>Parameters</h5>` +
      `<table class="detail-table"><thead><tr><th>Parameter</th><th>Value</th><th>Unit</th>` +
      `<th>Category</th><th>Section</th></tr></thead><tbody>`
    );
    for (const p of jc.parameters) {
      parts.push(
        `<tr><td>${esc(p.parameter)}</td><td>${esc(p.value)}</td><td>${esc(p.unit)}</td>` +
        `<td>${esc(p.category)}</td><td>${esc(p.table_or_section)}</td></tr>`
      );
    }
    parts.push(`</tbody></table>`);
  }

  if (jc.notes_and_flags && jc.notes_and_flags.length) {
    parts.push(`<h5>Notes & flags</h5><ul class="notes-list">`);
    for (const n of jc.notes_and_flags) {
      parts.push(`<li>${esc(n)}</li>`);
    }
    parts.push(`</ul>`);
  }
}

// Renders a GAD/SUB drawing's ExtractionView (title block, specs, BOM,
// dimensional callouts, notes). Shared by browse.js's detail pane and the
// Run Comparison tab's "View" modal so both render identically.
function renderDrawingExtraction(parts, ex) {
  if (!ex) {
    parts.push(`<p class="empty-note">No extraction data stored for this drawing.</p>`);
    return;
  }

  parts.push(`<h4>Extraction result</h4>`);

  if (ex.drawing_category) {
    parts.push(`<div class="kv-line"><strong>Category:</strong> ${esc(ex.drawing_category)}</div>`);
  }

  const tb = ex.title_block || {};
  if (Object.keys(tb).length) {
    parts.push(`<h5>Title block</h5><table class="detail-table"><tbody>`);
    for (const [k, v] of Object.entries(tb)) {
      parts.push(`<tr><td class="kcell">${esc(k.replace(/_/g, " "))}</td><td>${esc(v)}</td></tr>`);
    }
    parts.push(`</tbody></table>`);
  }

  if (ex.specifications && ex.specifications.length) {
    parts.push(`<h5>Specifications</h5><table class="detail-table"><tbody>`);
    for (const s of ex.specifications) {
      parts.push(`<tr><td class="kcell">${esc(s.parameter)}</td><td>${esc(s.value)}</td></tr>`);
    }
    parts.push(`</tbody></table>`);
  }

  if (ex.bill_of_materials && ex.bill_of_materials.length) {
    parts.push(
      `<h5>Bill of materials</h5>` +
      `<table class="detail-table"><thead><tr><th>SR</th><th>Description</th><th>Qty</th>` +
      `<th>Material</th><th>Size</th><th>Remarks</th><th>Table</th></tr></thead><tbody>`
    );
    for (const b of ex.bill_of_materials) {
      parts.push(
        `<tr><td>${esc(b.sr_no)}</td><td>${esc(b.description)}</td><td>${esc(b.qty)}</td>` +
        `<td>${esc(b.material)}</td><td>${esc(b.size)}</td><td>${esc(b.remarks)}</td>` +
        `<td>${esc(b.bom_table)}</td></tr>`
      );
    }
    parts.push(`</tbody></table>`);
  }

  if (ex.dimensional_callouts && ex.dimensional_callouts.length) {
    parts.push(`<h5>Dimensional callouts</h5><table class="detail-table"><thead><tr><th>Label</th><th>Category</th></tr></thead><tbody>`);
    for (const c of ex.dimensional_callouts) {
      parts.push(`<tr><td>${esc(c.label)}</td><td>${esc(c.category)}</td></tr>`);
    }
    parts.push(`</tbody></table>`);
  }

  if (ex.notes && ex.notes.length) {
    parts.push(`<h5>Notes</h5><ul class="notes-list">`);
    for (const n of ex.notes) {
      parts.push(`<li>${esc(n)}</li>`);
    }
    parts.push(`</ul>`);
  }
}

function renderDxfOutputs(parts, outputs) {
  if (!outputs || !outputs.length) {
    parts.push(`<p class="empty-note">No overlay images stored for this DXF file (FULL_BAFFLE/SEGMENTAL_BAFFLE_A/B parts may not have been found in the sheet).</p>`);
    return;
  }

  parts.push(`<h4>Overlay result</h4><div class="dxf-gallery">`);
  for (const o of outputs) {
    const label = o.label.replace(/_/g, " ");
    parts.push(
      `<div class="dxf-gallery-item">` +
        `<div class="dxf-gallery-label">${esc(label)}</div>` +
        `<img src="${o.image_url}" alt="${esc(o.label)}" ` +
          `data-lightbox-src="${o.image_url}" data-lightbox-label="${esc(label)}" />` +
      `</div>`
    );
  }
  parts.push(`</div>`);
}
