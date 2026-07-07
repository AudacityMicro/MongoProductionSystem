const ui = {
  state: document.querySelector("#system-state"),
  form: document.querySelector("#settings-form"),
  source: document.querySelector("#source-folder"),
  extensions: document.querySelector("#program-extensions"),
  unit: document.querySelector("#weight-unit"),
  poolSlotCount: document.querySelector("#pool-slot-count"),
  debugMenuEnabled: document.querySelector("#debug-menu-enabled"),
  robotConnectionMode: document.querySelector("#robot-connection-mode"),
  robotHost: document.querySelector("#robot-host"),
  robotPort: document.querySelector("#robot-port"),
  robotPollHz: document.querySelector("#robot-poll-hz"),
  robotTimeoutSeconds: document.querySelector("#robot-timeout-seconds"),
  robotConnectionHelp: document.querySelector("#robot-connection-help"),
  appVersion: document.querySelector("#app-version"),
  relaunchSystem: document.querySelector("#relaunch-system"),
  toast: document.querySelector("#toast"),
};

let board = null;
let healthVersion = "unknown";

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

function setSystemState() {
  const revision = board ? `rev ${board.revision}` : "rev ?";
  ui.state.classList.add("online");
  ui.state.lastChild.textContent = ` Online | v${healthVersion} | ${revision}`;
  ui.appVersion.textContent = `Version ${healthVersion}`;
}

function syncRobotModeUi() {
  const isPhysical = ui.robotConnectionMode.value === "physical";
  ui.robotConnectionHelp.textContent = isPhysical
    ? "Physical mode reads only live RTDE data from the configured robot. Digital toggles are disabled."
    : "Simulated mode uses internal robot state and lets you manually toggle digital inputs and outputs on the Debugging page.";
}

async function loadHealth() {
  const health = await api("/api/health");
  healthVersion = health.version || "unknown";
  return health;
}

async function loadSettings() {
  try {
    await loadHealth();
    board = await api("/api/settings");
    ui.source.value = board.settings.source_folder;
    ui.extensions.value = board.settings.program_extensions.join(", ");
    ui.unit.value = board.settings.weight_unit;
    ui.poolSlotCount.value = board.settings.pool_slot_count;
    ui.debugMenuEnabled.checked = board.settings.debug_menu_enabled;
    ui.robotConnectionMode.value = board.settings.robot_connection_mode;
    ui.robotHost.value = board.settings.robot_host || "";
    ui.robotPort.value = board.settings.robot_port;
    ui.robotPollHz.value = board.settings.robot_poll_hz;
    ui.robotTimeoutSeconds.value = board.settings.robot_timeout_seconds;
    syncRobotModeUi();
    setSystemState();
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
        robot_connection_mode: ui.robotConnectionMode.value,
        robot_host: ui.robotHost.value.trim(),
        robot_port: Number(ui.robotPort.value),
        robot_poll_hz: Number(ui.robotPollHz.value),
        robot_timeout_seconds: Number(ui.robotTimeoutSeconds.value),
      }),
    });
    board = result.board;
    setSystemState();
    syncRobotModeUi();
    const cleared = result.cleared_assignments.length
      ? ` Cleared program assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    showToast(`Settings saved.${cleared}`);
  } catch (error) {
    if (error.message.includes("another session")) await loadSettings();
    showToast(error.message, "error");
  }
});

ui.robotConnectionMode.addEventListener("change", syncRobotModeUi);

ui.relaunchSystem.addEventListener("click", async () => {
  const button = ui.relaunchSystem;
  button.disabled = true;
  button.textContent = "Relaunching...";
  try {
    await api("/api/system/relaunch", {method: "POST"});
    showToast("Backend relaunch queued. Waiting for the current version to come back...");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Close and relaunch";
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
        if (sawOffline) {
          window.location.reload();
          return;
        }
      }
    } catch {
      sawOffline = true;
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000));
  }

  button.disabled = false;
  button.textContent = "Close and relaunch";
  showToast("Relaunch timed out. If the backend did restart, refresh this page once.", "error");
});

loadSettings();
