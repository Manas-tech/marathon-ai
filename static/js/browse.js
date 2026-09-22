const BROWSE_API_BASE = "/api/v1";

const tabBtns = document.querySelectorAll(".tab-btn");
const tabPanels = document.querySelectorAll(".tab-panel");

const drawingListEl = document.getElementById("drawing-list");
const drawingDetailEl = document.getElementById("drawing-detail");
const browseProjectNameEl = document.getElementById("browse-project-name");

let browseTabActive = false;
let pendingProjectId = null;

// Makes the Browse panes fill the rest of the window: height = viewport minus
// whatever sits above the layout (title, project bar, tabs). Skipped on narrow
// screens where the panes stack and cap themselves instead (see style.css).
const browseLayoutEl = document.querySelector(".browse-layout");
function sizeBrowseLayout() {
  if (!browseLayoutEl) return;
  if (!browseTabActive || window.innerWidth <= 900) {
    browseLayoutEl.style.height = "";
    return;
  }
  const top = browseLayoutEl.getBoundingClientRect().top + window.scrollY;
  // 40px = the body's bottom padding (2rem) plus a little breathing room, so
  // the page itself doesn't gain a second scrollbar.
  browseLayoutEl.style.height = `${Math.max(420, window.innerHeight - top - 40)}px`;
}
window.addEventListener("resize", sizeBrowseLayout);

tabBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabBtns.forEach((b) => b.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");

    browseTabActive = btn.dataset.tab === "browse";
    document.body.classList.toggle("browse-active", browseTabActive);
    sizeBrowseLayout();
    if (browseTabActive && pendingProjectId !== null) {
      loadProjectDrawings(pendingProjectId, window.ActiveProject.name);
      pendingProjectId = null;
    }
  });
});

window.addEventListener("active-project-changed", (e) => {
  drawingDetailEl.textContent = "Select a drawing.";
  const { id, name } = e.detail;
  if (id == null) {
    browseProjectNameEl.textContent = "";
    drawingListEl.textContent = "No project selected.";
    return;
  }
  if (browseTabActive) {
    loadProjectDrawings(id, name);
  } else {
    pendingProjectId = id;
  }
});

async function loadProjectDrawings(projectId, projectName) {
  browseProjectNameEl.textContent = projectName ? `— ${projectName}` : "";
  drawingListEl.textContent = "Loading...";
  try {
    const res = await fetch(`${BROWSE_API_BASE}/projects/${projectId}`);
    if (!res.ok) throw new Error(`Failed to load project (HTTP ${res.status})`);
    const project = await res.json();

    if (!project.drawings.length) {
      drawingListEl.textContent = "No drawings uploaded for this project yet.";
      return;
    }

    drawingListEl.innerHTML = "";
    for (const d of project.drawings) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML =
        `<span class="drawing-type-tag drawing-type-${d.type_label.toLowerCase()}">${d.type_label}</span>` +
        `<div class="list-item-title">${esc(d.title)}</div>` +
        `<div class="list-item-sub">${formatDate(d.created_at)}</div>`;
      item.addEventListener("click", () => selectDrawing(projectId, d.id, item));
      drawingListEl.appendChild(item);
    }
  } catch (err) {
    drawingListEl.textContent = err.message;
  }
}

async function selectDrawing(projectId, drawingId, itemEl) {
  document.querySelectorAll("#drawing-list .list-item").forEach((el) => el.classList.remove("selected"));
  if (itemEl) itemEl.classList.add("selected");

  drawingDetailEl.textContent = "Loading...";
  try {
    const res = await fetch(`${BROWSE_API_BASE}/projects/${projectId}/drawings/${drawingId}`);
    if (!res.ok) throw new Error(`Failed to load drawing (HTTP ${res.status})`);
    const drawing = await res.json();
    renderDrawingDetail(drawing);
  } catch (err) {
    drawingDetailEl.textContent = err.message;
  }
}

function renderDrawingDetail(drawing) {
  const parts = [];

  parts.push(
    `<div class="detail-header">` +
      `<span class="drawing-type-tag drawing-type-${drawing.type_label.toLowerCase()}">${drawing.type_label}</span>` +
      `<h3>${esc(drawing.title)}</h3>` +
      `<div class="list-item-sub">Uploaded ${formatDate(drawing.created_at)}</div>` +
    `</div>`
  );

  if (drawing.type_label === "JOB_CARD") {
    renderJobCardExtraction(parts, drawing.job_card_extraction);
    renderJobCardComparisonGroups(parts, drawing.jobcard_comparisons, "JOB_CARD", drawing.title);
    drawingDetailEl.innerHTML = parts.join("");
    return;
  }

  if (drawing.type_label === "DXF") {
    const isGad = isGadDxfName(drawing.title);
    if (isGad) {
      parts.push(`<p class="empty-note">GAD DXF — nothing is extracted up front; the file is read when GAD vs DXF validation runs.</p>`);
    } else {
      renderDxfOutputs(parts, drawing.dxf_outputs);
    }
    drawingDetailEl.innerHTML = parts.join("");
    // Each DXF only shows the section for its own role: a GAD DXF shows the
    // GAD-vs-cutting-sheet validation it took part in, a cutting/baffle DXF
    // shows its own full-vs-segmental overlay (above) -- not both.
    if (isGad) {
      appendGadDxfValidations(drawingDetailEl, drawing.gad_dxf_validations, "GAD vs DXF validation");
    }
    return;
  }

  renderDrawingExtraction(parts, drawing.extraction);

  if (drawing.type_label === "SUB") {
    parts.push(`<h4>Comparison result</h4>`);
    if (!drawing.comparison.length) {
      parts.push(`<p class="empty-note">No comparison findings stored for this drawing.</p>`);
    } else {
      const gadName = drawing.gad_drawing ? drawing.gad_drawing.title : "Unknown GAD";
      parts.push(
        `<div class="compared-files">` +
          `<span class="compared-file-tag drawing-type-gad">GAD</span> ${esc(gadName)}` +
          `<span class="compared-vs">vs</span>` +
          `<span class="compared-file-tag drawing-type-sub">SUB</span> ${esc(drawing.title)}` +
        `</div>`
      );
      parts.push(findingsTableHtml(drawing.comparison));
    }
    renderJobCardComparisonGroups(parts, drawing.jobcard_comparisons, "SUB", drawing.title);
  }

  drawingDetailEl.innerHTML = parts.join("");
}
