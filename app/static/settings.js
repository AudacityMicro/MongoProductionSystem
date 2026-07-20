const ui = {
  state: document.querySelector("#system-state"),
  form: document.querySelector("#settings-form"),
  source: document.querySelector("#source-folder"),
  extensions: document.querySelector("#program-extensions"),
  unit: document.querySelector("#weight-unit"),
  poolSlotCount: document.querySelector("#pool-slot-count"),
  poolLocationGrid: document.querySelector("#pool-location-grid"),
  onDeckEnabled: document.querySelector("#on-deck-enabled"),
  drippingEnabled: document.querySelector("#dripping-enabled"),
  runModeSafetyConfirm: document.querySelector("#run-mode-safety-confirm"),
  onDeckLocationFields: document.querySelector("#on-deck-location-fields"),
  drippingLocationFields: document.querySelector("#dripping-location-fields"),
  robotMillLoadUnloadFields: document.querySelector("#robot-mill-load-unload-fields"),
  robotMillPreEntryFields: document.querySelector("#robot-mill-pre-entry-fields"),
  robotMillSafeEntryExitFields: document.querySelector("#robot-mill-safe-entry-exit-fields"),
  millLoadUnloadG53Fields: document.querySelector("#mill-load-unload-g53-fields"),
  buildMillLoadPositionProgram: document.querySelector("#build-mill-load-position-program"),
  millLoadPositionProgramStatus: document.querySelector("#mill-load-position-program-status"),
  debugMenuEnabled: document.querySelector("#debug-menu-enabled"),
  manualIoControlEnabled: document.querySelector("#manual-io-control-enabled"),
  robotConnectionMode: document.querySelector("#robot-connection-mode"),
  robotHost: document.querySelector("#robot-host"),
  robotPort: document.querySelector("#robot-port"),
  robotPollHz: document.querySelector("#robot-poll-hz"),
  robotTimeoutSeconds: document.querySelector("#robot-timeout-seconds"),
  robotSupervisorEnabled: document.querySelector("#robot-supervisor-enabled"),
  robotSupervisorHostname: document.querySelector("#robot-supervisor-hostname"),
  robotSupervisorListenHost: document.querySelector("#robot-supervisor-listen-host"),
  robotSupervisorPort: document.querySelector("#robot-supervisor-port"),
  robotSupervisorHeartbeatSeconds: document.querySelector("#robot-supervisor-heartbeat-seconds"),
  robotSupervisorTelemetryHz: document.querySelector("#robot-supervisor-telemetry-hz"),
  robotSupervisorReconnectLimitSeconds: document.querySelector("#robot-supervisor-reconnect-limit-seconds"),
  robotSupervisorFallback: document.querySelector("#robot-supervisor-fallback"),
  bootstrapRobotSupervisor: document.querySelector("#bootstrap-robot-supervisor"),
  installSupervisorFirewall: document.querySelector("#install-supervisor-firewall"),
  robotSupervisorStatus: document.querySelector("#robot-supervisor-status"),
  cncTelemetryEnabled: document.querySelector("#cnc-telemetry-enabled"),
  cncHost: document.querySelector("#cnc-host"),
  cncSshPort: document.querySelector("#cnc-ssh-port"),
  cncSshUsername: document.querySelector("#cnc-ssh-username"),
  cncSshPassword: document.querySelector("#cnc-ssh-password"),
  cncTimeoutSeconds: document.querySelector("#cnc-timeout-seconds"),
  cncRequireAAxisHomed: document.querySelector("#cnc-require-a-axis-homed"),
  testCncTelemetry: document.querySelector("#test-cnc-telemetry"),
  cncTelemetryStatus: document.querySelector("#cnc-telemetry-status"),
  debugProgramButtonCount: document.querySelector("#debug-program-button-count"),
  debugMillProgramButtonCount: document.querySelector("#debug-mill-program-button-count"),
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
  millResultsArchivingEnabled: document.querySelector("#mill-results-archiving-enabled"),
  millResultsSourcePath: document.querySelector("#mill-results-source-path"),
  millResultsArchiveDirectory: document.querySelector("#mill-results-archive-directory"),
  palletMotionEnabled: document.querySelector("#pallet-motion-enabled"),
  palletMotionTimeoutSeconds: document.querySelector("#pallet-motion-timeout-seconds"),
  palletMotionApproachYClearance: document.querySelector("#pallet-motion-approach-y-clearance"),
  palletMotionMillApproachXClearance: document.querySelector("#pallet-motion-mill-approach-x-clearance"),
  palletMotionLiftZClearance: document.querySelector("#pallet-motion-lift-z-clearance"),
  palletMotionMillLiftZClearance: document.querySelector("#pallet-motion-mill-lift-z-clearance"),
  palletMotionMaxTravelSpeed: document.querySelector("#pallet-motion-max-travel-speed"),
  palletMotionPickupSpeed: document.querySelector("#pallet-motion-pickup-speed"),
  palletMotionGripOutput: document.querySelector("#pallet-motion-grip-output"),
  palletMotionGripClosedValue: document.querySelector("#pallet-motion-grip-closed-value"),
  millDoorOpenOutput: document.querySelector("#mill-door-open-output"),
  millDoorOpenState: document.querySelector("#mill-door-open-state"),
  millDoorOpenPulse: document.querySelector("#mill-door-open-pulse"),
  millDoorCloseOutput: document.querySelector("#mill-door-close-output"),
  millDoorCloseState: document.querySelector("#mill-door-close-state"),
  millDoorClosePulse: document.querySelector("#mill-door-close-pulse"),
  erowaUnlockOutput: document.querySelector("#erowa-unlock-output"),
  erowaUnlockState: document.querySelector("#erowa-unlock-state"),
  erowaUnlockPulse: document.querySelector("#erowa-unlock-pulse"),
  erowaLockOutput: document.querySelector("#erowa-lock-output"),
  erowaLockState: document.querySelector("#erowa-lock-state"),
  erowaLockPulse: document.querySelector("#erowa-lock-pulse"),
  millActuationWaitSeconds: document.querySelector("#mill-actuation-wait-seconds"),
  palletMotionRx: document.querySelector("#pallet-motion-rx"),
  palletMotionRy: document.querySelector("#pallet-motion-ry"),
  palletMotionRz: document.querySelector("#pallet-motion-rz"),
  palletMotionSafePreFields: document.querySelector("#pallet-motion-safe-pre-fields"),
  intermediateSafePoseName: document.querySelector("#intermediate-safe-pose-name"),
  intermediateSafePoseJoints: Array.from({length: 6}, (_, index) => document.querySelector(`#intermediate-safe-pose-j${index}`)),
  intermediateSafePoseSlots: document.querySelector("#intermediate-safe-pose-slots"),
  addIntermediateSafePose: document.querySelector("#add-intermediate-safe-pose"),
  intermediateSafePoseList: document.querySelector("#intermediate-safe-pose-list"),
  rebuildMotionScripts: document.querySelector("#rebuild-motion-scripts"),
  generatedMotionProgramList: document.querySelector("#generated-motion-program-list"),
  motionProgramFileStatus: document.querySelector("#motion-program-file-status"),
  newWorkholding: document.querySelector("#new-workholding"),
  addWorkholding: document.querySelector("#add-workholding"),
  workholdingLibraryList: document.querySelector("#workholding-library-list"),
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
  poseCaptureModal: document.querySelector("#pose-capture-modal"),
  poseCaptureMessage: document.querySelector("#pose-capture-message"),
  poseCaptureCancel: document.querySelector("#pose-capture-cancel"),
  poseCaptureConfirm: document.querySelector("#pose-capture-confirm"),
  unsavedModal: document.querySelector("#unsaved-modal"),
  unsavedCancel: document.querySelector("#unsaved-cancel"),
  unsavedDiscard: document.querySelector("#unsaved-discard"),
  unsavedSave: document.querySelector("#unsaved-save"),
  scriptRebuildModal: document.querySelector("#script-rebuild-modal"),
  scriptRebuildLater: document.querySelector("#script-rebuild-later"),
  scriptRebuildNow: document.querySelector("#script-rebuild-now"),
  toast: document.querySelector("#toast"),
};

