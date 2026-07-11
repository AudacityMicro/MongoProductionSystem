const ui = {
  state: document.querySelector("#system-state"),
  form: document.querySelector("#settings-form"),
  source: document.querySelector("#source-folder"),
  extensions: document.querySelector("#program-extensions"),
  unit: document.querySelector("#weight-unit"),
  poolSlotCount: document.querySelector("#pool-slot-count"),
  debugMenuEnabled: document.querySelector("#debug-menu-enabled"),
  manualIoControlEnabled: document.querySelector("#manual-io-control-enabled"),
  robotConnectionMode: document.querySelector("#robot-connection-mode"),
  robotHost: document.querySelector("#robot-host"),
  robotPort: document.querySelector("#robot-port"),
  robotPollHz: document.querySelector("#robot-poll-hz"),
  robotTimeoutSeconds: document.querySelector("#robot-timeout-seconds"),
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

function settingsDraft() {
  return {
    source_folder: ui.source.value,
    program_extensions: programExtensions(),
    weight_unit: ui.unit.value,
    pool_slot_count: fieldNumber(ui.poolSlotCount, board.settings.pool_slot_count),
    debug_menu_enabled: ui.debugMenuEnabled.checked,
    manual_io_control_enabled: ui.manualIoControlEnabled.checked,
    robot_connection_mode: ui.robotConnectionMode.value,
    robot_host: ui.robotHost.value.trim(),
    robot_port: fieldNumber(ui.robotPort, board.settings.robot_port || 30004),
    robot_poll_hz: fieldNumber(ui.robotPollHz, board.settings.robot_poll_hz || 10),
    robot_timeout_seconds: fieldNumber(ui.robotTimeoutSeconds, board.settings.robot_timeout_seconds || 1.0),
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
    ui.debugMenuEnabled.checked = board.settings.debug_menu_enabled;
    ui.manualIoControlEnabled.checked = board.settings.manual_io_control_enabled;
    ui.robotConnectionMode.value = board.settings.robot_connection_mode;
    ui.robotHost.value = board.settings.robot_host || "";
    ui.robotPort.value = board.settings.robot_port;
    ui.robotPollHz.value = board.settings.robot_poll_hz;
    ui.robotTimeoutSeconds.value = board.settings.robot_timeout_seconds;
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

loadSettings();
