// Run Comparison tab: 3 independent actions, each gated on THIS SESSION's
// extractions (window.SessionUploads, maintained by upload.js), not on
// whatever the project already has persisted in the DB. Deliberate: the
// Upload tab always starts empty after a page refresh, so Run Comparison
// starts fully disabled too -- showing "ready to compare" for drawings the
// Upload tab doesn't visibly list would be confusing. The extraction data
// itself is still safely persisted (see [[drawing-validator-per-file-upload]]
// memory) and reachable via the Browse tab; re-uploading the same file just
// supersedes the old row (save_drawing()'s dedup) rather than duplicating it.
const RUN_API_BASE = "/api/v1";

const compareBtn = document.getElementById("run-compare-btn");
const compareViewBtn = document.getElementById("run-compare-view-btn");
const compareStatus = document.getElementById("run-compare-status");
const compareResult = document.getElementById("run-compare-result");

const jobCardBtn = document.getElementById("run-jobcard-btn");
const jobCardViewBtn = document.getElementById("run-jobcard-view-btn");
const jobCardResult = document.getElementById("run-jobcard-result");
const jobCardCompareBtn = document.getElementById("run-jobcard-compare-btn");
const jobCardCompareStatus = document.getElementById("run-jobcard-compare-status");
const jobCardCompareResult = document.getElementById("run-jobcard-compare-result");

// Mirrors JOBCARD_COMPARABLE_SUB_KEYWORDS in app/services/comparison_service.py
// (the server re-checks it) -- keep in sync.
const JOBCARD_COMPARABLE_SUB_KEYWORDS = ["heatingelement"];

function isJobCardComparableSub(filename) {
  const normalized = (filename || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return JOBCARD_COMPARABLE_SUB_KEYWORDS.some((k) => normalized.includes(k));
}

function jobCardComparableSubIds(state) {
  if (!state) return [];
  
  return state.sub_drawing_ids.filter((id) => isJobCardComparableSub(state.sub_drawing_names[id]));
}

// Brings a freshly rendered result (or error) into view and resets its own
// scroll position -- results scroll inside a capped box (see .run-result).
function revealResult(el) {
  if (!el || !el.innerHTML.trim()) return;
  el.scrollTop = 0;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
}

const dxfBtn = document.getElementById("run-dxf-btn");
const dxfViewBtn = document.getElementById("run-dxf-view-btn");
const dxfStatus = document.getElementById("run-dxf-status");
const dxfResult = document.getElementById("run-dxf-result");

const gadDxfBtn = document.getElementById("run-gaddxf-btn");
const gadDxfViewBtn = document.getElementById("run-gaddxf-view-btn");
const gadDxfStatus = document.getElementById("run-gaddxf-status");
const gadDxfResult = document.getElementById("run-gaddxf-result");

function refreshRunButtons() {
  const state = window.SessionUploads;
  // Compare itself genuinely needs both sides. "View files" just shows
  // whatever's been extracted so far -- independent of that, so a GAD-only
  // (or subs-only) upload is still viewable before its counterpart exists.
  const hasCompareInputs = !!(state && state.gad_drawing_id && state.sub_drawing_ids.length);
  const hasAnyCompareFile = !!(state && (state.gad_drawing_id || state.sub_drawing_ids.length));
  const hasJobCard = !!(state && state.job_card_drawing_id);
  const hasDxf = !!(state && state.dxf_drawing_id);

  compareBtn.disabled = !hasCompareInputs;
  compareViewBtn.disabled = !hasAnyCompareFile;
  jobCardBtn.disabled = !hasJobCard;
  jobCardCompareBtn.disabled = !(hasJobCard && jobCardComparableSubIds(state).length);
  jobCardViewBtn.disabled = !hasJobCard;
  dxfBtn.disabled = !hasDxf;
  dxfViewBtn.disabled = !hasDxf;

  // GAD vs DXF: needs a GAD DXF AND another (cutting-sheet) DXF; "View files"
  // shows whichever of the two exist so far.
  const hasGadDxf = !!(state && state.gad_dxf_drawing_id);
  gadDxfBtn.disabled = !(hasGadDxf && hasDxf);
  gadDxfViewBtn.disabled = !(hasGadDxf || hasDxf);
}

window.addEventListener("drawing-state-changed", refreshRunButtons);
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.tab === "run") refreshRunButtons();
  });
});
refreshRunButtons();

compareBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  compareBtn.disabled = true;
  compareResult.innerHTML = "";
  compareStatus.classList.remove("hidden");
  compareStatus.textContent = "Comparing...";

  try {
    const res = await fetch(`${RUN_API_BASE}/comparisons/from-extraction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: window.ActiveProject.id,
        gad_drawing_id: state.gad_drawing_id,
        sub_drawing_ids: state.sub_drawing_ids,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || `Comparison failed (HTTP ${res.status})`);
    }
    compareStatus.classList.add("hidden");
    renderComparisonReport(compareResult, body.result, body.usage);
  } catch (err) {
    compareStatus.classList.add("hidden");
    compareResult.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  } finally {
    compareBtn.disabled = false;
    revealResult(compareResult);
  }
});

jobCardBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  jobCardBtn.disabled = true;
  jobCardResult.innerHTML = "Loading...";

  try {
    const res = await fetch(`${RUN_API_BASE}/projects/${window.ActiveProject.id}/drawings/${state.job_card_drawing_id}`);
    if (!res.ok) throw new Error(`Failed to load job card (HTTP ${res.status})`);
    const drawing = await res.json();
    const parts = [];
    renderJobCardExtraction(parts, drawing.job_card_extraction);
    jobCardResult.innerHTML = parts.join("");
  } catch (err) {
    jobCardResult.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  } finally {
    jobCardBtn.disabled = false;
    revealResult(jobCardResult);
  }
});

jobCardCompareBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  const subIds = jobCardComparableSubIds(state);
  jobCardCompareBtn.disabled = true;
  jobCardCompareResult.innerHTML = "";
  jobCardCompareStatus.classList.remove("hidden");
  jobCardCompareStatus.textContent = "Comparing job card with sub drawing...";

  try {
    const reports = [];
    const usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0, estimated_cost_usd: 0 };
    for (const subId of subIds) {
      const res = await fetch(`${RUN_API_BASE}/comparisons/jobcard-vs-sub`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: window.ActiveProject.id,
          job_card_drawing_id: state.job_card_drawing_id,
          sub_drawing_id: subId,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || `Comparison failed (HTTP ${res.status})`);
      }
      reports.push(body.result);
      for (const k of Object.keys(usage)) usage[k] += body.usage[k];
    }
    jobCardCompareStatus.classList.add("hidden");
    renderJobCardComparison(jobCardCompareResult, reports, usage);
  } catch (err) {
    jobCardCompareStatus.classList.add("hidden");
    jobCardCompareResult.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  } finally {
    refreshRunButtons();
    revealResult(jobCardCompareResult);
  }
});

dxfBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  dxfBtn.disabled = true;
  dxfResult.innerHTML = "";
  dxfStatus.classList.remove("hidden");
  dxfStatus.textContent = "Validating...";

  try {
    const res = await fetch(`${RUN_API_BASE}/dxf/${state.dxf_drawing_id}/validate`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Validation failed (HTTP ${res.status})`);
    }
    const outputs = await res.json();
    const parts = [];
    renderDxfOutputs(parts, outputs);
    dxfResult.innerHTML = parts.join("");
  } catch (err) {
    dxfResult.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  } finally {
    dxfStatus.classList.add("hidden");
    dxfBtn.disabled = false;
    revealResult(dxfResult);
  }
});

gadDxfBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  gadDxfBtn.disabled = true;
  gadDxfResult.innerHTML = "";
  gadDxfStatus.classList.remove("hidden");
  gadDxfStatus.textContent = "Tracing GAD capsules against the baffle...";

  try {
    const res = await fetch(`${RUN_API_BASE}/dxf/gad-validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: window.ActiveProject.id,
        gad_drawing_id: state.gad_dxf_drawing_id,
        cutting_drawing_id: state.dxf_drawing_id,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || `Validation failed (HTTP ${res.status})`);
    }
    renderGadDxfValidation(gadDxfResult, body);
  } catch (err) {
    gadDxfResult.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  } finally {
    gadDxfStatus.classList.add("hidden");
    refreshRunButtons();
    revealResult(gadDxfResult);
  }
});

compareViewBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  const ids = [state.gad_drawing_id, ...state.sub_drawing_ids].filter(Boolean);
  const infos = await fetchDrawingInfos(ids);
  openViewModal("Compare: GAD vs Sub Drawings — uploaded files", infos);
});

jobCardViewBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  // The job card plus every heating-element sub-drawing it gets compared against.
  const ids = [state.job_card_drawing_id, ...jobCardComparableSubIds(state)].filter(Boolean);
  const infos = await fetchDrawingInfos(ids);
  openViewModal("Job Card & compared sub drawing — uploaded files", infos);
});

gadDxfViewBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  const infos = await fetchDrawingInfos([state.gad_dxf_drawing_id, state.dxf_drawing_id].filter(Boolean));
  openViewModal("GAD vs DXF Validation — uploaded files", infos);
});

dxfViewBtn.addEventListener("click", async () => {
  const state = window.SessionUploads;
  const infos = await fetchDrawingInfos([state.dxf_drawing_id]);
  openViewModal("Validate DXF — uploaded file", infos);
});