let board = null;
let healthVersion = "unknown";
let healthProcessId = null;
let healthStartedAt = "";
let savedSettingsSignature = "";
let savedSettingsDraft = null;
let intermediateSafePoses = [];
let isDirty = false;
let isLoadingSettings = false;
let allowNavigation = false;
let pendingNavigation = null;
let afterScriptRebuildPrompt = null;
let suppressNextPopstatePrompt = false;
let workholdingLibrary = [];
let pendingPoseCaptureButton = null;

function organizeSettingsPage() {
  const groups = [
    {
      id: "settings-general",
      eyebrow: "General",
      title: "Production and display",
      description: "Shared scheduling, pallet, workholding, and operator display settings.",
      panels: ["Display", "Pallet pool", "Workflow stations", "Workholding library"],
      openPanels: ["Display"],
      widePanels: ["Workholding library"],
    },
    {
      id: "settings-robot",
      eyebrow: "Robot",
      title: "Mongo robot",
      description: "Connection, controller files, physical locations, and generated pallet motion.",
      panels: ["Robot connection", "Robot file access", "Robot Programs page", "Robot pallet locations", "Robot pallet motion"],
      openPanels: ["Robot connection"],
      widePanels: ["Robot pallet locations", "Robot pallet motion"],
    },
    {
      id: "settings-mill",
      eyebrow: "Mill",
      title: "Tormach and PathPilot",
      description: "CNC telemetry, programs, machine loading coordinates, and tooling sources.",
      panels: ["CNC telemetry", "Mill programs", "Mill Programs page", "Mill loading position", "Fusion 360 tools"],
      openPanels: ["CNC telemetry"],
      widePanels: ["CNC telemetry", "Mill Programs page"],
    },
    {
      id: "settings-system",
      eyebrow: "System",
      title: "Application and diagnostics",
      description: "Diagnostic access, test controls, saving, and backend lifecycle actions.",
      panels: ["Debugging controls"],
      openPanels: ["Debugging controls"],
      widePanels: ["Debugging controls"],
    },
  ];
  const actions = ui.form.querySelector(".settings-actions");
  const panels = new Map([...ui.form.querySelectorAll(":scope > .settings-panel")].map(panel => [panel.querySelector("h2")?.textContent.trim(), panel]));
  const debugFields = panels.get("Debugging controls")?.querySelector(".panel-fields");
  [ui.debugMenuEnabled, ui.manualIoControlEnabled].forEach(input => {
    const control = input?.closest(".checkbox-control");
    if (control && debugFields) debugFields.prepend(control);
  });

  function setPanelExpanded(panel, expanded) {
    const fields = panel.querySelector(".panel-fields");
    const toggle = panel.querySelector(".settings-panel-toggle");
    panel.classList.toggle("is-collapsed", !expanded);
    fields.hidden = !expanded;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = expanded ? "Hide" : "Show";
  }

  for (const group of groups) {
    const section = document.createElement("section");
    section.className = "settings-category";
    section.id = group.id;
    section.innerHTML = `<header class="settings-category-heading"><p>${group.eyebrow}</p><h2>${group.title}</h2><span>${group.description}</span></header><div class="settings-category-panels"></div>`;
    const container = section.querySelector(".settings-category-panels");
    group.panels.forEach((title, index) => {
      const panel = panels.get(title);
      if (!panel) return;
      panel.querySelector(".section-number").textContent = `${group.eyebrow} ${String(index + 1).padStart(2, "0")}`;
      panel.classList.toggle("settings-panel-wide", group.widePanels.includes(title));
      const fields = panel.querySelector(".panel-fields");
      fields.id = `${group.id}-panel-${index + 1}`;
      const toggle = document.createElement("button");
      toggle.className = "settings-panel-toggle";
      toggle.type = "button";
      toggle.setAttribute("aria-controls", fields.id);
      panel.querySelector(".panel-intro").append(toggle);
      toggle.addEventListener("click", () => setPanelExpanded(panel, toggle.getAttribute("aria-expanded") !== "true"));
      setPanelExpanded(panel, group.openPanels.includes(title));
      container.append(panel);
    });
    if (group.id === "settings-system") {
      const systemBody = document.createElement("div");
      systemBody.className = "settings-system-body";
      systemBody.append(actions, ui.form.querySelector("#relaunch-status"));
      section.append(systemBody);
    }
    if (group.id === "settings-system") ui.form.append(section);
    else ui.form.insertBefore(section, actions);
  }
}

organizeSettingsPage();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(url, options = {}) {
  const headers = options.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const response = await fetch(url, {
    headers,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data.detail, `Request failed with status ${response.status}`));
  return data;
}

function errorMessage(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (Array.isArray(detail)) return detail.map(item => item?.msg || item?.message).filter(Boolean).join("; ") || fallback;
  return fallback;
}

function showToast(message, kind = "success") {
  const dismiss = document.createElement("button");
  dismiss.className = "toast-dismiss";
  dismiss.type = "button";
  dismiss.textContent = "Dismiss";
  ui.toast.replaceChildren(document.createTextNode(message), dismiss);
  ui.toast.className = `toast ${kind}`;
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => ui.toast.classList.add("hidden"), 4500);
}

ui.toast.addEventListener("click", event => {
  if (event.target.closest(".toast-dismiss")) ui.toast.classList.add("hidden");
});

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

function motionChannels() {
  const channels = [];
  for (const bank of ["standard", "configurable"]) {
    for (let index = 0; index < 8; index += 1) channels.push({bank, index});
  }
  for (let index = 0; index < 2; index += 1) channels.push({bank: "tool", index});
  return channels;
}

function channelValue(channel) {
  return channel ? `${channel.bank}:${channel.index}` : "";
}

function selectedChannel(select) {
  const [bank, rawIndex] = select.value.split(":");
  return bank ? {bank, index: Number(rawIndex)} : null;
}

function outputActionDraft(output, state, pulse) {
  const channel = selectedChannel(output);
  return channel ? {output: channel, active_value: state.value === "true", pulse: pulse.checked} : null;
}

