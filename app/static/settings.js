const ui = {
  state: document.querySelector("#system-state"),
  form: document.querySelector("#settings-form"),
  source: document.querySelector("#source-folder"),
  extensions: document.querySelector("#program-extensions"),
  unit: document.querySelector("#weight-unit"),
  poolSlotCount: document.querySelector("#pool-slot-count"),
  poolLocationGrid: document.querySelector("#pool-location-grid"),
  onDeckLocationFields: document.querySelector("#on-deck-location-fields"),
  drippingLocationFields: document.querySelector("#dripping-location-fields"),
  debugMenuEnabled: document.querySelector("#debug-menu-enabled"),
  manualIoControlEnabled: document.querySelector("#manual-io-control-enabled"),
  robotConnectionMode: document.querySelector("#robot-connection-mode"),
  robotHost: document.querySelector("#robot-host"),
  robotPort: document.querySelector("#robot-port"),
  robotPollHz: document.querySelector("#robot-poll-hz"),
  robotTimeoutSeconds: document.querySelector("#robot-timeout-seconds"),
  cncTelemetryEnabled: document.querySelector("#cnc-telemetry-enabled"),
  cncHost: document.querySelector("#cnc-host"),
  cncSshPort: document.querySelector("#cnc-ssh-port"),
  cncSshUsername: document.querySelector("#cnc-ssh-username"),
  cncSshPassword: document.querySelector("#cnc-ssh-password"),
  cncTimeoutSeconds: document.querySelector("#cnc-timeout-seconds"),
  testCncTelemetry: document.querySelector("#test-cnc-telemetry"),
  cncTelemetryStatus: document.querySelector("#cnc-telemetry-status"),
  debugProgramButtonCount: document.querySelector("#debug-program-button-count"),
  robotFileAccessEnabled: document.querySelector("#robot-file-access-enabled"),
  robotFileHost: document.querySelector("#robot-file-host"),
  robotFilePort: document.querySelector("#robot-file-port"),
  robotFileUsername: document.querySelector("#robot-file-username"),
  robotFilePassword: document.querySelector("#robot-file-password"),
  robotFileDirectory: document.querySelector("#robot-file-directory"),
  robotProgramExtensions: document.querySelector("#robot-program-extensions"),
  robotProgramsFilterEnabled: document.querySelector("#robot-programs-filter-enabled"),
  robotProgramsPageEnabled: document.querySelector("#robot-programs-page-enabled"),
  robotEditorCommand: document.querySelector("#robot-editor-command"),
  millFileDirectory: document.querySelector("#mill-file-directory"),
  millProgramExtensions: document.querySelector("#mill-program-extensions"),
  millProgramsFilterEnabled: document.querySelector("#mill-programs-filter-enabled"),
  millProgramsPageEnabled: document.querySelector("#mill-programs-page-enabled"),
  millEditorCommand: document.querySelector("#mill-editor-command"),
  fusionToolLibraryUpload: document.querySelector("#fusion-tool-library-upload"),
  fusionToolLibraryList: document.querySelector("#fusion-tool-library-list"),
  openRobotDirectory: document.querySelector("#open-robot-directory"),
  robotFileAccessStatus: document.querySelector("#robot-file-access-status"),
  robotDirectoryModal: document.querySelector("#robot-directory-modal"),
  robotDirectoryPath: document.querySelector("#robot-directory-path"),
  robotDirectorySummary: document.querySelector("#robot-directory-summary"),
  robotDirectoryFiles: document.querySelector("#robot-directory-files"),
  robotDirectoryClose: document.querySelector("#robot-directory-close"),
  robotConnectionHelp: document.querySelector("#robot-connection-help"),
  appVersion: document.querySelector("#app-version"),
  relaunchSystem: document.querySelector("#relaunch-system"),
  relaunchStatus: document.querySelector("#relaunch-status"),
  unsavedModal: document.querySelector("#unsaved-modal"),
  unsavedCancel: document.querySelector("#unsaved-cancel"),
  unsavedDiscard: document.querySelector("#unsaved-discard"),
  unsavedSave: document.querySelector("#unsaved-save"),
  toast: document.querySelector("#toast"),
};

