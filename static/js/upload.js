// Upload tab: each card auto-uploads + extracts the instant a file is
// selected -- no shared "Run extraction" button. That button used to
// resubmit every card's currently-selected file on every click, which
// silently re-extracted already-finished cards (e.g. uploading a sub after
// the GAD was already extracted re-extracted the GAD too). Per-card, one
// file, one independent upload+extract call -- nothing else is touched.
//
// Each row also gets an "x" button that soft-deletes that specific drawing
// (DELETE /api/v1/drawings/{id}), so a mis-uploaded file can be removed
// without leaving stale duplicate rows lying around (on top of the
// server-side save_drawing() dedup that already supersedes same-title
// re-uploads).
const UPLOAD_API_BASE = "/api/v1";
const DRAWING_TYPE = { GAD: 0, SUB: 1, JOB_CARD: 2, DXF: 3 };

// Deliberately in-memory only (not persisted to localStorage/sessionStorage):
// Run Comparison is gated on what THIS page load has extracted, not on
// whatever the project happens to already have in the DB from an earlier
// session. If the Upload tab shows empty after a refresh, Run Comparison
// must too -- otherwise "extracted and ready to compare" after a refresh
// with nothing visibly uploaded is just confusing. Reset on every reload
// for free, and explicitly on switching projects (see below).
// sub_drawing_names maps sub drawing id -> uploaded filename, so Run
// Comparison can spot heating-element subs for the job card comparison.
//
// The DXF card takes several files: dxf_drawings is every extracted DXF in
// upload order ({id, name}); gad_dxf_drawing_id is the latest one whose name
// contains "GAD" and dxf_drawing_id the latest one that doesn't (the cutting
// sheet) -- see recomputeDxfSlots().
function newSessionUploads() {
  return {
    gad_drawing_id: null, sub_drawing_ids: [], sub_drawing_names: {}, job_card_drawing_id: null,
    dxf_drawings: [], gad_dxf_drawing_id: null, dxf_drawing_id: null,
  };
}

function recomputeDxfSlots(s) {
  const latest = (pred) => [...s.dxf_drawings].reverse().find(pred);
  const gad = latest((d) => isGadDxfName(d.name));
  const cutting = latest((d) => !isGadDxfName(d.name));
  s.gad_dxf_drawing_id = gad ? gad.id : null;
  s.dxf_drawing_id = cutting ? cutting.id : null;
}
window.SessionUploads = newSessionUploads();

const uploadTargetName = document.getElementById("upload-target-name");

window.addEventListener("active-project-changed", (e) => {
  uploadTargetName.textContent = e.detail.name || "Default Project";
  // Ids from the previous project are meaningless once you're pointed at a
  // different project -- clear the session-tracked state along with it.
  window.SessionUploads = newSessionUploads();
  notifyDrawingStateChanged();
});

function notifyDrawingStateChanged() {
  window.dispatchEvent(new CustomEvent("drawing-state-changed"));
}

function addToSessionUploads(drawingType, drawingId, filename) {
  const s = window.SessionUploads;
  if (drawingType === DRAWING_TYPE.GAD) s.gad_drawing_id = drawingId;
  else if (drawingType === DRAWING_TYPE.SUB) {
    if (!s.sub_drawing_ids.includes(drawingId)) s.sub_drawing_ids.push(drawingId);
    s.sub_drawing_names[drawingId] = filename || "";
  }
  else if (drawingType === DRAWING_TYPE.JOB_CARD) s.job_card_drawing_id = drawingId;
  else if (drawingType === DRAWING_TYPE.DXF) {
    if (!s.dxf_drawings.some((d) => d.id === drawingId)) s.dxf_drawings.push({ id: drawingId, name: filename || "" });
    recomputeDxfSlots(s);
  }
}

function removeFromSessionUploads(drawingType, drawingId) {
  const s = window.SessionUploads;
  if (drawingType === DRAWING_TYPE.GAD && s.gad_drawing_id === drawingId) s.gad_drawing_id = null;
  else if (drawingType === DRAWING_TYPE.SUB) {
    s.sub_drawing_ids = s.sub_drawing_ids.filter((id) => id !== drawingId);
    delete s.sub_drawing_names[drawingId];
  }
  else if (drawingType === DRAWING_TYPE.JOB_CARD && s.job_card_drawing_id === drawingId) s.job_card_drawing_id = null;
  else if (drawingType === DRAWING_TYPE.DXF) {
    s.dxf_drawings = s.dxf_drawings.filter((d) => d.id !== drawingId);
    recomputeDxfSlots(s);
  }
}