function loadOutputAction(action, output, state, pulse) {
  output.value = channelValue(action?.output);
  state.value = String(action?.active_value !== false);
  pulse.checked = action?.pulse !== false;
}

function renderMotionChannelOptions() {
  for (const select of [
    ui.palletMotionGripOutput,
    ui.millDoorOpenOutput,
    ui.millDoorCloseOutput,
    ui.erowaUnlockOutput,
    ui.erowaLockOutput,
  ]) {
    const selected = select.value;
    select.innerHTML = `<option value="">Not configured</option>${motionChannels().map(channel => `<option value="${channelValue(channel)}">${channel.bank} output ${channel.index}</option>`).join("")}`;
    select.value = selected;
  }
}

function renderGeneratedMotionPrograms() {
  const programs = board?.settings.pallet_motion_programs || [];
  ui.generatedMotionProgramList.textContent = programs.length
    ? `${programs.length * 2 + 4} generated scripts are configured. Pool 01: ${programs[0].pick_program}`
    : "No generated scripts have been rebuilt yet.";
}

function poolSlotCountForAssignments() {
  return Math.max(1, Math.min(256, fieldNumber(ui.poolSlotCount, board?.settings.pool_slot_count || 16)));
}

function poolAssignmentCheckboxes(selectedSlots = [], index = null) {
  const selected = new Set(selectedSlots.map(Number));
  return Array.from({length: poolSlotCountForAssignments()}, (_, offset) => {
    const slot = offset + 1;
    const attribute = index === null ? "data-intermediate-new-slot" : `data-intermediate-pose-index="${index}"`;
    return `<label><input type="checkbox" value="${slot}" ${attribute} ${selected.has(slot) ? "checked" : ""}>Pool ${String(slot).padStart(2, "0")}</label>`;
  }).join("");
}

function selectedNewIntermediateSlots() {
  return Array.from(ui.intermediateSafePoseSlots.querySelectorAll("input:checked"), input => Number(input.value));
}

function renderIntermediateSafePoseSlots() {
  const selected = selectedNewIntermediateSlots();
  ui.intermediateSafePoseSlots.innerHTML = `<span class="field-help">Assign the new pose to Pool positions</span><div class="intermediate-safe-pose-assignment">${poolAssignmentCheckboxes(selected)}</div>`;
}

function renderIntermediateSafePoses() {
  ui.intermediateSafePoseList.replaceChildren();
  if (!intermediateSafePoses.length) {
    ui.intermediateSafePoseList.textContent = "No intermediate safe poses. Scripts will move directly from the shared safe pose to the pallet approach.";
    return;
  }
  intermediateSafePoses.forEach((pose, index) => {
    const row = document.createElement("section");
    row.className = "intermediate-safe-pose-row";
    const joints = Array.isArray(pose.joints_rad) ? pose.joints_rad.map((value, joint) => `J${joint} ${Number(value).toFixed(3)}`).join(" | ") : "Invalid joint positions";
    row.innerHTML = `<header><strong>${index + 1}. ${escapeHtml(pose.name)}</strong><button class="button ghost" type="button" data-intermediate-safe-pose-index="${index}">Remove</button></header><small>${escapeHtml(joints)}</small><div class="intermediate-safe-pose-assignment">${poolAssignmentCheckboxes(pose.pool_slots || [], index)}</div>`;
    ui.intermediateSafePoseList.append(row);
  });
}

function motionPoseInput(axis, value, scope) {
  const labels = {x_mm: "X position (mm)", y_mm: "Y position (mm)", z_mm: "Z position (mm)", rx_rad: "Tool Rx (rad)", ry_rad: "Tool Ry (rad)", rz_rad: "Tool Rz (rad)"};
  const displayValue = value === undefined || value === null ? "" : Number(value);
  return `<label>${labels[axis]}<input type="number" step="any" data-round-to-decimals="3" data-motion-pose="${scope}" data-motion-axis="${axis}" value="${displayValue}"></label>`;
}

function renderMotionPoseFields(container, scope, pose, title = "Shared Safe Waypoint", description = "The robot moves here before entering a pallet position and again after lifting clear and retreating.") {
  container.innerHTML = `<div class="motion-pose-heading"><strong>${title}</strong><span>${description}</span></div><div class="location-axis-row">${["x_mm", "y_mm", "z_mm", "rx_rad", "ry_rad", "rz_rad"].map(axis => motionPoseInput(axis, pose?.[axis], scope)).join("")}</div><button class="button ghost capture-robot-pose" type="button" data-capture-motion-pose="${scope}">Use current robot pose</button>`;
  bindPrecisionRounding(container);
  for (const input of container.querySelectorAll("input")) input.addEventListener("input", () => { if (!isLoadingSettings) refreshDirtyState(); });
}

function motionPoseDraft(scope, name) {
  const values = {};
  for (const axis of ["x_mm", "y_mm", "z_mm", "rx_rad", "ry_rad", "rz_rad"]) {
    values[axis] = ui.form.querySelector(`[data-motion-pose="${scope}"][data-motion-axis="${axis}"]`)?.value.trim() || "";
  }
  if (Object.values(values).every(value => value === "")) return null;
  return {name, ...Object.fromEntries(Object.entries(values).map(([axis, value]) => [axis, Number(value)]))};
}

function renderJointWaypointFields(container, scope, waypoint, title = "Shared Safe Joint Pose", description = "A physically verified J0-J5 configuration used before and after every pallet movement. The robot will use this exact joint posture rather than calculating inverse kinematics from a Cartesian point.") {
  const joints = Array.isArray(waypoint?.joints_rad) ? waypoint.joints_rad : [];
  const legacyNotice = waypoint && !Array.isArray(waypoint.joints_rad) ? " Legacy Cartesian safety data was found. Capture the current verified joint posture before saving and rebuilding scripts." : "";
  const inputs = Array.from({length: 6}, (_, index) => `<label>J${index} (rad)<input type="number" step="any" data-round-to-decimals="3" data-joint-waypoint="${scope}" data-joint-index="${index}" value="${joints[index] ?? ""}"></label>`).join("");
  container.innerHTML = `<div class="motion-pose-heading"><strong>${title}</strong><span>${description}${legacyNotice}</span></div><div class="location-axis-row joint-waypoint-row">${inputs}</div><button class="button ghost capture-robot-pose" type="button" data-capture-joint-waypoint="${scope}">Capture current joint positions</button>`;
  bindPrecisionRounding(container);
  for (const input of container.querySelectorAll("input")) input.addEventListener("input", () => { if (!isLoadingSettings) refreshDirtyState(); });
}

function jointWaypointDraft(scope, name) {
  const joints = Array.from({length: 6}, (_, index) => ui.form.querySelector(`[data-joint-waypoint="${scope}"][data-joint-index="${index}"]`)?.value.trim() || "");
  if (joints.every(value => value === "")) return null;
  return {name, joints_rad: joints.map(Number)};
}

function g53Input(axis, value) {
  const label = axis.replace("_in", "").toUpperCase();
  return `<label>${label} (in)<input type="number" step="0.0001" data-g53-axis="${axis}" value="${Number(value || 0)}"></label>`;
}