let board = null;
let healthVersion = "unknown";
let healthProcessId = null;
let healthStartedAt = "";
let savedSettingsSignature = "";
let isDirty = false;
let isLoadingSettings = false;
let allowNavigation = false;
let pendingNavigation = null;
let suppressNextPopstatePrompt = false;

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const response = await fetch(url, {
    headers,
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

function setDirtyState(nextDirty) {
  isDirty = Boolean(nextDirty);
  document.title = `${isDirty ? "* " : ""}Settings | Mongo Production System`;
}

function setSystemState() {
  const revision = board ? `rev ${board.revision}` : "rev ?";
  ui.state.classList.add("online");
  const processLabel = healthProcessId ? `pid ${healthProcessId}` : "pid ?";
  ui.state.lastChild.textContent = ` Online | v${healthVersion} | ${revision} | ${processLabel}`;
  ui.appVersion.textContent = `Version ${healthVersion}`;
}

function setRelaunchStatus(message, kind = "working") {
  ui.relaunchStatus.textContent = message;
  ui.relaunchStatus.className = `relaunch-status ${kind}`;
}

function syncRobotModeUi() {
  const isPhysical = ui.robotConnectionMode.value === "physical";
  ui.robotConnectionHelp.textContent = isPhysical
    ? "Physical mode reads live robot state. Unlock manual I/O control below to allow output LEDs to write through the robot Modbus server."
    : "Simulated mode uses internal robot state. Unlock manual I/O control to manually toggle digital inputs and outputs on the Debugging page.";
}

async function loadHealth() {
  const health = await api("/api/health");
  healthVersion = health.version || "unknown";
  healthProcessId = health.process_id || null;
  healthStartedAt = health.started_at || "";
  return health;
}

function fieldNumber(input, fallback) {
  if (input.value.trim() === "") return fallback;
  return Number(input.value);
}

function programExtensions() {
  const values = ui.extensions.value.split(",").map(value => value.trim()).filter(Boolean);
  return values.length ? values : board.settings.program_extensions;
}

function robotProgramExtensions() {
  const values = ui.robotProgramExtensions.value.split(",").map(value => value.trim()).filter(Boolean);
  return values.length ? values : board.settings.robot_program_extensions;
}

function millProgramExtensions() {
  const values = ui.millProgramExtensions.value.split(",").map(value => value.trim()).filter(Boolean);
  return values.length ? values : board.settings.mill_program_extensions;
}

function locationInput(axis, value, scope, slot = "") {
  const label = axis.replace("_mm", "").toUpperCase();
  return `<label>${label} (mm)<input type="number" step="0.001" data-location-scope="${scope}" data-location-slot="${slot}" data-location-axis="${axis}" value="${Number(value || 0)}"></label>`;
}

function bindLocationInputs(container) {
  for (const input of container.querySelectorAll("input")) {
    input.addEventListener("input", () => { if (!isLoadingSettings) refreshDirtyState(); });
  }
}

function readLocation(scope, slot = "") {
  const values = {};
  for (const axis of ["x_mm", "y_mm", "z_mm"]) {
    const input = ui.form.querySelector(`[data-location-scope="${scope}"][data-location-slot="${slot}"][data-location-axis="${axis}"]`);
    values[axis] = Number(input?.value || 0);
  }
  return values;
}

function poolLocationsDraft() {
  const count = fieldNumber(ui.poolSlotCount, board.settings.pool_slot_count);
  return Array.from({length: count}, (_, index) => ({slot: index + 1, ...readLocation("pool", String(index + 1))}));
}

function renderLocationFields() {
  const hasCurrentInputs = Boolean(ui.poolLocationGrid.querySelector("[data-location-scope='pool']"));
  const current = hasCurrentInputs ? new Map(poolLocationsDraft().map(location => [location.slot, location])) : new Map();
  const saved = new Map((board?.settings.pool_locations || []).map(location => [location.slot, location]));
  const count = fieldNumber(ui.poolSlotCount, board?.settings.pool_slot_count || 16);
  ui.poolLocationGrid.innerHTML = Array.from({length: count}, (_, index) => {
    const slot = index + 1;
    const location = current.get(slot) || saved.get(slot) || {};
    return `<fieldset class="location-fieldset"><legend>Pool ${String(slot).padStart(2, "0")}</legend><div class="location-axis-row">${locationInput("x_mm", location.x_mm, "pool", slot)}${locationInput("y_mm", location.y_mm, "pool", slot)}${locationInput("z_mm", location.z_mm, "pool", slot)}</div></fieldset>`;
  }).join("");
  const onDeck = ui.onDeckLocationFields.querySelector("input") ? readLocation("on_deck") : board?.settings.on_deck_location || {};
  const dripping = ui.drippingLocationFields.querySelector("input") ? readLocation("dripping") : board?.settings.dripping_location || {};
  ui.onDeckLocationFields.innerHTML = `<div class="location-axis-row">${locationInput("x_mm", onDeck.x_mm, "on_deck")}${locationInput("y_mm", onDeck.y_mm, "on_deck")}${locationInput("z_mm", onDeck.z_mm, "on_deck")}</div>`;
  ui.drippingLocationFields.innerHTML = `<div class="location-axis-row">${locationInput("x_mm", dripping.x_mm, "dripping")}${locationInput("y_mm", dripping.y_mm, "dripping")}${locationInput("z_mm", dripping.z_mm, "dripping")}</div>`;
  bindLocationInputs(ui.poolLocationGrid);
  bindLocationInputs(ui.onDeckLocationFields);
  bindLocationInputs(ui.drippingLocationFields);
}

function settingsDraft() {
  return {
    source_folder: ui.source.value,
    program_extensions: programExtensions(),
    weight_unit: ui.unit.value,
    pool_slot_count: fieldNumber(ui.poolSlotCount, board.settings.pool_slot_count),
    pool_locations: poolLocationsDraft(),
    on_deck_location: readLocation("on_deck"),
    dripping_location: readLocation("dripping"),
    debug_menu_enabled: ui.debugMenuEnabled.checked,
    manual_io_control_enabled: ui.manualIoControlEnabled.checked,
    robot_connection_mode: ui.robotConnectionMode.value,
    robot_host: ui.robotHost.value.trim(),
    robot_port: fieldNumber(ui.robotPort, board.settings.robot_port || 30004),
    robot_poll_hz: fieldNumber(ui.robotPollHz, board.settings.robot_poll_hz || 10),
    robot_timeout_seconds: fieldNumber(ui.robotTimeoutSeconds, board.settings.robot_timeout_seconds || 1.0),
    cnc_telemetry_enabled: ui.cncTelemetryEnabled.checked,
    cnc_host: ui.cncHost.value.trim(),
    cnc_ssh_port: fieldNumber(ui.cncSshPort, board.settings.cnc_ssh_port || 22),
    cnc_ssh_username: ui.cncSshUsername.value.trim() || "operator",
    cnc_ssh_password: ui.cncSshPassword.value,
    cnc_timeout_seconds: fieldNumber(ui.cncTimeoutSeconds, board.settings.cnc_timeout_seconds || 2),
    debug_program_button_count: fieldNumber(ui.debugProgramButtonCount, board.settings.debug_program_button_count || 4),
    robot_file_access_enabled: ui.robotFileAccessEnabled.checked,
    robot_file_host: ui.robotFileHost.value.trim(),
    robot_file_port: fieldNumber(ui.robotFilePort, board.settings.robot_file_port || 22),
    robot_file_username: ui.robotFileUsername.value.trim() || "root",
    robot_file_password: ui.robotFilePassword.value,
    robot_file_directory: ui.robotFileDirectory.value.trim() || "/programs",
    robot_program_extensions: robotProgramExtensions(),
    robot_programs_filter_enabled: ui.robotProgramsFilterEnabled.checked,
    robot_programs_page_enabled: ui.robotProgramsPageEnabled.checked,
    robot_editor_command: ui.robotEditorCommand.value.trim() || "code",
    mill_file_directory: ui.millFileDirectory.value.trim() || "/home/operator/gcode",
    mill_program_extensions: millProgramExtensions(),
    mill_programs_filter_enabled: ui.millProgramsFilterEnabled.checked,
    mill_programs_page_enabled: ui.millProgramsPageEnabled.checked,
    mill_editor_command: ui.millEditorCommand.value.trim() || "code",
  };
}

function hasUnsavedChanges() {
  return Boolean(board && savedSettingsSignature && isDirty);
}

function refreshDirtyState() {
  if (!board || !savedSettingsSignature) {
    setDirtyState(false);
    return;
  }
  setDirtyState(JSON.stringify(settingsDraft()) !== savedSettingsSignature);
}

async function loadSettings() {
  try {
    isLoadingSettings = true;
    await loadHealth();
    board = await api("/api/settings");
    ui.source.value = board.settings.source_folder;
    ui.extensions.value = board.settings.program_extensions.join(", ");
    ui.unit.value = board.settings.weight_unit;
    ui.poolSlotCount.value = board.settings.pool_slot_count;
    renderLocationFields();
    ui.debugMenuEnabled.checked = board.settings.debug_menu_enabled;
    ui.manualIoControlEnabled.checked = board.settings.manual_io_control_enabled;
    ui.robotConnectionMode.value = board.settings.robot_connection_mode;
    ui.robotHost.value = board.settings.robot_host || "";
    ui.robotPort.value = board.settings.robot_port;
    ui.robotPollHz.value = board.settings.robot_poll_hz;
    ui.robotTimeoutSeconds.value = board.settings.robot_timeout_seconds;
    ui.cncTelemetryEnabled.checked = board.settings.cnc_telemetry_enabled;
    ui.cncHost.value = board.settings.cnc_host || "";
    ui.cncSshPort.value = board.settings.cnc_ssh_port || 22;
    ui.cncSshUsername.value = board.settings.cnc_ssh_username || "operator";
    ui.cncSshPassword.value = board.settings.cnc_ssh_password || "";
    ui.cncTimeoutSeconds.value = board.settings.cnc_timeout_seconds || 2;
    ui.debugProgramButtonCount.value = board.settings.debug_program_button_count || 4;
    ui.robotFileAccessEnabled.checked = board.settings.robot_file_access_enabled;
    ui.robotFileHost.value = board.settings.robot_file_host || "";
    ui.robotFilePort.value = board.settings.robot_file_port || 22;
    ui.robotFileUsername.value = board.settings.robot_file_username || "root";
    ui.robotFilePassword.value = board.settings.robot_file_password;
    ui.robotFileDirectory.value = board.settings.robot_file_directory || "/programs";
    ui.robotProgramExtensions.value = board.settings.robot_program_extensions.join(", ");
    ui.robotProgramsFilterEnabled.checked = board.settings.robot_programs_filter_enabled;
    ui.robotProgramsPageEnabled.checked = board.settings.robot_programs_page_enabled;
    ui.robotEditorCommand.value = board.settings.robot_editor_command || "code";
    ui.millFileDirectory.value = board.settings.mill_file_directory || "/home/operator/gcode";
    ui.millProgramExtensions.value = board.settings.mill_program_extensions.join(", ");
    ui.millProgramsFilterEnabled.checked = board.settings.mill_programs_filter_enabled;
    ui.millProgramsPageEnabled.checked = board.settings.mill_programs_page_enabled;
    ui.millEditorCommand.value = board.settings.mill_editor_command || "code";
    renderFusionToolLibraries(board.settings.fusion_tool_libraries || []);
    document.querySelectorAll("[data-robot-programs-nav]").forEach(link => link.classList.toggle("hidden", !board.settings.robot_programs_page_enabled));
    document.querySelectorAll("[data-mill-programs-nav]").forEach(link => link.classList.toggle("hidden", !board.settings.mill_programs_page_enabled));
    savedSettingsSignature = JSON.stringify(settingsDraft());
    setDirtyState(false);
    syncRobotModeUi();
    setSystemState();
  } catch (error) {
    ui.state.lastChild.textContent = " Unavailable";
    showToast(error.message, "error");
  } finally {
    isLoadingSettings = false;
  }
}

async function saveSettings() {
  try {
    const result = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: board.revision,
        ...settingsDraft(),
      }),
    });
    board = result.board;
    ui.manualIoControlEnabled.checked = board.settings.manual_io_control_enabled;
    savedSettingsSignature = JSON.stringify(settingsDraft());
    setDirtyState(false);
    setSystemState();
    syncRobotModeUi();
    const cleared = result.cleared_assignments.length
      ? ` Cleared program assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    showToast(`Settings saved.${cleared}`);
    return true;
  } catch (error) {
    if (error.message.includes("another session")) await loadSettings();
    showToast(error.message, "error");
    return false;
  }
}

ui.form.addEventListener("submit", async event => {
  event.preventDefault();
  await saveSettings();
});

ui.robotConnectionMode.addEventListener("change", syncRobotModeUi);
ui.testCncTelemetry.addEventListener("click", async () => {
  const originalLabel = ui.testCncTelemetry.textContent;
  ui.testCncTelemetry.disabled = true;
  ui.testCncTelemetry.textContent = "Testing...";
  ui.cncTelemetryStatus.textContent = "Connecting to PathPilot...";
  try {
    const result = await api("/api/debug/cnc/test", {
      method: "POST",
      body: JSON.stringify({
        host: ui.cncHost.value.trim(),
        port: fieldNumber(ui.cncSshPort, 22),
        username: ui.cncSshUsername.value.trim() || "operator",
        password: ui.cncSshPassword.value,
        timeout_seconds: fieldNumber(ui.cncTimeoutSeconds, 2),
      }),
    });
    ui.cncTelemetryStatus.textContent = `${result.message} Program: ${result.program}.`;
  } catch (error) {
    ui.cncTelemetryStatus.textContent = error.message;
  } finally {
    ui.testCncTelemetry.disabled = false;
    ui.testCncTelemetry.textContent = originalLabel;
  }
});
ui.poolSlotCount.addEventListener("change", () => {
  if (!board) return;
  renderLocationFields();
  refreshDirtyState();
});

function renderFusionToolLibraries(libraries) {
  ui.fusionToolLibraryList.replaceChildren();
  if (!libraries.length) {
    ui.fusionToolLibraryList.textContent = "No uploaded Fusion tool libraries.";
    return;
  }
  for (const library of libraries) {
    const row = document.createElement("div");
    row.className = "managed-library-row";
    const name = document.createElement("span");
    name.textContent = library.name;
    const remove = document.createElement("button");
    remove.className = "button ghost";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.dataset.fusionLibraryPath = library.path;
    row.append(name, remove);
    ui.fusionToolLibraryList.append(row);
  }
}

ui.fusionToolLibraryUpload.addEventListener("change", async () => {
  const files = [...ui.fusionToolLibraryUpload.files];
  if (!files.length) return;
  const body = new FormData();
  for (const file of files) body.append("files", file);
  try {
    await api("/api/tool-libraries/upload", {method: "POST", body});
    showToast(`${files.length} Fusion tool librar${files.length === 1 ? "y" : "ies"} uploaded.`);
    await loadSettings();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    ui.fusionToolLibraryUpload.value = "";
  }
});

ui.fusionToolLibraryList.addEventListener("click", async event => {
  const button = event.target.closest("[data-fusion-library-path]");
  if (!button || !window.confirm("Remove this uploaded Fusion tool library?")) return;
  try {
    await api(`/api/tool-libraries?path=${encodeURIComponent(button.dataset.fusionLibraryPath)}`, {method: "DELETE"});
    showToast("Fusion tool library removed.");
    await loadSettings();
  } catch (error) {
    showToast(error.message, "error");
  }
});

for (const field of ui.form.querySelectorAll("input, select, textarea")) {
  const eventName = field.type === "checkbox" || field.tagName === "SELECT" ? "change" : "input";
  field.addEventListener(eventName, () => {
    if (isLoadingSettings) return;
    if (field === ui.robotConnectionMode) syncRobotModeUi();
    refreshDirtyState();
  });
}

function closeUnsavedModal() {
  pendingNavigation = null;
  ui.unsavedModal.classList.add("hidden");
}

function showRobotDirectory(files) {
  const directory = board.settings.robot_file_directory;
  ui.robotDirectoryPath.textContent = directory;
  ui.robotDirectorySummary.textContent = files.length
    ? `${files.length} ${files.length === 1 ? "file" : "files"} read from the controller.`
    : "Connected successfully. This directory is empty.";
  ui.robotDirectoryFiles.replaceChildren();
  for (const file of files) {
    const item = document.createElement("li");
    item.textContent = file;
    ui.robotDirectoryFiles.append(item);
  }
  ui.robotDirectoryModal.classList.remove("hidden");
}

function closeRobotDirectory() {
  ui.robotDirectoryModal.classList.add("hidden");
}

function openUnsavedModal(navigate) {
  pendingNavigation = navigate;
  ui.unsavedModal.classList.remove("hidden");
}

function continueNavigation() {
  const navigate = pendingNavigation;
  closeUnsavedModal();
  if (!navigate) return;
  allowNavigation = true;
  navigate();
}

document.addEventListener("click", async event => {
  const target = event.target instanceof Element ? event.target : event.target?.parentElement;
  const link = target?.closest("a[href]");
  if (!link || !hasUnsavedChanges() || event.defaultPrevented) return;
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

  event.preventDefault();
  openUnsavedModal(() => window.location.assign(link.href));
}, true);

window.addEventListener("beforeunload", event => {
  if (allowNavigation || !hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
});

window.addEventListener("popstate", event => {
  if (suppressNextPopstatePrompt) {
    suppressNextPopstatePrompt = false;
    return;
  }
  if (allowNavigation || !hasUnsavedChanges()) return;
  history.pushState({settingsGuard: true}, "", window.location.href);
  openUnsavedModal(() => {
    suppressNextPopstatePrompt = true;
    history.back();
  });
});

history.replaceState({settingsGuard: true}, "", window.location.href);

ui.unsavedCancel.addEventListener("click", () => {
  closeUnsavedModal();
});

ui.unsavedDiscard.addEventListener("click", () => {
  continueNavigation();
});

ui.unsavedSave.addEventListener("click", async () => {
  ui.unsavedSave.disabled = true;
  const saved = await saveSettings();
  ui.unsavedSave.disabled = false;
  if (!saved) return;
  continueNavigation();
});

ui.relaunchSystem.addEventListener("click", async () => {
  const button = ui.relaunchSystem;
  const startingProcessId = healthProcessId;
  const startingStartedAt = healthStartedAt;
  button.disabled = true;
  button.textContent = "Relaunching";
  setRelaunchStatus("Step 1 of 3: requesting backend restart...");
  try {
    await api("/api/system/relaunch", {method: "POST"});
    setRelaunchStatus("Step 2 of 3: backend restart requested. Waiting for the server to cycle...");
    showToast("Backend relaunch requested. This page will refresh when the server is current.");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Close and relaunch";
    setRelaunchStatus(`Relaunch failed: ${error.message}`, "error");
    showToast(error.message, "error");
    return;
  }

  const deadline = Date.now() + 45000;
  let sawOffline = false;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`/api/health?t=${Date.now()}`, {cache: "no-store"});
      if (response.ok) {
        const health = await response.json();
        healthVersion = health.version || "unknown";
        healthProcessId = health.process_id || null;
        healthStartedAt = health.started_at || "";
        const processChanged = healthProcessId && healthProcessId !== startingProcessId;
        const startChanged = healthStartedAt && healthStartedAt !== startingStartedAt;
        if (sawOffline || processChanged || startChanged) {
          setRelaunchStatus("Step 3 of 3: backend is back online. Refreshing the UI...", "success");
          window.location.reload();
          return;
        }
        setRelaunchStatus("Step 2 of 3: restart is still in progress...");
      }
    } catch {
      sawOffline = true;
      setRelaunchStatus("Step 2 of 3: backend is offline during restart. Waiting for it to return...");
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }

  button.disabled = false;
  button.textContent = "Close and relaunch";
  setRelaunchStatus("Relaunch timed out. The backend may still be restarting; refresh this page once in a few seconds.", "error");
  showToast("Relaunch timed out. If the backend did restart, refresh this page once.", "error");
});

ui.openRobotDirectory.addEventListener("click", async () => {
  if (hasUnsavedChanges()) {
    showToast("Save the Robot file access settings before opening the controller directory.", "error");
    return;
  }
  if (!board.settings.robot_file_access_enabled) {
    showToast("Enable SFTP file browser and save settings before opening the controller directory.", "error");
    return;
  }

  const button = ui.openRobotDirectory;
  button.disabled = true;
  button.textContent = "Opening directory...";
  ui.robotFileAccessStatus.textContent = "Connecting to the controller...";
  try {
    const result = await api("/api/debug/programs/files?include_all=true", {cache: "no-store"});
    const count = result.files.length;
    const message = `Connected. ${board.settings.robot_file_directory} opened; ${count} ${count === 1 ? "file" : "files"} found.`;
    ui.robotFileAccessStatus.textContent = message;
    showRobotDirectory(result.files);
    showToast(message);
  } catch (error) {
    ui.robotFileAccessStatus.textContent = `Could not open directory: ${error.message}`;
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Open robot directory";
  }
});

ui.robotDirectoryClose.addEventListener("click", closeRobotDirectory);
ui.robotDirectoryModal.addEventListener("click", event => {
  if (event.target === ui.robotDirectoryModal) closeRobotDirectory();
});

loadSettings();
