// Shared "View" modal for the Run Comparison tab: shows the list of files
// used for a given operation (Compare / Job Card / Validate), each as an
// accordion item (native <details>/<summary>) that lazy-loads and renders
// that drawing's extraction the first time it's expanded.
const VIEW_MODAL_API_BASE = "/api/v1";

const viewModalOverlay = document.getElementById("view-modal-overlay");
const viewModalTitle = document.getElementById("view-modal-title");
const viewModalBody = document.getElementById("view-modal-body");
const viewModalClose = document.getElementById("view-modal-close");

function closeViewModal() {
  viewModalOverlay.classList.add("hidden");
  viewModalBody.innerHTML = "";
}

viewModalClose.addEventListener("click", closeViewModal);
viewModalOverlay.addEventListener("click", (e) => {
  if (e.target === viewModalOverlay) closeViewModal();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !viewModalOverlay.classList.contains("hidden")) closeViewModal();
});

function renderExtractionInto(bodyEl, drawing) {
  const parts = [];
  const isGadDxf = drawing.type_label === "DXF" && isGadDxfName(drawing.title);
  if (drawing.type_label === "JOB_CARD") {
    renderJobCardExtraction(parts, drawing.job_card_extraction);
  } else if (drawing.type_label === "DXF") {
    if (isGadDxf) {
      parts.push(`<p class="empty-note">GAD DXF — nothing is extracted up front; the file is read when GAD vs DXF validation runs.</p>`);
    } else {
      renderDxfOutputs(parts, drawing.dxf_outputs);
    }
  } else {
    renderDrawingExtraction(parts, drawing.extraction);
  }
  bodyEl.innerHTML = parts.join("") || `<p class="empty-note">No data.</p>`;
  // Only the GAD DXF's own accordion item shows the GAD-vs-cutting-sheet
  // validation it took part in -- the cutting/baffle DXF's item only shows
  // its own full-vs-segmental overlay (above), not both.
  if (isGadDxf) {
    appendGadDxfValidations(bodyEl, drawing.gad_dxf_validations, "GAD vs DXF validation");
  }
}

function buildAccordionItem(fileInfo) {
  const details = document.createElement("details");
  details.className = "view-accordion-item";

  const summary = document.createElement("summary");
  summary.innerHTML =
    `<span class="drawing-type-tag drawing-type-${(fileInfo.type_label || "").toLowerCase()}">${esc(fileInfo.type_label)}</span>` +
    `<span class="view-accordion-title">${esc(fileInfo.title)}</span>`;
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "view-accordion-body";
  body.textContent = "Click to load extraction...";
  details.appendChild(body);

  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    body.textContent = "Loading...";
    try {
      const res = await fetch(`${VIEW_MODAL_API_BASE}/projects/${window.ActiveProject.id}/drawings/${fileInfo.id}`);
      if (!res.ok) throw new Error(`Failed to load drawing (HTTP ${res.status})`);
      const drawing = await res.json();
      renderExtractionInto(body, drawing);
    } catch (err) {
      loaded = false;
      body.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  });

  return details;
}

// files: array of {id, title, type_label} (from GET /api/v1/drawings/{id})
function openViewModal(title, files) {
  viewModalTitle.textContent = title;
  viewModalBody.innerHTML = "";

  if (!files.length) {
    viewModalBody.innerHTML = `<p class="empty-note">No files to show.</p>`;
  } else {
    for (const f of files) {
      viewModalBody.appendChild(buildAccordionItem(f));
    }
  }

  viewModalOverlay.classList.remove("hidden");
}

// Fetches lightweight {id, title, type_label} status for each drawing id
// (used to label accordion items before they're expanded).
async function fetchDrawingInfos(ids) {
  const results = await Promise.all(
    ids.map(async (id) => {
      try {
        const res = await fetch(`${VIEW_MODAL_API_BASE}/drawings/${id}`);
        if (!res.ok) throw new Error();
        return await res.json();
      } catch {
        return { id, title: `Drawing #${id}`, type_label: "" };
      }
    })
  );
  return results;
}