function readMillG53() {
  return Object.fromEntries(["x_in", "y_in", "z_in"].map(axis => [axis, Number(ui.millLoadUnloadG53Fields.querySelector(`[data-g53-axis="${axis}"]`)?.value || 0)]));
}

function renderMillG53(position) {
  ui.millLoadUnloadG53Fields.innerHTML = `<legend>Load/unload position · G53 machine coordinates · inches</legend><div class="location-axis-row">${["x_in", "y_in", "z_in"].map(axis => g53Input(axis, position?.[axis])).join("")}</div>`;
  bindLocationInputs(ui.millLoadUnloadG53Fields);
}

function locationInput(axis, value, scope, slot = "") {
  const label = axis.replace("_mm", "").toUpperCase();
  return `<label>${label} (mm)<input type="number" step="any" data-round-to-decimals="3" data-location-scope="${scope}" data-location-slot="${slot}" data-location-axis="${axis}" value="${Number(value || 0)}"></label>`;
}

function normalizeThreeDecimalInput(input) {
  if (input.value.trim() === "") return;
  const value = Number(input.value);
  if (!Number.isFinite(value)) return;
  const rounded = Math.sign(value) * Math.round(Math.abs(value) * 1000 + Number.EPSILON) / 1000;
  input.value = String(rounded);
}

function bindPrecisionRounding(container) {
  for (const input of container.querySelectorAll("[data-round-to-decimals='3']")) {
    const normalize = () => normalizeThreeDecimalInput(input);
    input.addEventListener("change", normalize);
    input.addEventListener("blur", normalize);
  }
}

function captureLocationButton(scope, slot = "") {
  return `<button class="button ghost capture-robot-pose" type="button" data-capture-location="${scope}" data-capture-slot="${slot}">Use current robot position</button>`;
}

function bindLocationInputs(container) {
  bindPrecisionRounding(container);
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
    return `<fieldset class="location-fieldset"><legend>Pool ${String(slot).padStart(2, "0")}</legend><div class="location-axis-row">${locationInput("x_mm", location.x_mm, "pool", slot)}${locationInput("y_mm", location.y_mm, "pool", slot)}${locationInput("z_mm", location.z_mm, "pool", slot)}</div>${captureLocationButton("pool", slot)}</fieldset>`;
  }).join("");
  const onDeck = ui.onDeckLocationFields.querySelector("input") ? readLocation("on_deck") : board?.settings.on_deck_location || {};
  const dripping = ui.drippingLocationFields.querySelector("input") ? readLocation("dripping") : board?.settings.dripping_location || {};
  const robotMillLoadUnload = ui.robotMillLoadUnloadFields.querySelector("input") ? motionPoseDraft("robot-mill-load-unload", "Mill load/unload") : board?.settings.robot_mill_load_unload;
  const robotMillPreEntry = ui.robotMillPreEntryFields.querySelector("input") ? motionPoseDraft("robot-mill-pre-entry", "Mill pre-entry") : board?.settings.pallet_motion_generation?.mill_pre_entry_waypoint;
  const robotMillSafeEntryExit = ui.robotMillSafeEntryExitFields.querySelector("input") ? motionPoseDraft("robot-mill-safe-entry-exit", "Mill safe entry/exit") : board?.settings.robot_mill_safe_entry_exit;
  const millLoadUnloadG53 = ui.millLoadUnloadG53Fields.querySelector("input") ? readMillG53() : board?.settings.mill_load_unload_g53 || {};
  ui.onDeckLocationFields.innerHTML = `<legend>On Deck Staging Pose</legend><p class="field-help">Robot position for the optional pallet staged before loading.</p><div class="location-axis-row">${locationInput("x_mm", onDeck.x_mm, "on_deck")}${locationInput("y_mm", onDeck.y_mm, "on_deck")}${locationInput("z_mm", onDeck.z_mm, "on_deck")}</div>${captureLocationButton("on_deck")}`;
  ui.drippingLocationFields.innerHTML = `<legend>Dripping Return Pose</legend><p class="field-help">Robot position for the optional completed pallet awaiting return to the pool.</p><div class="location-axis-row">${locationInput("x_mm", dripping.x_mm, "dripping")}${locationInput("y_mm", dripping.y_mm, "dripping")}${locationInput("z_mm", dripping.z_mm, "dripping")}</div>${captureLocationButton("dripping")}`;
  renderMotionPoseFields(ui.robotMillLoadUnloadFields, "robot-mill-load-unload", robotMillLoadUnload, "Robot Mill Load/Unload Pose", "The fork pose used while physically loading or unloading the mill pallet.");
  renderMotionPoseFields(ui.robotMillPreEntryFields, "robot-mill-pre-entry", robotMillPreEntry, "Robot Mill Pre-entry Waypoint", "The first Cartesian waypoint before the existing mill entry/exit pose. Scripts return through this waypoint after leaving the mill.");
  renderMotionPoseFields(ui.robotMillSafeEntryExitFields, "robot-mill-safe-entry-exit", robotMillSafeEntryExit, "Robot Mill Safe Entry/Exit", "The shared clearance pose used before entering the mill and after retracting from it.");
  renderMillG53(millLoadUnloadG53);
  bindLocationInputs(ui.poolLocationGrid);
  bindLocationInputs(ui.onDeckLocationFields);
  bindLocationInputs(ui.drippingLocationFields);
  syncOptionalStationUi();
}

function syncOptionalStationUi() {
  ui.onDeckLocationFields.disabled = !ui.onDeckEnabled.checked;
  ui.drippingLocationFields.disabled = !ui.drippingEnabled.checked;
}

