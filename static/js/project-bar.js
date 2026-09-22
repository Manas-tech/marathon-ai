// Shared "active project" selector used by both the upload form (app.js)
// and the browse tab (browse.js). Selecting a project here is what makes
// uploads and browsing scoped to that project.
const PROJECT_API_BASE = "/api/v1";

window.ActiveProject = { id: null, name: null };

const projectSelect = document.getElementById("project-select");
const newProjectBtn = document.getElementById("new-project-btn");
const projectBarMsg = document.getElementById("project-bar-msg");

function notifyActiveProjectChanged() {
  window.dispatchEvent(new CustomEvent("active-project-changed", { detail: window.ActiveProject }));
}

function applySelection(id, name) {
  window.ActiveProject = { id, name };
  notifyActiveProjectChanged();
}

async function loadProjectOptions(selectId) {
  try {
    const res = await fetch(`${PROJECT_API_BASE}/projects`);
    if (!res.ok) throw new Error(`Failed to load projects (HTTP ${res.status})`);
    const projects = await res.json();

    projectSelect.innerHTML = "";
    if (!projects.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No projects yet — create one";
      projectSelect.appendChild(opt);
      applySelection(null, null);
      return;
    }

    for (const p of projects) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `${p.name}${p.so_no ? " (SO " + p.so_no + ")" : ""} — ${p.drawing_count} drawing(s)`;
      projectSelect.appendChild(opt);
    }

    const toSelect = selectId && projects.some((p) => p.id === selectId) ? selectId : projects[0].id;
    projectSelect.value = String(toSelect);
    const picked = projects.find((p) => p.id === toSelect);
    applySelection(picked.id, picked.name);
  } catch (err) {
    projectSelect.innerHTML = `<option value="">Failed to load</option>`;
    projectBarMsg.textContent = err.message;
  }
}

projectSelect.addEventListener("change", () => {
  const id = Number(projectSelect.value);
  const label = projectSelect.selectedOptions[0]?.textContent || null;
  applySelection(id || null, label);
});

newProjectBtn.addEventListener("click", async () => {
  const name = prompt("New project name:");
  if (!name || !name.trim()) return;
  const so_no = prompt("Sales order number (optional):") || null;

  projectBarMsg.textContent = "Creating...";
  try {
    const res = await fetch(`${PROJECT_API_BASE}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), so_no }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Failed to create project (HTTP ${res.status})`);
    }
    const project = await res.json();
    projectBarMsg.textContent = `Created "${project.name}".`;
    await loadProjectOptions(project.id);
  } catch (err) {
    projectBarMsg.textContent = err.message;
  }
});

loadProjectOptions();
