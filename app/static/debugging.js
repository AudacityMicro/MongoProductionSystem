const ui = {
  state: document.querySelector("#system-state"),
  toast: document.querySelector("#toast"),
  notes: document.querySelector("#debug-notes"),
  connectionLight: document.querySelector("#debug-connection-light"),
  connectionLabel: document.querySelector("#debug-connection-label"),
  source: document.querySelector("#debug-source"),
  machineState: document.querySelector("#debug-machine-state"),
  timestamp: document.querySelector("#debug-timestamp"),
  autoRecover: document.querySelector("#debug-auto-recover"),
  clearMongoFault: document.querySelector("#clear-mongo-fault"),
  networkTest: document.querySelector("#run-network-test"),
  networkTestResult: document.querySelector("#network-test-result"),
  diagnosticEventList: document.querySelector("#diagnostic-event-list"),
  supervisorStatusGrid: document.querySelector("#robot-supervisor-status-grid"),
  supervisorDetail: document.querySelector("#robot-supervisor-detail"),
  supervisorCommandRows: document.querySelector("#robot-supervisor-command-rows"),
  supervisorMaintenance: document.querySelector("#debug-supervisor-maintenance"),
  supervisorClearLatch: document.querySelector("#debug-supervisor-clear-latch"),
  summaryMachinePallet: document.querySelector("#summary-machine-pallet"),
  summaryQueueCount: document.querySelector("#summary-queue-count"),
  summaryPoolCount: document.querySelector("#summary-pool-count"),
  summaryStorageCount: document.querySelector("#summary-storage-count"),
  summaryPoolOpen: document.querySelector("#summary-pool-open"),
  digitalIoLayout: document.querySelector("#digital-io-layout"),
  analogInputs: document.querySelector("#analog-inputs"),
  analogOutputs: document.querySelector("#analog-outputs"),
  stateRows: document.querySelector("#state-rows"),
  motionRows: document.querySelector("#motion-rows"),
  tcpDetailRows: document.querySelector("#tcp-detail-rows"),
  jointDetailRows: document.querySelector("#joint-detail-rows"),
  actualRows: document.querySelector("#actual-rows"),
  programButtons: document.querySelector("#debug-program-buttons"),
  millProgramButtons: document.querySelector("#debug-mill-program-buttons"),
  loadedControllerProgram: document.querySelector("#loaded-controller-program"),
  programFileNote: document.querySelector("#program-file-note"),
  millProgramFileNote: document.querySelector("#mill-program-file-note"),
  palletMotionSlot: document.querySelector("#debug-pallet-motion-slot"),
  palletMotionPick: document.querySelector("#debug-pick-pallet"),
  palletMotionPlace: document.querySelector("#debug-place-pallet"),
  palletMotionStatus: document.querySelector("#debug-pallet-motion-status"),
  millMotionLoad: document.querySelector("#debug-load-mill"),
  millMotionUnload: document.querySelector("#debug-unload-mill"),
  millMotionStatus: document.querySelector("#debug-mill-motion-status"),
  reliabilityStart: document.querySelector("#start-reliability-test"),
  reliabilityCancel: document.querySelector("#cancel-reliability-test"),
  reliabilityStatus: document.querySelector("#reliability-test-status"),
  reliabilityQueue: document.querySelector("#reliability-test-queue"),
  programDialog: document.querySelector("#program-button-dialog"),
  programForm: document.querySelector("#program-button-form"),
  programName: document.querySelector("#program-button-name"),
  programFilename: document.querySelector("#program-button-filename"),
  programColor: document.querySelector("#program-button-color"),
  programCancel: document.querySelector("#program-button-cancel"),
  millProgramDialog: document.querySelector("#mill-program-button-dialog"),
  millProgramForm: document.querySelector("#mill-program-button-form"),
  millProgramName: document.querySelector("#mill-program-button-name"),
  millProgramFilename: document.querySelector("#mill-program-button-filename"),
  millProgramColor: document.querySelector("#mill-program-button-color"),
  millProgramCancel: document.querySelector("#mill-program-button-cancel"),
  cncConnectionLight: document.querySelector("#cnc-connection-light"),
  cncConnectionLabel: document.querySelector("#cnc-connection-label"),
  cncNotes: document.querySelector("#cnc-notes"),
  millSupervisorGrid: document.querySelector("#mill-supervisor-grid"),
  millSupervisorDetail: document.querySelector("#mill-supervisor-detail"),
  reconcileMillSupervisor: document.querySelector("#reconcile-mill-supervisor"),
  cncControllerState: document.querySelector("#cnc-controller-state"),
  cncProgram: document.querySelector("#cnc-program"),
  cncSpindle: document.querySelector("#cnc-spindle"),
  cncCoolant: document.querySelector("#cnc-coolant"),
  cncFeedOverride: document.querySelector("#cnc-feed-override"),
  cncTimestamp: document.querySelector("#cnc-timestamp"),
  cncAxisRows: document.querySelector("#cnc-axis-rows"),
  cncHealthGrid: document.querySelector("#cnc-health-grid"),
  cncProgramGrid: document.querySelector("#cnc-program-grid"),
  cncSpindleGrid: document.querySelector("#cnc-spindle-grid"),
  cncMotionGrid: document.querySelector("#cnc-motion-grid"),
  cncProbeProductionGrid: document.querySelector("#cnc-probe-production-grid"),
  cncAtcGrid: document.querySelector("#cnc-atc-grid"),
  cncAtcSlots: document.querySelector("#cnc-atc-slots"),
  cncToolTableRows: document.querySelector("#cnc-tool-table-rows"),
  cncDigitalInputs: document.querySelector("#cnc-digital-inputs"),
  cncDigitalOutputs: document.querySelector("#cnc-digital-outputs"),
  cncAnalogInputs: document.querySelector("#cnc-analog-inputs"),
  cncAnalogOutputs: document.querySelector("#cnc-analog-outputs"),
};

let supervisorState = null;
let millSupervisorState = null;

function organizeDebuggingPage() {
  const grid = document.querySelector("#debug-robot .debug-grid");
  const articles = new Map([...grid.querySelectorAll(":scope > .debug-section")].map(article => [article.querySelector("h2")?.textContent.trim(), article]));
  const groups = [
    {
      eyebrow: "Robot operations",
      title: "Manual test controls",
      description: "Run controller programs and test pallet transfers.",
      panels: ["Program run buttons", "Pick or place a pool pallet", "Load or unload the mill", "Queue reliability test"],
      wide: ["Program run buttons"],
    },
    {
      eyebrow: "Controller I/O",
      title: "Signals and field values",
      description: "Digital faceplate plus raw analog channels.",
      panels: ["Digital I/O faceplate", "Analog and I/O values", "Analog outputs"],
      wide: ["Digital I/O faceplate"],
    },
    {
      eyebrow: "Motion telemetry",
      title: "Robot position and runtime",
      description: "TCP, joint, state, and additional realtime values.",
      panels: ["TCP pose, speed, and force", "Joint positions, velocities, and current", "Robot state", "Pose and joints", "Additional actual values"],
      wide: ["TCP pose, speed, and force", "Joint positions, velocities, and current"],
    },
  ];
  groups.forEach(group => {
    const section = document.createElement("section");
    section.className = "debug-subgroup";
    section.innerHTML = `<header class="debug-subgroup-heading"><div><p>${group.eyebrow}</p><h3>${group.title}</h3></div><span>${group.description}</span></header><div class="debug-subgroup-grid"></div>`;
    const container = section.querySelector(".debug-subgroup-grid");
    group.panels.forEach(title => {
      const article = articles.get(title);
      if (!article) return;
      article.classList.toggle("debug-section-wide", group.wide.includes(title));
      container.append(article);
    });
    grid.append(section);
  });

  const cnc = document.querySelector(".cnc-debug-section");
  const connectionDeck = document.querySelector("#debug-connection-deck");
  const systemOverview = document.querySelector("#debug-system .debug-overview");
  // Promote the immediately useful system cards ahead of controller detail.
  // The diagnostic timeline remains in the System section below.
  if (systemOverview) {
    [...systemOverview.children].slice(0, 3).forEach(card => {
      card.classList.add("debug-connection-card");
      connectionDeck.append(card);
    });
  }
  const robotConnection = articles.get("Persistent robot-originated connection");
  if (robotConnection) {
    robotConnection.classList.remove("debug-section-wide");
    robotConnection.classList.add("debug-connection-card");
    connectionDeck.append(robotConnection);
  }
  const millConnectionHeading = [...cnc.querySelectorAll(":scope > .cnc-subsection-heading")]
    .find(heading => heading.querySelector("h3")?.textContent.trim() === "Mill supervisor");
  if (millConnectionHeading) {
    const millConnection = document.createElement("article");
    millConnection.className = "debug-connection-card mill-connection-card";
    connectionDeck.append(millConnection);
    let node = millConnectionHeading;
    while (node) {
      const next = node.nextSibling;
      millConnection.append(node);
      if (next?.classList?.contains("cnc-subsection-heading")) break;
      node = next;
    }
  }
  [...cnc.querySelectorAll(":scope > .cnc-subsection-heading")].forEach((heading, index) => {
    const title = heading.querySelector("h3")?.textContent.trim() || "Details";
    const details = document.createElement("details");
    details.className = "debug-disclosure";
    details.open = ["Health and limits", "Execution and modes", "Motion and offsets"].includes(title);
    const summary = document.createElement("summary");
    summary.className = "cnc-subsection-heading";
    while (heading.firstChild) summary.append(heading.firstChild);
    heading.replaceWith(details);
    details.append(summary);
    let sibling = details.nextSibling;
    while (sibling && !sibling.classList?.contains("cnc-subsection-heading")) {
      const next = sibling.nextSibling;
      details.append(sibling);
      sibling = next;
    }
    summary.setAttribute("aria-label", `${title} details`);
  });
}