function settingsDraft() {
  return {
    source_folder: ui.source.value,
    program_extensions: programExtensions(),
    weight_unit: ui.unit.value,
    pool_slot_count: fieldNumber(ui.poolSlotCount, board.settings.pool_slot_count),
    on_deck_enabled: ui.onDeckEnabled.checked,
    dripping_enabled: ui.drippingEnabled.checked,
    run_mode_safety_confirm: ui.runModeSafetyConfirm.checked,
    pool_locations: poolLocationsDraft(),
    on_deck_location: readLocation("on_deck"),
    dripping_location: readLocation("dripping"),
    robot_mill_load_unload: motionPoseDraft("robot-mill-load-unload", "Mill load/unload"),
    robot_mill_safe_entry_exit: motionPoseDraft("robot-mill-safe-entry-exit", "Mill safe entry/exit"),
    mill_load_unload_g53: readMillG53(),
    debug_menu_enabled: ui.debugMenuEnabled.checked,
    manual_io_control_enabled: ui.manualIoControlEnabled.checked,
    robot_connection_mode: ui.robotConnectionMode.value,
    robot_host: ui.robotHost.value.trim(),
    robot_port: fieldNumber(ui.robotPort, board.settings.robot_port || 30003),
    robot_poll_hz: fieldNumber(ui.robotPollHz, board.settings.robot_poll_hz || 10),
    robot_timeout_seconds: fieldNumber(ui.robotTimeoutSeconds, board.settings.robot_timeout_seconds || 1.0),
    robot_supervisor_enabled: ui.robotSupervisorEnabled.checked,
    robot_supervisor_hostname: ui.robotSupervisorHostname.value.trim() || "DESKTOP-KF5I73N.lan",
    robot_supervisor_listen_host: ui.robotSupervisorListenHost.value.trim() || "0.0.0.0",
    robot_supervisor_port: fieldNumber(ui.robotSupervisorPort, 50010),
    robot_supervisor_heartbeat_seconds: fieldNumber(ui.robotSupervisorHeartbeatSeconds, 1),
    robot_supervisor_telemetry_hz: fieldNumber(ui.robotSupervisorTelemetryHz, 2),
    robot_supervisor_reconnect_limit_seconds: fieldNumber(ui.robotSupervisorReconnectLimitSeconds, 10),
    robot_supervisor_pre_dispatch_fallback: ui.robotSupervisorFallback.checked,
    cnc_telemetry_enabled: ui.cncTelemetryEnabled.checked,
    cnc_host: ui.cncHost.value.trim(),
    cnc_ssh_port: fieldNumber(ui.cncSshPort, board.settings.cnc_ssh_port || 22),
    cnc_ssh_username: ui.cncSshUsername.value.trim() || "operator",
    cnc_ssh_password: ui.cncSshPassword.value,
    cnc_timeout_seconds: fieldNumber(ui.cncTimeoutSeconds, board.settings.cnc_timeout_seconds || 2),
    cnc_require_a_axis_homed: ui.cncRequireAAxisHomed.checked,
    debug_program_button_count: fieldNumber(ui.debugProgramButtonCount, board.settings.debug_program_button_count || 4),
    debug_mill_program_button_count: fieldNumber(ui.debugMillProgramButtonCount, board.settings.debug_mill_program_button_count || 4),
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
    mill_file_directory: ui.millFileDirectory.value.trim() || "/home/operator/gcode/Gcode",
    mill_program_extensions: millProgramExtensions(),
    mill_programs_filter_enabled: ui.millProgramsFilterEnabled.checked,
    mill_programs_page_enabled: ui.millProgramsPageEnabled.checked,
    mill_editor_command: ui.millEditorCommand.value.trim() || "code",
    mill_results_archiving_enabled: ui.millResultsArchivingEnabled.checked,
    mill_results_source_path: ui.millResultsSourcePath.value.trim() || "/home/operator/gcode/RESULTS.TXT",
    mill_results_archive_directory: ui.millResultsArchiveDirectory.value.trim() || "/home/operator/gcode/results",
    pallet_motion_enabled: ui.palletMotionEnabled.checked,
    pallet_motion_timeout_seconds: fieldNumber(ui.palletMotionTimeoutSeconds, 120),
    pallet_motion_generation: {
      approach_y_clearance_mm: fieldNumber(ui.palletMotionApproachYClearance, 100),
      mill_approach_x_clearance_mm: fieldNumber(ui.palletMotionMillApproachXClearance, 100),
      lift_z_clearance_mm: fieldNumber(ui.palletMotionLiftZClearance, 100),
      mill_lift_z_clearance_mm: fieldNumber(ui.palletMotionMillLiftZClearance, 100),
      max_travel_speed_rad_s: fieldNumber(ui.palletMotionMaxTravelSpeed, 0.6),
      pickup_setdown_speed_m_s: fieldNumber(ui.palletMotionPickupSpeed, 0.08),
      rx_rad: fieldNumber(ui.palletMotionRx, 0),
      ry_rad: fieldNumber(ui.palletMotionRy, 0),
      rz_rad: fieldNumber(ui.palletMotionRz, 0),
      grip_output: selectedChannel(ui.palletMotionGripOutput),
      grip_closed_value: ui.palletMotionGripClosedValue.checked,
      door_open_action: outputActionDraft(ui.millDoorOpenOutput, ui.millDoorOpenState, ui.millDoorOpenPulse),
      door_close_action: outputActionDraft(ui.millDoorCloseOutput, ui.millDoorCloseState, ui.millDoorClosePulse),
      erowa_unlock_action: outputActionDraft(ui.erowaUnlockOutput, ui.erowaUnlockState, ui.erowaUnlockPulse),
      erowa_lock_action: outputActionDraft(ui.erowaLockOutput, ui.erowaLockState, ui.erowaLockPulse),
      mill_actuation_wait_seconds: fieldNumber(ui.millActuationWaitSeconds, 2),
      mill_pre_entry_waypoint: motionPoseDraft("robot-mill-pre-entry", "Mill pre-entry"),
      safe_pre_waypoint: jointWaypointDraft("safe-pre", "Shared Safe Joint Pose"),
      safe_post_waypoint: jointWaypointDraft("safe-pre", "Shared Safe Joint Pose"),
      travel_waypoints: [],
      intermediate_safe_poses: intermediateSafePoses,
    },
    workholding_library: workholdingLibrary,
  };
}

function cloneSettingsDraft(value) {
  return JSON.parse(JSON.stringify(value));
}

function changedSettings(base, current) {
  return Object.fromEntries(Object.keys(current).filter(key => JSON.stringify(current[key]) !== JSON.stringify(base?.[key])).map(key => [key, current[key]]));
}

function rebaseSettingsChanges(base, current, latestSettings) {
  const changes = changedSettings(base, current);
  for (const [key, value] of Object.entries(changes)) {
    if (!Array.isArray(value) && value && typeof value === "object" && latestSettings[key] && typeof latestSettings[key] === "object") {
      const nestedChanges = changedSettings(base?.[key] || {}, value);
      changes[key] = {...latestSettings[key], ...nestedChanges};
    }
  }
  return changes;
}