function setRowStatus(rowEl, statusClass, text) {
  const statusEl = rowEl.querySelector(".upload-row-status");
  statusEl.className = `upload-row-status ${statusClass}`;
  statusEl.textContent = text;
}

function buildRow(filename) {
  const row = document.createElement("div");
  row.className = "upload-row";
  row.innerHTML =
    `<span class="upload-row-name">${esc(filename)}</span>` +
    `<span class="upload-row-status upload-row-pending">Uploading...</span>` +
    `<button type="button" class="upload-row-remove" disabled>&times;</button>`;
  return row;
}

async function pollDrawingStatus(rowEl, drawingId, drawingType) {
  const poll = async () => {
    const res = await fetch(`${UPLOAD_API_BASE}/drawings/${drawingId}`);
    if (!res.ok) throw new Error(`Failed to fetch status (HTTP ${res.status})`);
    const drawing = await res.json();

    if (drawing.is_deleted) {
      return; // removed while extraction was in flight -- row is already gone
    }
    if (drawing.is_extracted) {
      setRowStatus(rowEl, "upload-row-done", "✓ Extracted");
      addToSessionUploads(drawingType, drawingId, rowEl.querySelector(".upload-row-name").textContent);
      notifyDrawingStateChanged();
      return;
    }
    if (drawing.extraction_error) {
      setRowStatus(rowEl, "upload-row-failed", `✗ ${drawing.extraction_error}`);
      notifyDrawingStateChanged();
      return;
    }
    setTimeout(poll, 1500);
  };
  poll().catch((err) => setRowStatus(rowEl, "upload-row-failed", `✗ ${err.message}`));
}

async function uploadAndTrack(card, fileListEl, drawingType, file, isMulti) {
  const row = buildRow(file.name);
  fileListEl.appendChild(row);

  const formData = new FormData();
  formData.append("file", file);
  formData.append("drawing_type", String(drawingType));
  if (window.ActiveProject && window.ActiveProject.id) {
    formData.append("project_id", window.ActiveProject.id);
  }

  let drawingId = null;
  const removeBtn = row.querySelector(".upload-row-remove");

  removeBtn.addEventListener("click", async () => {
    if (drawingId == null) return;
    removeBtn.disabled = true;
    try {
      const res = await fetch(`${UPLOAD_API_BASE}/drawings/${drawingId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Failed to remove (HTTP ${res.status})`);
      row.remove();
      removeFromSessionUploads(drawingType, drawingId);
      if (!isMulti) {
        const input = card.querySelector(".upload-file-input");
        input.classList.remove("hidden");
        input.value = "";
      }
      notifyDrawingStateChanged();
    } catch (err) {
      removeBtn.disabled = false;
      setRowStatus(row, "upload-row-failed", `✗ ${err.message}`);
    }
  });

  try {
    const res = await fetch(`${UPLOAD_API_BASE}/drawings`, { method: "POST", body: formData });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Upload failed (HTTP ${res.status})`);
    }
    const drawing = await res.json();
    drawingId = drawing.id;
    removeBtn.disabled = false;
    setRowStatus(row, "upload-row-pending", "Extracting...");
    notifyDrawingStateChanged();
    pollDrawingStatus(row, drawingId, drawingType);
  } catch (err) {
    setRowStatus(row, "upload-row-failed", `✗ ${err.message}`);
    removeBtn.disabled = false;
  }
}

document.querySelectorAll(".upload-card").forEach((card) => {
  const drawingType = Number(card.dataset.drawingType);
  const isMulti = card.dataset.multi === "true";
  const input = card.querySelector(".upload-file-input");
  const fileListEl = card.querySelector(".upload-file-list");

  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    if (!files.length) return;

    if (!isMulti) {
      input.classList.add("hidden");
      uploadAndTrack(card, fileListEl, drawingType, files[0], false);
    } else {
      for (const f of files) {
        uploadAndTrack(card, fileListEl, drawingType, f, true);
      }
    }
    input.value = "";
  });
});