organizeDebuggingPage();

let lastError = "";
let snapshotState = null;
let editingProgramIndex = null;
let editingMillProgramIndex = null;
let latestCncSnapshot = null;
let cncIoLabels = {digital_inputs: {}, digital_outputs: {}, analog_inputs: {}, analog_outputs: {}};
let palletMotionSettings = null;
let millPalletMotionReady = false;

async function loadRobotProgramsNav() {
  try {
    const result = await api("/api/settings");
    document.querySelectorAll("[data-robot-programs-nav]").forEach(link => {
      link.classList.toggle("hidden", !result.settings.robot_programs_page_enabled);
    });
    document.querySelectorAll("[data-mill-programs-nav]").forEach(link => {
      link.classList.toggle("hidden", !result.settings.mill_programs_page_enabled);
    });
  } catch {
    // Keep the link hidden when settings cannot be read.
  }
}

async function loadPalletMotionTestSettings() {
  try {
    const result = await api("/api/settings");
    palletMotionSettings = result.settings;
    const count = Number(result.settings.pool_slot_count || 0);
    ui.palletMotionSlot.innerHTML = Array.from({length: count}, (_, index) => {
      const slot = index + 1;
      return `<option value="${slot}">Pool ${String(slot).padStart(2, "0")}</option>`;
    }).join("");
    const ready = result.settings.pallet_motion_enabled;
    const millPosesReady = Boolean(
      result.settings.robot_mill_load_unload
        && result.settings.pallet_motion_generation?.mill_pre_entry_waypoint
        && result.settings.robot_mill_safe_entry_exit,
    );
    millPalletMotionReady = ready && millPosesReady;
    ui.palletMotionPick.disabled = !ready;
    ui.palletMotionPlace.disabled = !ready;
    ui.millMotionLoad.disabled = !millPalletMotionReady;
    ui.millMotionUnload.disabled = !millPalletMotionReady;
    ui.palletMotionStatus.textContent = ready
      ? "Ready for a manual test. This does not update the schedule."
      : "Enable physical pallet movements in Settings to use this tool.";
    ui.millMotionStatus.textContent = !ready
      ? "Enable physical pallet movements in Settings to use this tool."
      : !millPosesReady
        ? "Configure and save the robot mill load/unload, pre-entry, and safe entry/exit poses in Settings first."
        : "Ready. Rebuild generated scripts after changing either mill robot pose.";
  } catch (error) {
    ui.palletMotionStatus.textContent = `Pallet-motion test unavailable: ${error.message}`;
    ui.millMotionStatus.textContent = `Mill-transfer test unavailable: ${error.message}`;
    ui.palletMotionPick.disabled = true;
    ui.palletMotionPlace.disabled = true;
    ui.millMotionLoad.disabled = true;
    ui.millMotionUnload.disabled = true;
    millPalletMotionReady = false;
  }
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

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "Unavailable";
  if (typeof value === "boolean") return value ? "ON" : "OFF";
  if (typeof value === "number" && !Number.isInteger(value)) return value.toFixed(4);
  return String(value);
}

function hasReportedValue(value) {
  if (value === null || value === undefined || value === "") return false;
  if (typeof value === "string" && value.trim().toLowerCase() === "unavailable") return false;
  if (Array.isArray(value)) return value.some(hasReportedValue);
  return true;
}

function tableEmpty(columns, label) {
  return `<tr><td colspan="${columns}" class="debug-table-empty">${escapeHtml(label)}</td></tr>`;
}

function cncAxisRow(row) {
  const home = row.homed === null || row.homed === undefined ? "Unavailable" : (row.homed ? "Homed" : "Not homed");
  const limit = row.limit === null || row.limit === undefined ? "Unavailable" : (Number(row.limit) === 0 ? "Clear" : `Active (${formatValue(row.limit)})`);
  return `<tr><td>${escapeHtml(row.axis)}</td><td>${escapeHtml(formatValue(row.position))}</td><td>${escapeHtml(formatValue(row.commanded))}</td><td>${escapeHtml(formatValue(row.velocity))}</td><td>${escapeHtml(formatValue(row.distance_to_go))}</td><td>${escapeHtml(home)}</td><td>${escapeHtml(limit)}</td></tr>`;
}

function cncDataGrid(values) {
  const reported = values.filter(([, value]) => hasReportedValue(value));
  return reported.length
    ? reported.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(formatValue(value))}</strong></article>`).join("")
    : '<p class="debug-table-empty">This controller does not report values for this group.</p>';
}

function boolState(value, on, off) {
  if (value === null || value === undefined) return "Unavailable";
  return value ? on : off;
}

function percentage(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(0)}%` : "Unavailable";
}

function axisValues(values) {
  const axes = ["X", "Y", "Z", "A", "B", "C", "U", "V", "W"];
  if (!Array.isArray(values) || !values.length) return "Unavailable";
  return values.map((value, index) => `${axes[index] || index}: ${formatValue(value)}`).join(" | ");
}

function g5xLabel(index) {
  if (!Number.isInteger(index)) return "Unavailable";
  if (index >= 1 && index <= 6) return `G${53 + index}`;
  if (index >= 7 && index <= 9) return `G59.${index - 6}`;
  return `G54.1 P${index - 9}`;
}