function normalizeSettingsPrecision() {
  for (const input of ui.form.querySelectorAll("[data-round-to-decimals='3']")) normalizeThreeDecimalInput(input);
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
    ui.onDeckEnabled.checked = board.settings.on_deck_enabled !== false;
    ui.drippingEnabled.checked = board.settings.dripping_enabled !== false;
    renderLocationFields();
    ui.debugMenuEnabled.checked = board.settings.debug_menu_enabled;
    ui.manualIoControlEnabled.checked = board.settings.manual_io_control_enabled;
    ui.robotConnectionMode.value = board.settings.robot_connection_mode;
    ui.robotHost.value = board.settings.robot_host || "";
    ui.robotPort.value = board.settings.robot_port;
    ui.robotPollHz.value = board.settings.robot_poll_hz;
    ui.robotTimeoutSeconds.value = board.settings.robot_timeout_seconds;
    ui.robotSupervisorEnabled.checked = board.settings.robot_supervisor_enabled;
    ui.robotSupervisorHostname.value = board.settings.robot_supervisor_hostname || "DESKTOP-KF5I73N.lan";
    ui.robotSupervisorListenHost.value = board.settings.robot_supervisor_listen_host || "0.0.0.0";
    ui.robotSupervisorPort.value = board.settings.robot_supervisor_port || 50010;
    ui.robotSupervisorHeartbeatSeconds.value = board.settings.robot_supervisor_heartbeat_seconds ?? 1;
    ui.robotSupervisorTelemetryHz.value = board.settings.robot_supervisor_telemetry_hz ?? 2;
    ui.robotSupervisorReconnectLimitSeconds.value = board.settings.robot_supervisor_reconnect_limit_seconds ?? 10;
    ui.robotSupervisorFallback.checked = board.settings.robot_supervisor_pre_dispatch_fallback !== false;
    ui.robotSupervisorStatus.textContent = board.settings.robot_supervisor_activation_verified
      ? "No-motion handshake verified. Enable and save when ready."
      : "Not verified. Save and rebuild scripts before testing.";
    ui.cncTelemetryEnabled.checked = board.settings.cnc_telemetry_enabled;
    ui.cncHost.value = board.settings.cnc_host || "";
    ui.cncSshPort.value = board.settings.cnc_ssh_port || 22;
    ui.cncSshUsername.value = board.settings.cnc_ssh_username || "operator";
    ui.cncSshPassword.value = board.settings.cnc_ssh_password || "";
    ui.cncTimeoutSeconds.value = board.settings.cnc_timeout_seconds || 2;
    ui.cncRequireAAxisHomed.checked = Boolean(board.settings.cnc_require_a_axis_homed);
    ui.runModeSafetyConfirm.checked = board.settings.run_mode_safety_confirm !== false;
    ui.runModeSafetyConfirm.disabled = Boolean(board.run_mode?.enabled);
    ui.debugProgramButtonCount.value = board.settings.debug_program_button_count || 4;
    ui.debugMillProgramButtonCount.value = board.settings.debug_mill_program_button_count || 4;
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
    ui.millFileDirectory.value = board.settings.mill_file_directory || "/home/operator/gcode/Gcode";
    ui.millProgramExtensions.value = board.settings.mill_program_extensions.join(", ");
    ui.millProgramsFilterEnabled.checked = board.settings.mill_programs_filter_enabled;
    ui.millProgramsPageEnabled.checked = board.settings.mill_programs_page_enabled;
    ui.millEditorCommand.value = board.settings.mill_editor_command || "code";
    ui.millResultsArchivingEnabled.checked = board.settings.mill_results_archiving_enabled !== false;
    ui.millResultsSourcePath.value = board.settings.mill_results_source_path || "/home/operator/gcode/RESULTS.TXT";
    ui.millResultsArchiveDirectory.value = board.settings.mill_results_archive_directory || "/home/operator/gcode/results";
    renderMotionChannelOptions();
    ui.palletMotionEnabled.checked = board.settings.pallet_motion_enabled;
    ui.palletMotionTimeoutSeconds.value = board.settings.pallet_motion_timeout_seconds || 120;
    const generation = board.settings.pallet_motion_generation || {};
    ui.palletMotionApproachYClearance.value = generation.approach_y_clearance_mm ?? 100;
    ui.palletMotionMillApproachXClearance.value = generation.mill_approach_x_clearance_mm ?? 100;
    ui.palletMotionLiftZClearance.value = generation.lift_z_clearance_mm ?? 100;
    ui.palletMotionMillLiftZClearance.value = generation.mill_lift_z_clearance_mm ?? 100;
    ui.palletMotionMaxTravelSpeed.value = generation.max_travel_speed_rad_s ?? 0.6;
    ui.palletMotionPickupSpeed.value = generation.pickup_setdown_speed_m_s ?? 0.08;
    ui.palletMotionGripOutput.value = channelValue(generation.grip_output);
    ui.palletMotionGripClosedValue.checked = generation.grip_closed_value !== false;
    loadOutputAction(generation.door_open_action, ui.millDoorOpenOutput, ui.millDoorOpenState, ui.millDoorOpenPulse);
    loadOutputAction(generation.door_close_action, ui.millDoorCloseOutput, ui.millDoorCloseState, ui.millDoorClosePulse);
    loadOutputAction(generation.erowa_unlock_action, ui.erowaUnlockOutput, ui.erowaUnlockState, ui.erowaUnlockPulse);
    loadOutputAction(generation.erowa_lock_action, ui.erowaLockOutput, ui.erowaLockState, ui.erowaLockPulse);
    ui.millActuationWaitSeconds.value = generation.mill_actuation_wait_seconds ?? 2;
    ui.palletMotionRx.value = generation.rx_rad ?? 0;
    ui.palletMotionRy.value = generation.ry_rad ?? 0;
    ui.palletMotionRz.value = generation.rz_rad ?? 0;
    renderJointWaypointFields(ui.palletMotionSafePreFields, "safe-pre", generation.safe_pre_waypoint);
    intermediateSafePoses = [...(generation.intermediate_safe_poses || [])];
    renderIntermediateSafePoseSlots();
    renderIntermediateSafePoses();
    renderGeneratedMotionPrograms();
    workholdingLibrary = [...(board.settings.workholding_library || [])];
    renderWorkholdingLibrary();
    renderFusionToolLibraries(board.settings.fusion_tool_libraries || []);
    document.querySelectorAll("[data-robot-programs-nav]").forEach(link => link.classList.toggle("hidden", !board.settings.robot_programs_page_enabled));
    document.querySelectorAll("[data-mill-programs-nav]").forEach(link => link.classList.toggle("hidden", !board.settings.mill_programs_page_enabled));
    savedSettingsDraft = cloneSettingsDraft(settingsDraft());
    savedSettingsSignature = JSON.stringify(savedSettingsDraft);
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

function showScriptRebuildPrompt(afterChoice = null) {
  afterScriptRebuildPrompt = afterChoice;
  ui.scriptRebuildModal.classList.remove("hidden");
  ui.scriptRebuildNow.focus();
}

function closeScriptRebuildPrompt() {
  ui.scriptRebuildModal.classList.add("hidden");
  const afterChoice = afterScriptRebuildPrompt;
  afterScriptRebuildPrompt = null;
  return afterChoice;
}

async function saveSettings({promptForScriptRebuild = true} = {}) {
  normalizeSettingsPrecision();
  const draft = settingsDraft();
  const baseDraft = savedSettingsDraft || cloneSettingsDraft(draft);
  let settingsChanges = changedSettings(baseDraft, draft);
  try {
    const submit = revision => api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({expected_revision: revision, ...settingsChanges}),
    });
    let result;
    try {
      result = await submit(board.revision);
    } catch (error) {
      if (!error.message.includes("another session")) throw error;
      const latestBoard = await api("/api/board");
      settingsChanges = rebaseSettingsChanges(baseDraft, draft, latestBoard.settings);
      board = latestBoard;
      result = await submit(board.revision);
    }
    board = result.board;
    ui.manualIoControlEnabled.checked = board.settings.manual_io_control_enabled;
    ui.robotSupervisorEnabled.checked = board.settings.robot_supervisor_enabled;
    if (!board.settings.robot_supervisor_activation_verified) {
      ui.robotSupervisorStatus.textContent = "Supervisor configuration changed. Rebuild scripts and repeat the no-motion bootstrap.";
    }
    workholdingLibrary = [...(board.settings.workholding_library || [])];
    renderWorkholdingLibrary();
    savedSettingsDraft = cloneSettingsDraft(settingsDraft());
    savedSettingsSignature = JSON.stringify(savedSettingsDraft);
    setDirtyState(false);
    setSystemState();
    syncRobotModeUi();
    const cleared = result.cleared_assignments.length
      ? ` Cleared program assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    showToast(`Settings saved.${cleared}`);
    if (promptForScriptRebuild && board.settings.motion_scripts_need_rebuild) showScriptRebuildPrompt();
    return true;
  } catch (error) {
    showToast(error.message, "error");
    return false;
  }
}

ui.form.addEventListener("submit", async event => {
  event.preventDefault();
  await saveSettings();
});

bindPrecisionRounding(ui.form);

ui.robotConnectionMode.addEventListener("change", syncRobotModeUi);
ui.onDeckEnabled.addEventListener("change", syncOptionalStationUi);
ui.drippingEnabled.addEventListener("change", syncOptionalStationUi);
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

function renderSupervisorStatus(status) {
  if (!status.listening) {
    ui.robotSupervisorStatus.textContent = status.last_disconnect_detail || "Backend listener is unavailable.";
    return;
  }
  if (!status.connected) {
    ui.robotSupervisorStatus.textContent = `Listening on ${status.listen_host}:${status.listen_port}; Mongo is not connected.`;
    return;
  }
  const match = status.robot_last_sequence === status.expected_sequence ? "sequence matched" : "reconciliation required";
  ui.robotSupervisorStatus.textContent = `Connected · robot session ${status.robot_session} · heartbeat ${status.heartbeat_age_seconds ?? "?"}s · ${match}${status.latched ? " · LATCHED" : ""}.`;
}

async function loadSupervisorStatus() {
  try {
    renderSupervisorStatus(await api("/api/debug/robot-supervisor", {cache: "no-store"}));
  } catch (error) {
    ui.robotSupervisorStatus.textContent = error.message;
  }
}

ui.bootstrapRobotSupervisor.addEventListener("click", async () => {
  if (hasUnsavedChanges()) {
    showToast("Save settings and rebuild generated scripts before the no-motion bootstrap.", "error");
    return;
  }
  if (board.settings.motion_scripts_need_rebuild) {
    showToast("Rebuild generated scripts before the no-motion bootstrap.", "error");
    return;
  }
  const button = ui.bootstrapRobotSupervisor;
  button.disabled = true;
  button.textContent = "Waiting for Mongo...";
  ui.robotSupervisorStatus.textContent = "Starting the supervisor without dispatching a movement...";
  try {
    const status = await api("/api/debug/robot-supervisor/bootstrap", {method: "POST", body: "{}"});
    renderSupervisorStatus(status);
    board = await api("/api/board", {cache: "no-store"});
    ui.robotSupervisorEnabled.checked = board.settings.robot_supervisor_enabled;
    savedSettingsDraft = cloneSettingsDraft(settingsDraft());
    savedSettingsSignature = JSON.stringify(savedSettingsDraft);
    showToast("No-motion supervisor handshake verified. Enable it and Save settings when ready.");
  } catch (error) {
    ui.robotSupervisorStatus.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Run no-motion bootstrap";
  }
});

ui.installSupervisorFirewall.addEventListener("click", async () => {
  if (hasUnsavedChanges()) {
    showToast("Save the supervisor port before installing its firewall rule.", "error");
    return;
  }
  const button = ui.installSupervisorFirewall;
  button.disabled = true;
  try {
    const result = await api("/api/system/supervisor-firewall", {method: "POST", body: "{}"});
    ui.robotSupervisorStatus.textContent = `${result.message} Approve the Windows prompt, then run the no-motion bootstrap.`;
    showToast(`Firewall setup opened for TCP port ${result.port}.`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

ui.buildMillLoadPositionProgram.addEventListener("click", async () => {
  if (isDirty) {
    ui.millLoadPositionProgramStatus.textContent = "Save the changed loading coordinates before building the program.";
    showToast("Save Settings before building the mill loading program.", "error");
    return;
  }
  const originalLabel = ui.buildMillLoadPositionProgram.textContent;
  ui.buildMillLoadPositionProgram.disabled = true;
  ui.buildMillLoadPositionProgram.textContent = "Building...";
  ui.millLoadPositionProgramStatus.textContent = "Writing and uploading the G53 loading program...";
  try {
    const result = await api("/api/mill-programs/rebuild-load-position", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision}),
    });
    ui.millLoadPositionProgramStatus.textContent = `${result.filename} uploaded to ${result.remote_path}.`;
    showToast("Mill loading program built and uploaded.");
  } catch (error) {
    ui.millLoadPositionProgramStatus.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    ui.buildMillLoadPositionProgram.disabled = false;
    ui.buildMillLoadPositionProgram.textContent = originalLabel;
  }
});
ui.poolSlotCount.addEventListener("change", () => {
  if (!board) return;
  const poolSlotCount = fieldNumber(ui.poolSlotCount, board.settings.pool_slot_count || 16);
  intermediateSafePoses = intermediateSafePoses.map(pose => ({...pose, pool_slots: (pose.pool_slots || []).filter(slot => Number(slot) <= poolSlotCount)}));
  renderLocationFields();
  renderIntermediateSafePoseSlots();
  renderIntermediateSafePoses();
  refreshDirtyState();
});

function poseCaptureName(button) {
  if (button.hasAttribute("data-capture-joint-waypoint")) {
    return button.dataset.captureJointWaypoint === "safe-pre" ? "the shared safe joint pose" : "the intermediate joint pose";
  }
  if (button.hasAttribute("data-capture-motion-pose")) {
    if (button.dataset.captureMotionPose === "robot-mill-load-unload") return "the robot mill load/unload pose";
    if (button.dataset.captureMotionPose === "robot-mill-pre-entry") return "the robot mill pre-entry waypoint";
    return "the robot mill entry/exit pose";
  }
  if (button.dataset.captureLocation === "pool") return `Pool ${String(button.dataset.captureSlot).padStart(2, "0")} robot position`;
  if (button.dataset.captureLocation === "on_deck") return "the On deck robot position";
  if (button.dataset.captureLocation === "dripping") return "the Dripping robot position";
  return "the selected robot pose";
}

function openPoseCaptureModal(button) {
  pendingPoseCaptureButton = button;
  const label = poseCaptureName(button);
  ui.poseCaptureMessage.textContent = `Capture the live robot position and replace ${label}? This changes only the displayed settings until Save settings is pressed.`;
  ui.poseCaptureModal.classList.remove("hidden");
  ui.poseCaptureConfirm.focus();
}

function closePoseCaptureModal() {
  pendingPoseCaptureButton = null;
  ui.poseCaptureModal.classList.add("hidden");
}

async function captureRobotPose(button) {

  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Reading robot...";
  try {
    const pose = await api("/api/debug/robot-pose");
    if (button.hasAttribute("data-capture-joint-waypoint")) {
      if (!Array.isArray(pose.joints_rad) || pose.joints_rad.length !== 6) throw new Error("Live robot joint positions are unavailable. Confirm physical RTDE telemetry is connected before capturing a joint waypoint.");
      const scope = button.dataset.captureJointWaypoint;
      pose.joints_rad.forEach((value, index) => {
        const input = ui.form.querySelector(`[data-joint-waypoint="${scope}"][data-joint-index="${index}"]`);
        if (input) input.value = value;
      });
      showToast("Current J0-J5 joint positions captured. Press Save changes to store them.");
    } else if (button.hasAttribute("data-capture-motion-pose")) {
      const scope = button.dataset.captureMotionPose;
      for (const axis of ["x_mm", "y_mm", "z_mm", "rx_rad", "ry_rad", "rz_rad"]) {
        const input = ui.form.querySelector(`[data-motion-pose="${scope}"][data-motion-axis="${axis}"]`);
        if (input) input.value = pose[axis];
      }
    } else {
      const scope = button.dataset.captureLocation;
      const slot = button.dataset.captureSlot || "";
      for (const axis of ["x_mm", "y_mm", "z_mm"]) {
        const input = ui.form.querySelector(`[data-location-scope="${scope}"][data-location-slot="${slot}"][data-location-axis="${axis}"]`);
        if (input) input.value = pose[axis];
      }
    }
    refreshDirtyState();
    if (!button.hasAttribute("data-capture-joint-waypoint")) showToast(`Robot pose captured: X ${pose.x_mm} mm, Y ${pose.y_mm} mm, Z ${pose.z_mm} mm. Press Save changes to store it.`);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

ui.form.addEventListener("click", event => {
  const button = event.target.closest(".capture-robot-pose");
  if (button) openPoseCaptureModal(button);
});

ui.poseCaptureCancel.addEventListener("click", closePoseCaptureModal);
ui.poseCaptureConfirm.addEventListener("click", async () => {
  const button = pendingPoseCaptureButton;
  closePoseCaptureModal();
  if (button) await captureRobotPose(button);
});

ui.addIntermediateSafePose.addEventListener("click", () => {
  const name = ui.intermediateSafePoseName.value.trim();
  const joints = ui.intermediateSafePoseJoints.map(input => input.value.trim());
  const poolSlots = selectedNewIntermediateSlots();
  if (!name) return showToast("Enter an intermediate safe-pose name.", "error");
  if (intermediateSafePoses.some(pose => pose.name.localeCompare(name, undefined, {sensitivity: "accent"}) === 0)) return showToast("Intermediate safe-pose names must be unique.", "error");
  if (joints.some(value => value === "" || !Number.isFinite(Number(value)))) return showToast("Enter all six finite joint positions for the intermediate safe pose.", "error");
  if (!poolSlots.length) return showToast("Assign the intermediate safe pose to at least one Pool position.", "error");
  intermediateSafePoses.push({name, joints_rad: joints.map(Number), pool_slots: poolSlots});
  ui.intermediateSafePoseName.value = "";
  ui.intermediateSafePoseJoints.forEach(input => { input.value = ""; });
  renderIntermediateSafePoseSlots();
  renderIntermediateSafePoses();
  refreshDirtyState();
});

ui.intermediateSafePoseList.addEventListener("change", event => {
  const input = event.target.closest("[data-intermediate-pose-index]");
  if (!input) return;
  const index = Number(input.dataset.intermediatePoseIndex);
  const row = input.closest(".intermediate-safe-pose-row");
  intermediateSafePoses[index].pool_slots = Array.from(row.querySelectorAll("input:checked"), checkbox => Number(checkbox.value));
  refreshDirtyState();
});

ui.intermediateSafePoseList.addEventListener("click", event => {
  const button = event.target.closest("[data-intermediate-safe-pose-index]");
  if (!button) return;
  intermediateSafePoses.splice(Number(button.dataset.intermediateSafePoseIndex), 1);
  renderIntermediateSafePoses();
  refreshDirtyState();
});

async function rebuildMotionScripts() {
  ui.rebuildMotionScripts.disabled = true;
  ui.motionProgramFileStatus.textContent = "Synchronizing generated scripts...";
  try {
    const result = await api("/api/robot-motions/rebuild-scripts", {method: "POST"});
    board = result.board;
    renderGeneratedMotionPrograms();
    ui.motionProgramFileStatus.textContent = `${result.files.length} scripts synchronized locally and to the robot.`;
    savedSettingsDraft = cloneSettingsDraft(settingsDraft());
    savedSettingsSignature = JSON.stringify(savedSettingsDraft);
    setDirtyState(false);
    showToast("Generated robot scripts rebuilt and synchronized.");
    return true;
  } catch (error) {
    ui.motionProgramFileStatus.textContent = error.message;
    showToast(error.message, "error");
    return false;
  } finally {
    ui.rebuildMotionScripts.disabled = false;
  }
}

ui.rebuildMotionScripts.addEventListener("click", async () => {
  ui.motionProgramFileStatus.textContent = "Saving settings and synchronizing generated scripts...";
  if (!await saveSettings({promptForScriptRebuild: false})) return;
  await rebuildMotionScripts();
});

ui.scriptRebuildLater.addEventListener("click", () => {
  const afterChoice = closeScriptRebuildPrompt();
  afterChoice?.();
});
ui.scriptRebuildNow.addEventListener("click", async () => {
  const afterChoice = closeScriptRebuildPrompt();
  if (await rebuildMotionScripts()) afterChoice?.();
});

function renderWorkholdingLibrary() {
  ui.workholdingLibraryList.replaceChildren();
  if (!workholdingLibrary.length) {
    ui.workholdingLibraryList.textContent = "No workholding descriptions have been added.";
    return;
  }
  workholdingLibrary.forEach((name, index) => {
    const row = document.createElement("div");
    row.className = "managed-library-row";
    const label = document.createElement("span");
    label.textContent = name;
    const remove = document.createElement("button");
    remove.className = "button ghost";
    remove.type = "button";
    remove.textContent = "Remove";
    remove.dataset.workholdingIndex = String(index);
    row.append(label, remove);
    ui.workholdingLibraryList.append(row);
  });
}

function addWorkholding() {
  const name = ui.newWorkholding.value.trim();
  if (!name) return;
  if (workholdingLibrary.some(item => item.localeCompare(name, undefined, {sensitivity: "accent"}) === 0)) {
    showToast("That workholding description is already in the library.", "error");
    return;
  }
  workholdingLibrary.push(name);
  ui.newWorkholding.value = "";
  renderWorkholdingLibrary();
  refreshDirtyState();
}

ui.addWorkholding.addEventListener("click", addWorkholding);
ui.newWorkholding.addEventListener("keydown", event => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  addWorkholding();
});
ui.workholdingLibraryList.addEventListener("click", event => {
  const button = event.target.closest("[data-workholding-index]");
  if (!button) return;
  const index = Number(button.dataset.workholdingIndex);
  workholdingLibrary.splice(index, 1);
  renderWorkholdingLibrary();
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
  if (field === ui.newWorkholding) continue;
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
  const saved = await saveSettings({promptForScriptRebuild: false});
  ui.unsavedSave.disabled = false;
  if (!saved) return;
  if (board.settings.motion_scripts_need_rebuild) {
    const navigate = pendingNavigation;
    closeUnsavedModal();
    showScriptRebuildPrompt(() => {
      if (!navigate) return;
      allowNavigation = true;
      navigate();
    });
  } else {
    continueNavigation();
  }
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

loadSettings().then(loadSupervisorStatus);
