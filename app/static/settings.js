const ui = {
  state: document.querySelector("#system-state"),
  form: document.querySelector("#settings-form"),
  source: document.querySelector("#source-folder"),
  extensions: document.querySelector("#program-extensions"),
  unit: document.querySelector("#weight-unit"),
  poolSlotCount: document.querySelector("#pool-slot-count"),
  debugMenuEnabled: document.querySelector("#debug-menu-enabled"),
  toast: document.querySelector("#toast"),
};

let board = null;

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed with status ${response.status}`);
  return data;
}

function showToast(message, kind = "success") {
  ui.toast.textContent = message;
  ui.toast.className = `toast ${kind}`;
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => ui.toast.classList.add("hidden"), 4500);
}

async function loadSettings() {
  try {
    board = await api("/api/settings");
    ui.source.value = board.settings.source_folder;
    ui.extensions.value = board.settings.program_extensions.join(", ");
    ui.unit.value = board.settings.weight_unit;
    ui.poolSlotCount.value = board.settings.pool_slot_count;
    ui.debugMenuEnabled.checked = board.settings.debug_menu_enabled;
    ui.state.classList.add("online");
    ui.state.lastChild.textContent = ` Online · rev ${board.revision}`;
  } catch (error) {
    ui.state.lastChild.textContent = " Unavailable";
    showToast(error.message, "error");
  }
}

ui.form.addEventListener("submit", async event => {
  event.preventDefault();
  if (!ui.form.reportValidity()) return;
  try {
    const result = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: board.revision,
        source_folder: ui.source.value,
        program_extensions: ui.extensions.value.split(",").map(value => value.trim()).filter(Boolean),
        weight_unit: ui.unit.value,
        pool_slot_count: Number(ui.poolSlotCount.value),
        debug_menu_enabled: ui.debugMenuEnabled.checked,
      }),
    });
    board = result.board;
    ui.state.lastChild.textContent = ` Online · rev ${board.revision}`;
    const cleared = result.cleared_assignments.length
      ? ` Cleared program assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    showToast(`Settings saved.${cleared}`);
  } catch (error) {
    if (error.message.includes("another session")) await loadSettings();
    showToast(error.message, "error");
  }
});

loadSettings();