function humanizeIoSignal(signal) {
  if (!signal) return "Unassigned";
  const aliases = {atc: "ATC", vfd: "VFD", io: "I/O", ngc: "NGC", tsc: "TSC", dbbutton: "Tool change", dig: "Digital", in: "input", out: "output", ref: "reference"};
  return String(signal)
    .replace(/^dbbutton-/, "")
    .replace(/trayref/g, "tray-reference")
    .replace(/draw-bar/g, "drawbar")
    .replace(/dig-(in|out)-/g, "digital-$1-")
    .split(/[._-]+/)
    .filter(Boolean)
    .map(part => aliases[part.toLowerCase()] || `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function ioLabel(labels, index) {
  return labels?.[String(index)] || labels?.[index] || "";
}

function cncDigitalTiles(values, labels, prefix) {
  if (!Array.isArray(values) || !values.length) return '<p class="debug-table-empty">No channel values reported.</p>';
  return values.map((value, index) => {
    const signal = ioLabel(labels, index);
    return `<article class="cnc-digital-tile ${value ? "active" : "idle"}" title="${escapeHtml(signal || "No named HAL signal")}"><span>${prefix}${String(index).padStart(2, "0")}</span>${ledCell(value)}<strong>${escapeHtml(humanizeIoSignal(signal))}</strong><small>${value ? "ON" : "OFF"}</small></article>`;
  }).join("");
}

function cncAnalogRows(values, labels, prefix) {
  if (!Array.isArray(values) || !values.length) return tableEmpty(3, "No channel values reported.");
  const reported = values.map((value, index) => [value, index]).filter(([value]) => hasReportedValue(value));
  return reported.length
    ? reported.map(([value, index]) => `<tr><td>${prefix}${String(index).padStart(2, "0")}</td><td>${escapeHtml(humanizeIoSignal(ioLabel(labels, index)))}</td><td>${escapeHtml(formatValue(value))}</td></tr>`).join("")
    : tableEmpty(3, "No channel values reported.");
}

function renderCnc(snapshot) {
  ui.cncConnectionLight.className = `debug-connection-light ${snapshot.connected ? "active" : "unknown"}`;
  ui.cncConnectionLabel.textContent = snapshot.connection_label;
  ui.cncNotes.textContent = snapshot.notes;
  const summaryValues = [
    [ui.cncControllerState, snapshot.controller_state],
    [ui.cncProgram, snapshot.program],
    [ui.cncSpindle, snapshot.spindle],
    [ui.cncCoolant, snapshot.coolant],
    [ui.cncFeedOverride, snapshot.feed_override],
    [ui.cncTimestamp, snapshot.timestamp ? new Date(snapshot.timestamp).toLocaleString() : null],
  ];
  summaryValues.forEach(([element, value]) => {
    element.textContent = formatValue(value);
    element.closest("article").hidden = !hasReportedValue(value);
  });
  renderMillProgramControls(snapshot);
  const axisRows = snapshot.axis_rows || [];
  ui.cncAxisRows.innerHTML = axisRows.length
    ? axisRows.map(cncAxisRow).join("")
    : tableEmpty(7, "Axis telemetry will appear after the PathPilot/LinuxCNC connection is configured.");
  const health = snapshot.health || {};
  const motion = snapshot.motion || {};
  const coordinates = snapshot.coordinates || {};
  const execution = snapshot.program_execution || {};
  const spindle = snapshot.spindle_details || {};
  const probe = snapshot.probe || {};
  const tooling = snapshot.tooling || {};
  const production = snapshot.production || {};
  const io = snapshot.io || {};
  ui.cncHealthGrid.innerHTML = cncDataGrid([
    ["E-stop", boolState(health.estop, "Active", "Reset")],
    ["Machine enabled", boolState(health.enabled, "Enabled", "Disabled")],
    ["In position", boolState(health.in_position, "Yes", "No")],
    ["Homed axes", axisValues(health.homed)],
    ["Axis limits", axisValues(health.limits)],
    ["Lubrication", boolState(health.lube_active, "Active", "Inactive")],
    ["Lube level", boolState(health.lube_level_warning, "Warning", "OK")],
    ["Interpreter error", health.interpreter_error],
  ]);
  ui.cncProgramGrid.innerHTML = cncDataGrid([
    ["Controller state", execution.state],
    ["Execution state", execution.exec_state],
    ["Current line", execution.read_line],
    ["Read-ahead line", execution.readahead_line],
    ["Active queue", execution.active_queue],
    ["Queued motions", execution.queue],
    ["Queue full", boolState(execution.queue_full, "Yes", "No")],
    ["Dwell remaining", execution.dwell_remaining],
    ["Optional stop", boolState(execution.optional_stop, "On", "Off")],
    ["Block delete", boolState(execution.block_delete, "On", "Off")],
    ["Adaptive feed", boolState(execution.adaptive_feed, "On", "Off")],
    ["Feed hold available", boolState(execution.feed_hold_enabled, "Yes", "No")],
    ["Active G-codes", (execution.g_codes || []).join(" ") || "None"],
    ["Active M-codes", (execution.m_codes || []).join(" ") || "None"],
  ]);
  const direction = {"-1": "Reverse", "0": "Stopped", "1": "Forward"};
  ui.cncSpindleGrid.innerHTML = cncDataGrid([
    ["Commanded RPM", spindle.commanded_speed],
    ["Feedback RPM", spindle.feedback_speed],
    ["Spindle", boolState(spindle.enabled, "Enabled", "Stopped")],
    ["Direction", direction[String(spindle.direction)] || spindle.direction],
    ["Brake", boolState(spindle.brake, "Engaged", "Released")],
    ["Spindle override", percentage(spindle.spindle_override)],
    ["Rapid override", percentage(spindle.rapid_override)],
    ["Feed override", percentage(spindle.feed_override)],
  ]);
  ui.cncMotionGrid.innerHTML = cncDataGrid([
    ["Distance to go", motion.distance_to_go],
    ["Current velocity", motion.current_velocity],
    ["Commanded velocity", motion.velocity],
    ["Acceleration", motion.acceleration],
    ["Motion mode", motion.motion_mode],
    ["Work coordinate", g5xLabel(coordinates.g5x_index)],
    ["G5X offset", axisValues(coordinates.g5x_offset)],
    ["G92 offset", axisValues(coordinates.g92_offset)],
    ["XY rotation", coordinates.rotation_xy],
    ["Program units", {1: "Inch", 2: "Millimeter", 3: "Centimeter"}[coordinates.program_units] || coordinates.program_units],
    ["Linear units", coordinates.linear_units],
    ["Angular units", coordinates.angular_units],
  ]);
  ui.cncProbeProductionGrid.innerHTML = cncDataGrid([
    ["Probe", boolState(probe.tripped, "Tripped", "Clear")],
    ["Probe input", probe.value],
    ["Last probe position", axisValues(probe.last_position)],
    ["Spindle tool", tooling.tool_in_spindle ? `T${tooling.tool_in_spindle}` : "No tool"],
    ["Prepared pocket", tooling.prepared_pocket],
    ["Tool offset number", tooling.tool_offset_number],
    ["Tool offset", axisValues(tooling.tool_offset)],
    ["Cycle time", production.cycle_time],
    ["M30 counter A", production.m30_a],
    ["M30 counter B", production.m30_b],
    ["M99 counter A", production.m99_a],
    ["M99 counter B", production.m99_b],
  ]);
  const atc = snapshot.atc || {};
  const atcValues = [
    ["ATC position", atc.current_position],
    ["HAL carousel value", atc.carousel_slot],
    ["Spindle tool", atc.tool_number == null ? null : (atc.tool_number ? `T${atc.tool_number}` : "No tool")],
    ["Prepared tool", atc.prepared_tool == null ? null : (atc.prepared_tool ? `T${atc.prepared_tool}` : "None")],
    ["Tool change", atc.change_in_progress == null ? null : (atc.change_in_progress ? "In progress" : "Idle")],
    ["Tray", atc.tray_in == null ? null : (atc.tray_in ? "In" : "Out")],
    ["ATC device", atc.device_ready == null ? null : (atc.device_ready ? "Ready" : "Not ready")],
    ["Tray reference", atc.tray_referenced == null ? null : (atc.tray_referenced ? "Referenced" : "Not referenced")],
    ["Air pressure", atc.pressure_ok == null ? null : (atc.pressure_ok ? "OK" : "Not OK")],
    ["Drawbar", boolState(atc.drawbar_engaged, "Engaged", "Released")],
    ["ATC lock", boolState(atc.lock_engaged, "Engaged", "Released")],
    ["ATC VFD", boolState(atc.vfd_status, "Active", "Idle")],
    ["ATC busy", boolState(atc.busy, "Busy", "Idle")],
    ["ATC return code", atc.return_code],
    ["Tray capacity", atc.tray_capacity],
  ];
  ui.cncAtcGrid.innerHTML = cncDataGrid(atcValues);
  const slots = atc.slots || [];
  ui.cncAtcSlots.innerHTML = slots.length
    ? slots.map(slot => {
      const geometry = slot.tool_number
        ? `Length ${formatValue(slot.length_offset)} / Dia ${formatValue(slot.diameter)}`
        : "Available";
      return `<article class="cnc-atc-slot ${slot.current ? "current" : ""} ${slot.tool_number ? "loaded" : "empty"}"><span>Position ${escapeHtml(slot.position)}</span><strong>${slot.tool_number ? `T${escapeHtml(slot.tool_number)}` : "Empty"}</strong><small>${escapeHtml(geometry)}</small>${slot.current ? '<b>Current</b>' : ""}</article>`;
    }).join("")
    : '<p class="debug-table-empty">PathPilot carousel assignments are unavailable.</p>';
  const tools = (snapshot.tool_table || []).filter(tool => tool.length_offset !== null && tool.length_offset !== undefined);
  const loadedToolNumbers = new Set(slots.filter(slot => slot.tool_number).map(slot => Number(slot.tool_number)));
  ui.cncToolTableRows.innerHTML = tools.length
    ? tools.map(tool => {
      const status = loadedToolNumbers.has(Number(tool.tool_number))
        ? "atc"
        : (Math.abs(Number(tool.length_offset)) > 1e-9 ? "measured" : "zero");
      return `<tr class="tool-status-${status}"><td>T${escapeHtml(tool.tool_number)}</td><td>${escapeHtml(formatValue(tool.diameter))}</td><td>${escapeHtml(formatValue(tool.length_offset))}</td></tr>`;
    }).join("")
    : tableEmpty(3, "No PathPilot tool table entries have a length offset.");
  ui.cncDigitalInputs.innerHTML = cncDigitalTiles(io.digital_inputs, cncIoLabels.digital_inputs, "DI");
  ui.cncDigitalOutputs.innerHTML = cncDigitalTiles(io.digital_outputs, cncIoLabels.digital_outputs, "DO");
  ui.cncAnalogInputs.innerHTML = cncAnalogRows(io.analog_inputs, cncIoLabels.analog_inputs, "AI");
  ui.cncAnalogOutputs.innerHTML = cncAnalogRows(io.analog_outputs, cncIoLabels.analog_outputs, "AO");
}

function renderMillProgramControls(snapshot) {
  const controls = snapshot.mill_program_controls || {buttons: [], file_list_note: "Mill program controls are unavailable."};
  ui.millProgramFileNote.textContent = controls.file_list_note;
  ui.millProgramButtons.innerHTML = controls.buttons.length
    ? controls.buttons.map(button => `
      <div class="debug-program-card program-${escapeHtml(button.color)}">
        <button class="debug-program-run" type="button" data-run-debug-mill-program="${button.index}" ${button.can_run ? "" : "disabled"}>
          <span class="debug-io-title"><span class="debug-led ${button.can_run ? "active" : "unknown"}"></span><span class="debug-io-name">${escapeHtml(button.display_name)}</span></span>
          <span class="debug-io-meta">${escapeHtml(button.filename || "No mill file configured")}</span>
        </button>
        <button class="debug-io-rename" type="button" data-edit-debug-mill-program="${button.index}">Edit Mill</button>
      </div>`).join("")
    : `<p class="debug-table-empty">Enable one or more Mill program buttons in Settings.</p>`;
}

function ledCell(value) {
  const stateClass = value === null || value === undefined
    ? "unknown"
    : (value ? "active" : "idle");
  const label = value === null || value === undefined
    ? "Unavailable"
    : (value ? "On" : "Off");
  return `<span class="debug-led ${stateClass}" title="${label}" aria-label="${label}"></span>`;
}

function digitalTile(row) {
  const disabled = !row.writable;
  const stateLabel = formatValue(row.value);
  const voltageLabel = row.value ? "24V" : "0V";
  const buttonClass = [
    "debug-io-toggle",
    row.value ? "active" : "idle",
    disabled ? "readonly" : "writable",
  ].join(" ");
  return `
    <div class="debug-io-card">
      <button class="${buttonClass}" type="button"
        data-toggle-debug-io="true"
        data-debug-direction="${escapeHtml(row.direction)}"
        data-debug-bank="${escapeHtml(row.bank)}"
        data-debug-index="${escapeHtml(row.index)}"
        ${disabled ? "disabled" : ""}>
        <span class="debug-io-title">
          ${ledCell(row.value)}
          <span class="debug-io-name">${escapeHtml(row.label || row.channel)}</span>
        </span>
        <span class="debug-io-meta">${escapeHtml(row.channel)} · ${escapeHtml(voltageLabel)} · ${escapeHtml(stateLabel)}</span>
      </button>
      <button
        class="debug-io-rename"
        type="button"
        data-rename-debug-io="true"
        data-debug-direction="${escapeHtml(row.direction)}"
        data-debug-bank="${escapeHtml(row.bank)}"
        data-debug-index="${escapeHtml(row.index)}"
        data-debug-label="${escapeHtml(row.label || row.channel)}">
        Rename
      </button>
    </div>`;
}

function digitalGroup(group, toneClass) {
  return `
    <section class="debug-io-bank ${toneClass}">
      <header>
        <h3>${escapeHtml(group.title)}</h3>
      </header>
      <div class="debug-io-bank-grid">
        ${group.rows.map(digitalTile).join("")}
      </div>
    </section>`;
}

function analogPreviewCard(row, toneClass) {
  return `
    <div class="debug-faceplate-cell debug-faceplate-analog ${toneClass}">
      <span class="debug-faceplate-code">${escapeHtml(row.channel)}</span>
      <strong>${escapeHtml(formatValue(row.value))}</strong>
    </div>`;
}

function faceplatePort(row, toneClass) {
  const disabled = !row.writable;
  const stateLabel = formatValue(row.value);
  const voltageLabel = row.value ? "24V" : "0V";
  return `
    <div class="debug-faceplate-cell ${toneClass} ${row.value ? "active" : "idle"} ${disabled ? "readonly" : "writable"}">
      <button class="debug-faceplate-port" type="button"
        data-toggle-debug-io="true"
        data-debug-direction="${escapeHtml(row.direction)}"
        data-debug-bank="${escapeHtml(row.bank)}"
        data-debug-index="${escapeHtml(row.index)}"
        ${disabled ? "disabled" : ""}>
        <span class="debug-faceplate-name-row">
          ${ledCell(row.value)}
          <span class="debug-faceplate-name">${escapeHtml(row.label || row.channel)}</span>
        </span>
        <span class="debug-faceplate-code">${escapeHtml(row.channel)} · ${escapeHtml(voltageLabel)} · ${escapeHtml(stateLabel)}</span>
      </button>
      <button
        class="debug-faceplate-edit"
        type="button"
        data-rename-debug-io="true"
        data-debug-direction="${escapeHtml(row.direction)}"
        data-debug-bank="${escapeHtml(row.bank)}"
        data-debug-index="${escapeHtml(row.index)}"
        data-debug-label="${escapeHtml(row.label || row.channel)}">
        Edit
      </button>
    </div>`;
}

function blankFaceplateCell(toneClass = "signal-gray") {
  return `<div class="debug-faceplate-cell ${toneClass} blank" aria-hidden="true"></div>`;
}

function faceplateColumn(title, rows, toneClass, targetRows = rows.length) {
  const filledRows = [...rows];
  while (filledRows.length < targetRows) {
    filledRows.push(null);
  }
  return `
    <section class="debug-faceplate-column ${toneClass}">
      <header>${escapeHtml(title)}</header>
      <div class="debug-faceplate-stack">
        ${filledRows.map(row => row ? faceplatePort(row, toneClass) : blankFaceplateCell(toneClass)).join("")}
      </div>
    </section>`;
}

function analogColumn(analogInputs, analogOutputs, targetRows) {
  const rows = [
    ...analogInputs.map(row => analogPreviewCard(row, "signal-teal")),
    ...analogOutputs.map(row => analogPreviewCard(row, "signal-green")),
  ];
  while (rows.length < targetRows) {
    rows.push(blankFaceplateCell("signal-teal"));
  }
  return `
    <section class="debug-faceplate-column signal-teal">
      <header>Analog</header>
      <div class="debug-faceplate-stack">
        ${rows.join("")}
      </div>
    </section>`;
}

function renderControllerLayout(snapshot) {
  const groupsByTitle = Object.fromEntries(
    [...snapshot.digital_input_groups, ...snapshot.digital_output_groups].map(group => [group.title, group]),
  );
  const targetRows = 8;
  const columns = [
    faceplateColumn("Tool inputs", groupsByTitle["Tool inputs"]?.rows || [], "signal-red", targetRows),
    faceplateColumn("Tool outputs", groupsByTitle["Tool outputs"]?.rows || [], "signal-red", targetRows),
    faceplateColumn("Configurable inputs", groupsByTitle["Configurable inputs"]?.rows || [], "signal-yellow", targetRows),
    faceplateColumn("Configurable outputs", groupsByTitle["Configurable outputs"]?.rows || [], "signal-yellow", targetRows),
    faceplateColumn("Digital inputs", groupsByTitle["Standard inputs"]?.rows || [], "signal-gray", targetRows),
    faceplateColumn("Digital outputs", groupsByTitle["Standard outputs"]?.rows || [], "signal-gray", targetRows),
    analogColumn(snapshot.analog_inputs, snapshot.analog_outputs, targetRows),
  ];

  ui.digitalIoLayout.innerHTML = `
    <div class="debug-faceplate-grid">
      ${columns.join("")}
    </div>`;
}

function analogRow(row) {
  return `
    <tr>
      <td>${escapeHtml(row.channel)}</td>
      <td>${escapeHtml(row.label)}</td>
      <td>${escapeHtml(formatValue(row.value))}</td>
      <td>${row.mode_bit === null || row.mode_bit === undefined ? "n/a" : escapeHtml(row.mode_bit)}</td>
      <td>${row.mode_mask === null || row.mode_mask === undefined ? "n/a" : escapeHtml(row.mode_mask)}</td>
    </tr>`;
}

function stateRow(row) {
  return `
    <tr>
      <td>${escapeHtml(row.label)}</td>
      <td>${escapeHtml(formatValue(row.value))}</td>
    </tr>`;
}

function motionRow(row) {
  return `
    <tr>
      <td>${escapeHtml(row.channel)}</td>
      <td>${escapeHtml(row.label)}</td>
      <td>${escapeHtml(formatValue(row.value))}</td>
    </tr>`;
}

function tcpDetailRow(row) {
  return `
    <tr>
      <td>${escapeHtml(row.axis)}</td>
      <td>${escapeHtml(formatValue(row.actual_pose))}</td>
      <td>${escapeHtml(formatValue(row.actual_speed))}</td>
      <td>${escapeHtml(formatValue(row.actual_force))}</td>
      <td>${escapeHtml(formatValue(row.target_pose))}</td>
      <td>${escapeHtml(formatValue(row.target_speed))}</td>
    </tr>`;
}

function jointDetailRow(row) {
  return `
    <tr>
      <td>${escapeHtml(row.joint)}</td>
      <td>${escapeHtml(formatValue(row.actual_position))}</td>
      <td>${escapeHtml(formatValue(row.actual_velocity))}</td>
      <td>${escapeHtml(formatValue(row.actual_current))}</td>
      <td>${escapeHtml(formatValue(row.target_position))}</td>
      <td>${escapeHtml(formatValue(row.target_velocity))}</td>
      <td>${escapeHtml(formatValue(row.target_current))}</td>
    </tr>`;
}

function renderProgramControls(snapshot) {
  const controls = snapshot.program_controls || {buttons: [], loaded_program: null, file_list_note: "Program controls are unavailable."};
  ui.loadedControllerProgram.textContent = controls.loaded_program || "No program reported";
  ui.programFileNote.textContent = controls.file_list_note;
  ui.programButtons.innerHTML = controls.buttons.length
    ? controls.buttons.map(button => `
      <div class="debug-program-card program-${escapeHtml(button.color)}">
        <button class="debug-program-run" type="button" data-run-debug-program="${button.index}" ${button.can_run ? "" : "disabled"}>
          <span class="debug-io-title"><span class="debug-led ${button.can_run ? "active" : "unknown"}"></span><span class="debug-io-name">${escapeHtml(button.display_name)}</span></span>
          <span class="debug-io-meta">${escapeHtml(button.filename || "No controller file configured")}</span>
        </button>
        <button class="debug-io-rename" type="button" data-edit-debug-program="${button.index}">Edit</button>
      </div>`).join("")
    : `<p class="debug-table-empty">Enable one or more program buttons in Settings.</p>`;
}

function renderSupervisorStatus(status) {
  supervisorState = status;
  const supervisorMode = status.latched
    ? "LATCHED"
    : status.maintenance_mode
      ? "Maintenance"
      : !status.activation_verified
        ? "Awaiting handshake"
        : status.enabled
          ? "Enabled"
          : "Verified / disabled";
  const fields = [
    ["Protocol", status.protocol || "Unavailable"],
    ["Listener", status.listening ? `${status.listen_host}:${status.listen_port}` : "Unavailable"],
    ["Connection", status.connected ? "Connected" : "Disconnected"],
    ["Peer", status.peer || "Unavailable"],
    ["Heartbeat", status.heartbeat_age_seconds == null ? "Unavailable" : `${status.heartbeat_age_seconds}s ago`],
    ["Telemetry", status.telemetry_age_seconds == null ? "Unavailable" : `${status.telemetry_age_seconds}s ago`],
    ["App session", status.app_session ?? "Unavailable"],
    ["Robot session", status.robot_session ?? "Unavailable"],
    ["Sequence", `${status.robot_last_sequence ?? "?"} / expected ${status.expected_sequence ?? "?"}`],
    ["Last event", status.robot_last_event && status.robot_last_event !== 0 ? status.robot_last_event : null],
    ["Status document", status.status_document?.written_at ? `Updated ${new Date(status.status_document.written_at).toLocaleTimeString()}` : status.status_document?.error],
    ["State", supervisorMode],
  ].filter(([label, value]) => label === "Connection" || label === "State" || hasReportedValue(value));
  ui.supervisorStatusGrid.innerHTML = fields.map(([label, value]) => `
    <article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>
  `).join("");
  ui.supervisorDetail.className = `network-test-result ${status.reconciliation_required ? "error" : status.connected ? "healthy" : "warning"}`;
  ui.supervisorDetail.textContent = status.reconciliation_required
    ? "Reconciliation required. Use Reconcile command to record the observed physical result. If the pallet location differs from the board, use the Schedule page recovery dialog next."
    : status.connected
      ? `Supervisor handshake is live${status.enabled ? " and commands are enabled" : ", but command routing is not enabled"}.`
      : (status.last_disconnect_detail || "Mongo has not connected to the backend listener.");
  const commands = status.commands || [];
  ui.supervisorCommandRows.innerHTML = commands.length ? commands.map(command => `
    <tr>
      <td>${escapeHtml(command.sequence)}</td>
      <td>${escapeHtml(command.operation)}</td>
      <td>${escapeHtml(command.transport)}</td>
      <td>${escapeHtml(command.status)}</td>
      <td>${escapeHtml(command.fault_detail || command.result_code || "-")}</td>
    </tr>
  `).join("") : tableEmpty(5, "No supervisor commands have been dispatched.");
  ui.supervisorMaintenance.textContent = status.maintenance_mode ? "Exit Maintenance Mode" : "Enter Maintenance Mode";
  ui.supervisorMaintenance.disabled = !status.activation_verified && !status.maintenance_mode;
  ui.supervisorClearLatch.textContent = status.reconciliation_required ? "Reconcile command" : "Clear supervisor latch";
  ui.supervisorClearLatch.disabled = !status.latched && !status.reconciliation_required;
}

function renderDiagnosticTimeline(snapshot) {
  const events = [...(snapshot.events || [])].reverse().slice(0, 12);
  ui.diagnosticEventList.innerHTML = events.length ? events.map(item => `
    <article class="diagnostic-event severity-${escapeHtml(item.severity)}">
      <span>${escapeHtml(new Date(item.timestamp).toLocaleTimeString())}</span>
      <strong>${escapeHtml(item.component)} / ${escapeHtml(item.event)}</strong>
      <p>${escapeHtml(item.message)}</p>
      ${item.correlation_id ? `<code>${escapeHtml(item.correlation_id)}</code>` : ""}
    </article>
  `).join("") : `<p class="debug-table-empty">No diagnostic events have been recorded.</p>`;
}

async function loadDiagnostics() {
  try {
    renderDiagnosticTimeline(await api("/api/debug/diagnostics?limit=50", {cache: "no-store"}));
  } catch (error) {
    ui.diagnosticEventList.textContent = `Diagnostics unavailable: ${error.message}`;
  }
}

async function loadSupervisorDebugging() {
  try {
    renderSupervisorStatus(await api("/api/debug/robot-supervisor", {cache: "no-store"}));
  } catch (error) {
    ui.supervisorDetail.className = "network-test-result error";
    ui.supervisorDetail.textContent = error.message;
  }
}

function renderReliabilityTest(result) {
  const active = result.active;
  const run = active || result.latest;
  ui.reliabilityStart.disabled = Boolean(active);
  ui.reliabilityCancel.disabled = !active || active.cancel_requested;
  if (!run) {
    ui.reliabilityStatus.className = "network-test-result";
    ui.reliabilityStatus.textContent = "No reliability test has been run. The current production queue will be captured when Start is pressed.";
    ui.reliabilityQueue.innerHTML = "";
    return;
  }
  const labels = {
    requested: "Starting",
    running: "Running",
    completed: "Completed",
    cancelled: "Cancelled",
    faulted: "Faulted",
    interrupted: "Interrupted",
  };
  const current = run.current_pallet_name
    ? ` Current: ${run.current_pallet_name} from Pool ${String(run.current_pool_slot).padStart(2, "0")}.`
    : "";
  const stopping = run.cancel_requested ? " Stop requested; the active pallet will be returned first." : "";
  ui.reliabilityStatus.className = `network-test-result ${["faulted", "interrupted"].includes(run.status) ? "error" : run.status === "completed" ? "healthy" : "warning"}`;
  ui.reliabilityStatus.innerHTML = `<strong>${escapeHtml(labels[run.status] || run.status)} · ${run.completed_pallets}/${run.total_pallets} pallets complete</strong><span>${escapeHtml(run.failure_detail || current + stopping || "Queue snapshot captured.")}</span>`;
  const queue = run.queue_snapshot || [];
  ui.reliabilityQueue.innerHTML = queue.map((item, index) => {
    const complete = index < run.completed_pallets;
    const currentItem = run.current_index === index;
    return `<article class="reliability-queue-item ${complete ? "complete" : currentItem ? "active" : ""}"><span>${index + 1}</span><strong>${escapeHtml(item.pallet_name)}</strong><small>Pool ${String(item.pool_slot).padStart(2, "0")}</small><em>${complete ? "Returned" : currentItem ? "In progress" : "Pending"}</em></article>`;
  }).join("");
}

async function loadReliabilityTest() {
  try {
    renderReliabilityTest(await api("/api/debug/reliability-test", {cache: "no-store"}));
  } catch (error) {
    ui.reliabilityStatus.className = "network-test-result error";
    ui.reliabilityStatus.textContent = error.message;
  }
}

ui.reliabilityStart.addEventListener("click", async () => {
  if (!snapshotState) return;
  if (await window.mpsConfirm({
    eyebrow: "Robot validation",
    title: "Start reliability test?",
    message: "Each queued pallet will be picked, moved only to the outer mill staging waypoint, and returned to the same Pool position. The mill door and Erowa will not be operated.",
    tone: "warning",
    primaryLabel: "Start test",
  }) !== "primary") return;
  ui.reliabilityStart.disabled = true;
  try {
    renderReliabilityTest(await api("/api/debug/reliability-test", {
      method: "POST",
      body: JSON.stringify({expected_revision: snapshotState.revision}),
    }));
    showToast("Queue reliability test started.");
  } catch (error) {
    showToast(error.message, "error");
    await loadReliabilityTest();
  }
});

ui.reliabilityCancel.addEventListener("click", async () => {
  ui.reliabilityCancel.disabled = true;
  try {
    renderReliabilityTest(await api("/api/debug/reliability-test/cancel", {method: "POST", body: "{}"}));
    showToast("Stop requested. The test will end after the current pallet is returned.");
  } catch (error) {
    showToast(error.message, "error");
    await loadReliabilityTest();
  }
});

ui.supervisorMaintenance.addEventListener("click", async () => {
  if (!supervisorState) return;
  const enabling = !supervisorState.maintenance_mode;
  if (enabling && await window.mpsConfirm({
    eyebrow: "Robot supervisor",
    title: "Enter Maintenance Mode?",
    message: "Scheduled supervisor commands will be unavailable until the supervisor is restarted and completes a new handshake.",
    tone: "warning",
    primaryLabel: "Enter Maintenance Mode",
  }) !== "primary") return;
  ui.supervisorMaintenance.disabled = true;
  try {
    const board = await api("/api/board", {cache: "no-store"});
    renderSupervisorStatus(await api("/api/debug/robot-supervisor/maintenance", {
      method: "PUT",
      body: JSON.stringify({expected_revision: board.revision, enabled: enabling}),
    }));
    showToast(enabling ? "Supervisor stopped for Maintenance Mode." : "Supervisor restarted and reconnected.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    await loadSupervisorDebugging();
  }
});

function supervisorCandidate(status = supervisorState) {
  return status?.reconciliation || (status?.commands || [])
    .find(command => ["latched", "uncertain", "faulted", "dispatching", "sent", "accepted", "running"].includes(command.status));
}

async function reconcileSupervisorCandidate(candidate) {
  if (!candidate) return showToast("No supervisor command is awaiting physical confirmation.", "error");
  const pallet = candidate.pallet_name ? ` for pallet ${candidate.pallet_name}` : "";
  const outcome = await window.mpsConfirm({
    eyebrow: "Supervisor recovery",
    title: `Did ${candidate.operation} complete?`,
    message: `Sequence ${candidate.sequence}${pallet} did not receive a confirmed terminal result. Inspect the cell before continuing. Select Completed only if the physical operation finished exactly as intended. Select Failed if it did not complete or you are not certain.`,
    tone: "warning",
    primaryLabel: "Completed",
    secondaryLabel: "Failed / uncertain",
    cancelLabel: "Leave locked",
  });
  if (!outcome) return;
  try {
    const board = await api("/api/board", {cache: "no-store"});
    renderSupervisorStatus(await api("/api/debug/robot-supervisor/reconcile", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: board.revision,
        sequence: candidate.sequence,
        resolution: outcome === "primary" ? "accept_completed" : "mark_faulted",
      }),
    }));
    if (outcome === "secondary" && candidate.motion_id) {
      showToast("Robot command marked failed. Use the Schedule page recovery dialog to record the observed pallet location.", "warning");
    } else {
      showToast("Robot command outcome recorded. The supervisor is ready once no separate pallet-location recovery is pending.");
    }
  } catch (error) {
    showToast(error.message, "error");
  }
  await loadSupervisorDebugging();
}

ui.supervisorClearLatch.addEventListener("click", async () => {
  if (!supervisorState) return;
  const candidate = supervisorCandidate();
  if (candidate) return reconcileSupervisorCandidate(candidate);
  if (await window.mpsConfirm({
    eyebrow: "Supervisor recovery",
    title: "Clear supervisor latch?",
    message: "This sends a no-motion reset command to Mongo. Use it only after all prior commands have been reconciled.",
    tone: "warning",
    primaryLabel: "Clear latch",
  }) !== "primary") return;
  try {
    const board = await api("/api/board", {cache: "no-store"});
    const status = await api("/api/debug/robot-supervisor", {cache: "no-store"});
    const resolved = (status.commands || []).find(command => ["operator_completed", "operator_faulted", "completed"].includes(command.status));
    if (!resolved) throw new Error("No reconciled supervisor command is available to clear.");
    renderSupervisorStatus(await api("/api/debug/robot-supervisor/reconcile", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, sequence: resolved.sequence, resolution: "clear_latch"}),
    }));
    showToast("Supervisor latch cleared.");
  } catch (error) {
    showToast(error.message, "error");
  }
});

function render(snapshot) {
  snapshotState = snapshot;
  ui.notes.textContent = snapshot.notes;
  const connectionClass = snapshot.connection_state === "degraded"
    ? "degraded"
    : (snapshot.connected ? "active" : "unknown");
  ui.connectionLight.className = `debug-connection-light ${connectionClass}`;
  ui.connectionLabel.textContent = snapshot.connection_label || (snapshot.connected ? "Connected" : "Unavailable");
  ui.source.textContent = snapshot.source;
  ui.machineState.textContent = snapshot.machine_state;
  ui.timestamp.textContent = new Date(snapshot.timestamp).toLocaleString();

  ui.summaryMachinePallet.textContent = snapshot.summary.machine_pallet || "None";
  ui.summaryQueueCount.textContent = String(snapshot.summary.queue_count);
  ui.summaryPoolCount.textContent = String(snapshot.summary.pool_count);
  ui.summaryStorageCount.textContent = String(snapshot.summary.storage_count);
  ui.summaryPoolOpen.textContent = String(snapshot.summary.pool_open_positions);

  renderControllerLayout(snapshot);
  renderProgramControls(snapshot);

  ui.analogInputs.innerHTML = snapshot.analog_inputs.length
    ? snapshot.analog_inputs.map(analogRow).join("")
    : tableEmpty(5, "No readable analog inputs.");
  ui.analogOutputs.innerHTML = snapshot.analog_outputs.length
    ? snapshot.analog_outputs.map(analogRow).join("")
    : tableEmpty(5, "No readable analog outputs.");
  ui.stateRows.innerHTML = snapshot.state_rows.length
    ? snapshot.state_rows.map(stateRow).join("")
    : tableEmpty(2, "No readable state values.");

  const motionRows = [
    ...snapshot.pose_rows,
    ...snapshot.tcp_speed_rows,
    ...snapshot.joint_rows,
  ];
  ui.motionRows.innerHTML = motionRows.length
    ? motionRows.map(motionRow).join("")
    : tableEmpty(3, "No motion data in the current telemetry recipe.");
  const tcpDetailRows = snapshot.tcp_detail_rows || [];
  ui.tcpDetailRows.innerHTML = tcpDetailRows.length
    ? tcpDetailRows.map(tcpDetailRow).join("")
    : tableEmpty(6, "TCP detail is not available in the current telemetry recipe.");
  const jointDetailRows = snapshot.joint_detail_rows || [];
  ui.jointDetailRows.innerHTML = jointDetailRows.length
    ? jointDetailRows.map(jointDetailRow).join("")
    : tableEmpty(7, "Joint detail is not available in the current telemetry recipe.");
  ui.actualRows.innerHTML = (snapshot.extra_actual_rows || []).length
    ? snapshot.extra_actual_rows.map(motionRow).join("")
    : tableEmpty(3, "No additional actual values in the current telemetry recipe.");

  ui.state.classList.add("online");
  ui.state.lastChild.textContent = ` Online · ${snapshot.source}`;
}

async function loadDebugging() {
  try {
    const snapshot = await api("/api/debug/robot-io");
    lastError = "";
    render(snapshot);
    if (!snapshot.connected && snapshot.connection_state !== "degraded") void loadNetworkTestStatus();
  } catch (error) {
    ui.state.lastChild.textContent = " Unavailable";
    if (error.message !== lastError) {
      showToast(error.message, "error");
      lastError = error.message;
    }
  }
}

ui.autoRecover.addEventListener("click", async () => {
  if (await window.mpsConfirm({
    eyebrow: "Controller recovery",
    title: "Run automatic recovery?",
    message: "This checks Dashboard and PathPilot read-only health, refreshes stale local transports, restores missing idle supervisors, and reports every action. It never clears a robot fault, guesses a pallet location, interrupts a controller program, or restarts a helper while PathPilot is running.",
    tone: "warning",
    primaryLabel: "Auto recover",
  }) !== "primary") return;
  ui.autoRecover.disabled = true;
  ui.autoRecover.textContent = "Recovering...";
  try {
    const result = await api("/api/debug/controllers/auto-recover", {method: "POST", body: "{}"});
    const summary = (result.results || []).map(item => `${item.controller}: ${item.action}`).join(" | ");
    showToast(summary || "Recovery check completed.", (result.results || []).some(item => item.action === "Deferred" || item.action.includes("unavailable")) ? "warning" : "success");
    await Promise.all([loadDebugging(), loadSupervisorDebugging(), loadCncDebugging(), loadMillSupervisor()]);
    if (result.reconciliation) await reconcileSupervisorCandidate(result.reconciliation);
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    ui.autoRecover.disabled = false;
    ui.autoRecover.textContent = "Auto recover";
  }
});

ui.clearMongoFault.addEventListener("click", async () => {
  if (!snapshotState) {
    showToast("Robot status is not loaded yet.", "error");
    return;
  }
  if (await window.mpsConfirm({
    eyebrow: "Robot fault",
    title: "Clear recorded fault?",
    message: "Inspect the cell and identify the cause first. This will not release an E-stop, power the arm, resume a program, or move the robot.",
    tone: "danger",
    primaryLabel: "Clear fault",
  }) !== "primary") return;
  ui.clearMongoFault.disabled = true;
  ui.clearMongoFault.textContent = "Clearing...";
  try {
    const result = await api("/api/debug/robot-fault/clear", {
      method: "POST",
      body: JSON.stringify({expected_revision: snapshotState.revision, confirmed: true}),
    });
    showToast(result.message);
    await loadDebugging();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    ui.clearMongoFault.disabled = false;
    ui.clearMongoFault.textContent = "Clear robot fault/error";
  }
});

function renderNetworkTest(result) {
  const paths = result.paths || [];
  const statusFor = path => {
    if (path.supported === false) return {label: "NOT AVAILABLE", tone: "neutral"};
    if (path.method === "supervisor-tcp") return path.connected
      ? {label: "CONNECTED", tone: "healthy"}
      : {label: "DISCONNECTED", tone: "error"};
    if (path.error || Number(path.received ?? 0) === 0) return {label: "FAILED", tone: "error"};
    if (Number(path.packet_loss_percent || 0) > 0 || Number(path.average_ms || 0) >= 100) return {label: "DEGRADED", tone: "warning"};
    return {label: "OK", tone: "healthy"};
  };
  const statuses = paths.map(statusFor);
  const failures = statuses.filter(status => status.tone === "error").length;
  const degraded = statuses.filter(status => status.tone === "warning").length;
  ui.networkTestResult.className = `network-test-result network-matrix-result ${failures ? "error" : degraded ? "warning" : "healthy"}`;
  const rows = paths.map((path, index) => {
    const status = statuses[index];
    const route = `${path.source || "Unknown"} -> ${path.target || "Unknown"}`;
    const replies = path.method === "icmp" && path.supported !== false ? `${path.received ?? 0}/${path.sent ?? 0}` : "-";
    const loss = path.method === "icmp" && path.supported !== false ? `${path.packet_loss_percent ?? 100}%` : "-";
    const latency = path.method === "icmp" && path.supported !== false
      ? `${path.minimum_ms ?? "-"} / ${path.average_ms ?? "-"} / ${path.maximum_ms ?? "-"} ms`
      : path.method === "supervisor-tcp" ? "Supervisor TCP" : "Not supported";
    const detail = path.error || path.detail || path.host || "";
    return `<tr class="network-path-${status.tone}">
      <td><strong>${escapeHtml(route)}</strong><small>${escapeHtml(detail)}</small></td>
      <td><span class="network-status network-status-${status.tone}">${escapeHtml(status.label)}</span></td>
      <td>${escapeHtml(replies)}</td><td>${escapeHtml(loss)}</td><td>${escapeHtml(latency)}</td>
    </tr>`;
  }).join("");
  ui.networkTestResult.innerHTML = `
    <div class="network-matrix-summary"><strong>${failures ? `${failures} failed path${failures === 1 ? "" : "s"}` : degraded ? `${degraded} degraded path${degraded === 1 ? "" : "s"}` : "All supported paths healthy"}</strong><span>${escapeHtml(String(result.packet_count || 0))} ICMP packets per supported path. Latency is min / average / max.</span></div>
    <div class="network-matrix-shell"><table class="network-matrix-table"><thead><tr><th>Path</th><th>Status</th><th>Replies</th><th>Loss</th><th>Latency</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function loadNetworkTestStatus() {
  try {
    const status = await api("/api/debug/network-test");
    if (status.active) {
      ui.networkTestResult.className = "network-test-result";
      ui.networkTestResult.textContent = "Automatic network test running after robot telemetry loss. This can take up to one minute.";
      return;
    }
    if (!status.latest) return;
    if (status.latest.result) {
      renderNetworkTest(status.latest.result);
      return;
    }
    ui.networkTestResult.className = "network-test-result error";
    ui.networkTestResult.textContent = status.latest.error || "Network test did not return a result.";
  } catch {
    // The robot diagnostic remains usable if this optional status request fails.
  }
}

ui.networkTest.addEventListener("click", async () => {
  ui.networkTest.disabled = true;
  ui.networkTest.textContent = "Testing network...";
  ui.networkTestResult.className = "network-test-result";
  ui.networkTestResult.textContent = "Testing Desktop and Mill network paths. This can take up to one minute when packets are timing out.";
  try {
    renderNetworkTest(await api("/api/debug/network-test", {method: "POST", body: "{}"}));
  } catch (error) {
    ui.networkTestResult.className = "network-test-result error";
    ui.networkTestResult.textContent = error.message;
  } finally {
    ui.networkTest.disabled = false;
    ui.networkTest.textContent = "Run network matrix";
  }
});

async function loadCncDebugging() {
  try {
    latestCncSnapshot = await api("/api/debug/cnc");
    renderCnc(latestCncSnapshot);
  } catch (error) {
    ui.cncConnectionLight.className = "debug-connection-light unknown";
    ui.cncConnectionLabel.textContent = "Unavailable";
    ui.cncNotes.textContent = `CNC debug data is unavailable: ${error.message}`;
  }
}

function renderMillSupervisor(status) {
  millSupervisorState = status;
  const fields = [
    ["Rollout", status.enabled ? "Active command routing" : "Telemetry only"],
    ["Listener", status.listening ? `${status.listen_host}:${status.listen_port}` : null],
    ["Connection", status.connected ? `Session ${status.mill_session}` : "Disconnected"],
    ["Heartbeat", status.heartbeat_age_seconds == null ? null : `${status.heartbeat_age_seconds}s`],
    ["Telemetry", status.telemetry_age_seconds == null ? null : `${status.telemetry_age_seconds}s`],
    ["Sequence", `${status.mill_last_sequence ?? "?"} / expected ${status.expected_sequence ?? "?"}`],
    ["Last result", status.last_result?.event],
  ].filter(([label, value]) => label === "Connection" || hasReportedValue(value));
  ui.millSupervisorGrid.innerHTML = fields.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></article>`).join("");
  ui.millSupervisorDetail.className = `network-test-result ${status.reconciliation_required ? "error" : status.connected ? "healthy" : ""}`;
  ui.millSupervisorDetail.textContent = status.reconciliation_required
    ? "Mill supervisor reconciliation would be required before command routing. Existing SSH control remains unchanged while this rollout is inactive."
    : status.connected
    ? status.enabled
      ? "Persistent mill supervisor connected. Scheduler commands use the durable controller-resident ledger."
      : "Telemetry-only agent connected. Program commands remain disabled."
    : (status.last_disconnect_detail || "No staged mill-supervisor connection is started automatically.");
  ui.reconcileMillSupervisor.disabled = !status.reconciliation_required;
}

async function loadMillSupervisor() {
  try {
    renderMillSupervisor(await api("/api/debug/cnc/supervisor", {cache: "no-store"}));
  } catch (error) {
    ui.millSupervisorDetail.textContent = error.message;
  }
}

ui.reconcileMillSupervisor.addEventListener("click", async () => {
  const candidate = millSupervisorState?.recent_commands?.find(command =>
    ["latched", "faulted", "uncertain", "monitoring_released"].includes(command.status));
  if (!candidate) return showToast("No mill command is awaiting reconciliation.", "error");
  const resolution = await window.mpsConfirm({
    eyebrow: "Mill command ledger",
    title: `Reconcile sequence ${candidate.sequence}?`,
    message: "This updates only the durable ledger. It will not load, start, stop, or retry a PathPilot program.",
    tone: "warning",
    primaryLabel: "Accept completed",
    secondaryLabel: "Mark faulted",
  });
  if (!resolution) return;
  try {
    const board = await api("/api/board", {cache: "no-store"});
    renderMillSupervisor(await api("/api/debug/cnc/supervisor/reconcile", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: board.revision,
        sequence: candidate.sequence,
        resolution: resolution === "primary" ? "accept_completed" : "mark_faulted",
      }),
    }));
    showToast("Mill supervisor ledger reconciled. No controller command was sent.");
  } catch (error) {
    showToast(error.message, "error");
    await loadMillSupervisor();
  }
});

async function loadCncIoLabels() {
  try {
    const result = await api("/api/debug/cnc/io-labels");
    cncIoLabels = result.labels || cncIoLabels;
    if (latestCncSnapshot) renderCnc(latestCncSnapshot);
  } catch {
    // The live values remain useful when the static HAL label map is unavailable.
  }
}

document.addEventListener("click", async event => {
  const editMillProgramButton = event.target.closest("[data-edit-debug-mill-program]");
  if (editMillProgramButton && latestCncSnapshot) {
    editingMillProgramIndex = Number(editMillProgramButton.dataset.editDebugMillProgram);
    const button = latestCncSnapshot.mill_program_controls?.buttons?.[editingMillProgramIndex];
    if (!button) return;
    ui.millProgramName.value = button.display_name;
    ui.millProgramFilename.innerHTML = `<option value="">No Mill program assigned</option>`;
    ui.millProgramColor.value = button.color;
    ui.millProgramDialog.showModal();
    try {
      const result = await api("/api/debug/mill-programs/files");
      // The API scans only PathPilot's user G-code folder.
      const files = [...new Set(result.files || [])];
      ui.millProgramFilename.innerHTML = ["", ...files]
        .map(file => `<option value="${escapeHtml(file)}">${escapeHtml(file || "No Mill program assigned")}</option>`)
        .join("");
      ui.millProgramFilename.value = button.filename;
    } catch (error) {
      showToast(error.message, "error");
    }
    return;
  }

  const runMillProgramButton = event.target.closest("[data-run-debug-mill-program]");
  if (runMillProgramButton && latestCncSnapshot) {
    const button = runMillProgramButton;
    button.disabled = true;
    try {
      const updated = await api("/api/debug/mill-programs/run", {
        method: "POST",
        body: JSON.stringify({expected_revision: latestCncSnapshot.revision, index: Number(button.dataset.runDebugMillProgram)}),
      });
      latestCncSnapshot = updated;
      renderCnc(updated);
      showToast("PathPilot accepted the program start command.");
    } catch (error) {
      showToast(error.message, "error");
    }
    return;
  }

  const editProgramButton = event.target.closest("[data-edit-debug-program]");
  if (editProgramButton && snapshotState) {
    editingProgramIndex = Number(editProgramButton.dataset.editDebugProgram);
    const button = snapshotState.program_controls?.buttons?.[editingProgramIndex];
    if (!button) return;
    ui.programName.value = button.display_name;
    ui.programFilename.innerHTML = `<option value="">No Robot program assigned</option>`;
    ui.programColor.value = button.color;
    ui.programDialog.showModal();
    try {
      const result = await api("/api/debug/programs/files");
      const files = [...new Set([button.filename, ...result.files].filter(Boolean))];
      ui.programFilename.innerHTML = ["", ...files]
        .map(file => `<option value="${escapeHtml(file)}">${escapeHtml(file || "No Robot program assigned")}</option>`)
        .join("");
      ui.programFilename.value = button.filename;
    } catch (error) {
      showToast(error.message, "error");
    }
    return;
  }

  const runProgramButton = event.target.closest("[data-run-debug-program]");
  if (runProgramButton && snapshotState) {
    try {
      const updated = await api("/api/debug/programs/run", {
        method: "POST",
        body: JSON.stringify({expected_revision: snapshotState.revision, index: Number(runProgramButton.dataset.runDebugProgram)}),
      });
      render(updated);
      showToast("Controller accepted the program start command.");
    } catch (error) {
      showToast(error.message, "error");
    }
    return;
  }

  const renameButton = event.target.closest("[data-rename-debug-io]");
  if (renameButton && snapshotState) {
    const currentLabel = renameButton.dataset.debugLabel || "";
    const nextLabel = await window.mpsPrompt({
      eyebrow: "I/O configuration",
      title: "Rename I/O port",
      message: "Leave the label blank to reset to the hardware name.",
      label: "Display label",
      value: currentLabel,
      submitLabel: "Save label",
    });
    if (nextLabel === null) return;
    try {
      const updated = await api("/api/debug/io/label", {
        method: "POST",
        body: JSON.stringify({
          expected_revision: snapshotState.revision,
          direction: renameButton.dataset.debugDirection,
          bank: renameButton.dataset.debugBank,
          index: Number(renameButton.dataset.debugIndex),
          label: nextLabel,
        }),
      });
      render(updated);
      showToast("I/O label saved.");
    } catch (error) {
      showToast(error.message, "error");
    }
    return;
  }

  const button = event.target.closest("[data-toggle-debug-io]");
  if (!button || !snapshotState) return;
  if (button.disabled) return;
  try {
    const updated = await api("/api/debug/io/toggle", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: snapshotState.revision,
        direction: button.dataset.debugDirection,
        bank: button.dataset.debugBank,
        index: Number(button.dataset.debugIndex),
      }),
    });
    render(updated);
    showToast(`Toggled ${button.dataset.debugBank} ${button.dataset.debugDirection} ${button.dataset.debugIndex}.`);
  } catch (error) {
    showToast(error.message, "error");
  }
});

async function runPalletMotionTest(operation) {
  if (!snapshotState) return;
  const button = operation === "pick" ? ui.palletMotionPick : ui.palletMotionPlace;
  button.disabled = true;
  ui.palletMotionStatus.textContent = `${operation === "pick" ? "Pick" : "Place"} command is being dispatched...`;
  try {
    const result = await api("/api/debug/pallet-motion", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: snapshotState.revision,
        operation,
        pool_slot_number: Number(ui.palletMotionSlot.value),
      }),
    });
    ui.palletMotionStatus.textContent = result.message;
    showToast(result.message);
  } catch (error) {
    ui.palletMotionStatus.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    button.disabled = !palletMotionSettings?.pallet_motion_enabled;
  }
}

ui.palletMotionPick.addEventListener("click", () => runPalletMotionTest("pick"));
ui.palletMotionPlace.addEventListener("click", () => runPalletMotionTest("put"));

async function runMillPalletMotionTest(operation) {
  if (!snapshotState) return;
  const button = operation === "load" ? ui.millMotionLoad : ui.millMotionUnload;
  button.disabled = true;
  ui.millMotionStatus.textContent = `${operation === "load" ? "Load" : "Unload"} command is being dispatched...`;
  try {
    const result = await api("/api/debug/mill-pallet-motion", {
      method: "POST",
      body: JSON.stringify({expected_revision: snapshotState.revision, operation}),
    });
    ui.millMotionStatus.textContent = result.message;
    showToast(result.message);
  } catch (error) {
    ui.millMotionStatus.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    ui.millMotionLoad.disabled = !millPalletMotionReady;
    ui.millMotionUnload.disabled = !millPalletMotionReady;
  }
}

ui.millMotionLoad.addEventListener("click", () => runMillPalletMotionTest("load"));
ui.millMotionUnload.addEventListener("click", () => runMillPalletMotionTest("unload"));

ui.programCancel.addEventListener("click", () => ui.programDialog.close());
ui.millProgramCancel.addEventListener("click", () => ui.millProgramDialog.close());
ui.programForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (editingProgramIndex === null || !snapshotState) return;
  try {
    const updated = await api("/api/debug/programs/configure", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: snapshotState.revision,
        index: editingProgramIndex,
        display_name: ui.programName.value,
        filename: ui.programFilename.value,
        color: ui.programColor.value,
      }),
    });
    ui.programDialog.close();
    render(updated);
    showToast("Program button saved.");
  } catch (error) {
    showToast(error.message, "error");
  }
});

ui.millProgramForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (editingMillProgramIndex === null || !latestCncSnapshot) return;
  try {
    const updated = await api("/api/debug/mill-programs/configure", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: latestCncSnapshot.revision,
        index: editingMillProgramIndex,
        display_name: ui.millProgramName.value,
        filename: ui.millProgramFilename.value,
        color: ui.millProgramColor.value,
      }),
    });
    ui.millProgramDialog.close();
    latestCncSnapshot = updated;
    renderCnc(updated);
    showToast("Mill program button saved.");
  } catch (error) {
    showToast(error.message, "error");
  }
});

loadCncIoLabels();
loadRobotProgramsNav();
loadPalletMotionTestSettings();

async function pollRobotDebugging() {
  if (!document.hidden) await Promise.all([loadDebugging(), loadReliabilityTest()]);
  window.setTimeout(pollRobotDebugging, 2000);
}

async function pollCncDebugging() {
  if (!document.hidden) await Promise.all([loadCncDebugging(), loadMillSupervisor()]);
  window.setTimeout(pollCncDebugging, 5000);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  loadDebugging();
  loadCncDebugging();
  loadMillSupervisor();
});

pollRobotDebugging();
loadMillSupervisor();
async function pollSupervisorDebugging() {
  if (!document.hidden) await loadSupervisorDebugging();
  window.setTimeout(pollSupervisorDebugging, 2000);
}

async function pollDiagnostics() {
  if (!document.hidden) await loadDiagnostics();
  window.setTimeout(pollDiagnostics, 10000);
}

pollSupervisorDebugging();
pollDiagnostics();
pollCncDebugging();
