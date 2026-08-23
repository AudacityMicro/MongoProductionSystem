const KG_TO_LB = 2.2046226218;
const contentLabels = {
  empty: "Empty",
  raw_stock: "Raw stock",
  complete_parts: "Complete parts",
  defective_parts: "Defective parts",
};

const ui = {
  state: document.querySelector("#system-state"),
  queue: document.querySelector("#queue-list"),
  pool: document.querySelector("#pool-list"),
  machine: document.querySelector("#machine-slot"),
  onDeck: document.querySelector("#on-deck-slot"),
  dripping: document.querySelector("#dripping-slot"),
  onDeckZone: document.querySelector('[data-zone="on_deck"]'),
  drippingZone: document.querySelector('[data-zone="dripping"]'),
  storage: document.querySelector("#storage-list"),
  warning: document.querySelector("#program-warning"),
  warningMessage: document.querySelector("#program-warning-message"),
  warningDismiss: document.querySelector("#dismiss-program-warning"),
  notificationCenter: document.querySelector("#schedule-notifications"),
  palletProgramHelp: document.querySelector("#pallet-program-help"),
  toast: document.querySelector("#toast"),
  palletDialog: document.querySelector("#pallet-dialog"),
  palletForm: document.querySelector("#pallet-form"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  autoscheduleDialog: document.querySelector("#autoschedule-dialog"),
  autoscheduleSummary: document.querySelector("#autoschedule-summary"),
  autoscheduleWarning: document.querySelector("#autoschedule-warning"),
  autoscheduleSteps: document.querySelector("#autoschedule-steps"),
  autoscheduleNote: document.querySelector("#autoschedule-note"),
  debugPanel: document.querySelector("#debug-panel"),
  debugState: document.querySelector("#debug-state"),
  robotHeld: document.querySelector("#robot-held-slot"),
  robotMotionStatus: document.querySelector("#robot-motion-status"),
  robotMotionSummary: document.querySelector("#robot-motion-summary"),
  robotMotionDismiss: document.querySelector("#dismiss-robot-motion"),
  runModeToggle: document.querySelector("#run-mode-toggle"),
  resumeQueueAfterManualRobot: document.querySelector("#resume-queue-after-manual-robot"),
  runModeStatus: document.querySelector("#run-mode-status"),
  millOptionalStopOff: document.querySelector("#mill-optional-stop-off"),
  millFeedHold: document.querySelector("#mill-feed-hold"),
  millStop: document.querySelector("#mill-stop"),
  runConfirmDialog: document.querySelector("#run-confirm-dialog"),
  recoveryLaunch: document.querySelector("#system-recovery-launch"),
  recoveryDialog: document.querySelector("#system-recovery-dialog"),
  recoveryMessage: document.querySelector("#system-recovery-message"),
  recoveryFaults: document.querySelector("#system-recovery-faults"),
  recoveryServices: document.querySelector("#system-recovery-services"),
  recoveryQuestions: document.querySelector("#system-recovery-questions"),
  recoveryActions: document.querySelector("#system-recovery-actions"),
  recoveryCancel: document.querySelector("#system-recovery-cancel"),
  recoveryContinue: document.querySelector("#system-recovery-continue"),
};

let board = null;
let draggedPalletId = null;
let draggedCardContext = null;
let confirmCallback = null;
let autoschedulePlan = null;
let shownRunConfirmationToken = null;
let palletDialogPrograms = [];
let palletSaveInProgress = false;
let renderedMotionKey = null;
let renderedBoardKey = null;
let boardLoadPromise = null;
let dismissedProgramWarning = null;
let dismissedMotionKey = null;
let runModeStartPending = false;
let runModeStopQueued = false;
let pendingRunModeRequestId = null;
let recoveryState = null;
let recoveryPollTimer = null;

function newRunModeRequestId() {
  // Some older tablet/webview browsers lack crypto.randomUUID(). The server
  // only needs a short, unique idempotency key for one start request.
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const random = globalThis.crypto?.getRandomValues
    ? globalThis.crypto.getRandomValues(new Uint32Array(2))
    : [Math.floor(Math.random() * 0x100000000), Math.floor(Math.random() * 0x100000000)];
  return `${Date.now().toString(36)}-${random[0].toString(36)}${random[1].toString(36)}`.slice(0, 36);
}

function reconcilePendingRunModeStart(nextBoard) {
  const run = nextBoard?.run_mode || {};
  // A persisted start request always sets enabled before any slow controller
  // check. If the authoritative board is idle/completed/faulted, this browser
  // request cannot still own a start lock.
  if (runModeStartPending && !run.enabled && run.state !== "start_requested") {
    runModeStartPending = false;
    runModeStopQueued = false;
    pendingRunModeRequestId = null;
    return true;
  }
  return false;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function displayWeight(weightKg) {
  if (board.settings.weight_unit === "lb") {
    return `${(weightKg * KG_TO_LB).toFixed(2)} lb`;
  }
  return `${weightKg.toFixed(2)} kg`;
}

function displayCycleTime(seconds) {
  if (!seconds) return "";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function syncRobotProgramsNav() {
  document.querySelectorAll("[data-robot-programs-nav]").forEach(link => {
    link.classList.toggle("hidden", !board?.settings.robot_programs_page_enabled);
  });
  document.querySelectorAll("[data-mill-programs-nav]").forEach(link => {
    link.classList.toggle("hidden", !board?.settings.mill_programs_page_enabled);
  });
}

function inputWeight(weightKg) {
  return board.settings.weight_unit === "lb" ? weightKg * KG_TO_LB : weightKg;
}

function canonicalWeight(value) {
  return board.settings.weight_unit === "lb" ? value / KG_TO_LB : value;
}

function renderProgramOptions(selectedProgram = "", programs = board.programs || []) {
  const select = document.querySelector("#pallet-program");
  const available = programs;
  const options = ['<option value="">No program assigned</option>'];
  if (selectedProgram && !available.includes(selectedProgram)) {
    options.push(`<option value="${escapeHtml(selectedProgram)}" disabled>Unavailable: ${escapeHtml(selectedProgram)}</option>`);
  }
  options.push(...available.map(program => `<option value="${escapeHtml(program)}">${escapeHtml(program)}</option>`));
  select.innerHTML = options.join("");
  select.value = selectedProgram || "";
}

async function loadPalletProgramOptions(selectedProgram = "") {
  const select = document.querySelector("#pallet-program");
  select.disabled = true;
  ui.palletProgramHelp.textContent = "Loading programs from the PathPilot Gcode folder...";
  try {
    const result = await api("/api/pallet-programs");
    palletDialogPrograms = result.files || [];
    renderProgramOptions(selectedProgram, palletDialogPrograms);
    ui.palletProgramHelp.textContent = palletDialogPrograms.length
      ? `${palletDialogPrograms.length} program${palletDialogPrograms.length === 1 ? "" : "s"} available from the PathPilot Gcode folder.`
      : "No allowed mill programs were found in the PathPilot Gcode folder.";
  } catch (error) {
    palletDialogPrograms = [];
    renderProgramOptions(selectedProgram, []);
    ui.palletProgramHelp.textContent = `Could not read PathPilot programs: ${error.message}`;
  } finally {
    select.disabled = false;
  }
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 409) await loadBoard();
    throw new Error(errorMessage(data.detail, `Request failed with status ${response.status}`));
  }
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
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => ui.toast.classList.add("hidden"), 4200);
}

function emptyState(label) {
  return `<div class="zone-empty"><span>+</span><p>${escapeHtml(label)}</p></div>`;
}

function palletReturnGhost(pallet) {
  const source = pallet.location === "machine" ? "Mill" : "Robot-held";
  return `
    <article class="pool-return-ghost" aria-label="${escapeHtml(pallet.name)} is reserved to return here">
      <span class="ghost-label">Reserved return</span>
      <strong>${escapeHtml(pallet.name)}</strong>
      <small>Currently ${source}</small>
    </article>`;
}

function palletCard(pallet, position = null) {
  const program = pallet.program_path || "No program";
  const runLocked = Boolean(board.run_mode?.enabled);
  const canManage = !runLocked || pallet.location !== "machine";
  const activeMotionPalletId = board.robot_motion?.active?.pallet_id;
  const queueAction = canManage && pallet.id !== activeMotionPalletId && pallet.queue_position === null
    ? (pallet.location === "pool" ? `<button class="text-button" data-action="queue">Queue</button>` : "")
    : "";
  const dequeueAction = canManage && pallet.id !== activeMotionPalletId && position !== null && pallet.queue_position !== null
    ? `<button class="text-button" data-action="dequeue">Remove from queue</button>`
    : "";
  const motionLocked = Boolean(board.robot_motion?.active) || runLocked;
  const pickAction = pallet.location === "pool" && !motionLocked
    ? `<button class="text-button" data-action="pick">Pick</button>`
    : "";
  const automaticPutAwayAction = board.capabilities?.automatic_put_away
    && ["machine", "robot_held"].includes(pallet.location) && !motionLocked
    ? `<button class="text-button" data-action="automatic-put-away">Put away pallet</button>`
    : "";
  const millPutAwayAction = pallet.location === "machine" && !motionLocked
    ? `<button class="text-button" data-action="mongo-unload">Choose return position</button>`
    : "";
  const manualReturnAction = pallet.location === "machine" && !motionLocked
    ? `<button class="text-button danger-text" data-action="manual-return-to-pool">Record return to pool</button>`
    : "";
  const sendToPoolAction = pallet.location === "storage" && !motionLocked
    ? `<button class="text-button" data-action="send-to-pool">Move to pool</button>`
    : "";
  const returnToStorageAction = ["pool", "on_deck", "dripping"].includes(pallet.location) && !motionLocked
    ? `<button class="text-button" data-action="return-to-storage">Return to storage</button>`
    : "";
  const queueBadge = pallet.queue_position !== null && position === null
    ? `<span class="queue-chip">Queued #${pallet.queue_position + 1}</span>`
    : "";
  const cardContext = position === null ? "physical" : "queue";
  const showProgramDetails = pallet.program_path && !["complete_parts", "defective_parts"].includes(pallet.content_status);
  const programDetails = !showProgramDetails
    ? ""
    : pallet.program_metadata_state === "parsed"
      ? `<div><dt>Tools</dt><dd>${escapeHtml(pallet.program_tools.join(", ") || "None")}</dd></div>
         <div><dt>WCS</dt><dd>${escapeHtml((pallet.program_wcs || []).join(", ") || "None")}</dd></div>
         <div><dt>Cycle</dt><dd>${displayCycleTime(pallet.expected_cycle_seconds)}</dd></div>`
      : `<div><dt>Metadata</dt><dd title="${escapeHtml(pallet.program_metadata_detail || "Program metadata unavailable")}">Unavailable</dd></div>`;
  return `
    <article class="pallet-card content-${pallet.content_status}" draggable="${canManage && pallet.location !== "robot_held" ? "true" : "false"}"
      data-pallet-id="${pallet.id}" data-card-context="${cardContext}" tabindex="0">
      <div class="card-topline">
        ${position === null ? `<span class="drag-handle" aria-hidden="true">⠿</span>` : `<span class="queue-number">${position + 1}</span>`}
        <span class="card-badges">${queueBadge}<span class="content-chip">${contentLabels[pallet.content_status]}</span></span>
      </div>
      <h3>${escapeHtml(pallet.name)}</h3>
      <dl>
        <div><dt>Holding</dt><dd>${escapeHtml(pallet.workholding)}</dd></div>
        <div><dt>Weight</dt><dd>${displayWeight(pallet.weight_kg)}</dd></div>
        <div><dt>Program</dt><dd class="${pallet.program_path ? "" : "muted"}">${escapeHtml(program)}</dd></div>
        ${programDetails}
      </dl>
      <div class="card-actions">
        ${queueAction}
        ${dequeueAction}
        ${pickAction}
        ${automaticPutAwayAction}
        ${millPutAwayAction}
        ${manualReturnAction}
        ${sendToPoolAction}
        ${returnToStorageAction}
        ${canManage ? `<button class="text-button" data-action="edit">Edit</button>
        <button class="text-button" data-action="duplicate">Duplicate</button>
        <button class="text-button danger-text" data-action="delete">Delete</button>` : ""}
      </div>
    </article>`;
}

function renderBoard() {
  renderedBoardKey = JSON.stringify(board);
  syncRobotProgramsNav();
  const pallets = board.pallets;
  const queue = pallets.filter(item => item.queue_position !== null)
    .sort((a, b) => a.queue_position - b.queue_position);
  const pool = pallets.filter(item => item.location === "pool")
    .sort((a, b) => a.pool_slot_number - b.pool_slot_number);
  const machine = pallets.find(item => item.location === "machine");
  const onDeck = pallets.find(item => item.location === "on_deck");
  const dripping = pallets.find(item => item.location === "dripping");
  const robotHeld = pallets.find(item => item.location === "robot_held");
  const returnGhosts = pallets.filter(item =>
    ["machine", "robot_held"].includes(item.location) && item.return_pool_slot_number !== null,
  );
  const stored = pallets.filter(item => item.location === "storage")
    .sort((a, b) => a.name.localeCompare(b.name));

  ui.queue.innerHTML = queue.length
    ? queue.map((item, index) => palletCard(item, index)).join("")
    : emptyState("Drop pallets here to build the run order");
  ui.pool.innerHTML = Array.from(
    {length: board.settings.pool_slot_count},
    (_, index) => {
      const number = index + 1;
      const occupant = pool.find(item => item.pool_slot_number === number);
      const ghost = returnGhosts.find(item => item.return_pool_slot_number === number);
      return `<div class="pool-position drop-target ${occupant ? "occupied" : ghost ? "reserved" : ""}"
        data-destination="pool" data-pool-slot="${number}">
        <header><span>${String(number).padStart(2, "0")}</span><small>Pool position</small></header>
        ${occupant ? palletCard(occupant) : ghost ? palletReturnGhost(ghost) : (robotHeld && !board.robot_motion?.active ? `<button class="button secondary pool-put-action" type="button" data-put-slot="${number}">Put Robot-held pallet here</button>` : emptyState("Available"))}
      </div>`;
    },
  ).join("");
  ui.machine.innerHTML = machine
    ? palletCard(machine)
    : emptyState("Machine is available");
  ui.onDeck.innerHTML = onDeck
    ? palletCard(onDeck)
    : emptyState("Stage the next pallet here");
  ui.dripping.innerHTML = dripping
    ? palletCard(dripping)
    : emptyState("Stage finished pallets here");
  ui.onDeckZone.classList.toggle("hidden", board.settings.on_deck_enabled === false);
  ui.drippingZone.classList.toggle("hidden", board.settings.dripping_enabled === false);
  ui.robotHeld.innerHTML = robotHeld
    ? palletCard(robotHeld)
    : emptyState("Picked pallets appear here");

  ui.storage.innerHTML = stored.length
    ? stored.map(item => `<div class="storage-position"><span class="storage-row-label">Stored</span>${palletCard(item)}</div>`).join("")
    : emptyState("Stored pallets appear here");

  document.querySelector("#queue-count").textContent = `${queue.length} pallet${queue.length === 1 ? "" : "s"}`;
  document.querySelector("#autoschedule-queue").disabled = queue.filter(item => item.program_tools?.length).length < 2;
  document.querySelector("#create-pallet").disabled = false;
  if (board.run_mode?.enabled) document.querySelector("#autoschedule-queue").disabled = true;
  document.querySelector("#pool-count").textContent = `${pool.length} pallet${pool.length === 1 ? "" : "s"}${returnGhosts.length ? ` · ${returnGhosts.length} reserved` : ""}`;
  document.querySelector("#storage-count").textContent = `${stored.length} pallet${stored.length === 1 ? "" : "s"}`;
  document.querySelector("#weight-unit-label").textContent = `(${board.settings.weight_unit})`;
  // Program choices are read from PathPilot when the pallet dialog opens.
  if (!ui.palletDialog.open) {
    renderProgramOptions();
    ui.palletProgramHelp.textContent = "Open a pallet to load the current program list from the PathPilot Gcode folder.";
  }
  document.querySelector("#workholding-options").innerHTML = (board.settings.workholding_library || [])
    .map(workholding => `<option value="${escapeHtml(workholding)}"></option>`).join("");
  renderRobotMotionStatus();
  renderRunMode();
  renderMillControls();

  const runAlert = board.run_mode?.alert || "";
  if (runAlert !== dismissedProgramWarning) dismissedProgramWarning = null;
  ui.warning.classList.toggle("hidden", !runAlert || dismissedProgramWarning === runAlert);
  ui.warningMessage.textContent = runAlert;
  renderNotificationCenter();
  ui.state.classList.add("online");
  ui.state.lastChild.textContent = ` Online · rev ${board.revision}`;
  ui.debugPanel.classList.toggle("hidden", !board.settings.debug_menu_enabled);
  document.body.classList.toggle(
    "debug-active",
    board.settings.debug_menu_enabled,
  );
  ui.debugState.textContent = board.settings.machine_state;
  ui.debugState.className = `debug-state state-${board.settings.machine_state}`;
}

function renderRobotMotionStatus() {
  const motion = board.robot_motion?.active;
  if (!motion) {
    renderedMotionKey = null;
    dismissedMotionKey = null;
    ui.robotMotionStatus.classList.add("hidden");
    ui.robotMotionSummary.innerHTML = "";
    return;
  }
  const palletIsHeld = motion.operation === "pick"
    && board.pallets.some(pallet => pallet.id === motion.pallet_id && pallet.location === "robot_held");
  const motionKey = JSON.stringify([
    motion.id,
    motion.status,
    motion.operation,
    motion.source_slot,
    motion.destination_slot,
    motion.failure_detail,
    palletIsHeld,
  ]);
  if (motionKey !== dismissedMotionKey) dismissedMotionKey = null;
  if (motionKey === renderedMotionKey) {
    ui.robotMotionStatus.classList.toggle("hidden", dismissedMotionKey === motionKey);
    return;
  }
  renderedMotionKey = motionKey;
  const target = motion.operation === "pick"
    ? `Pool ${String(motion.source_slot).padStart(2, "0")}`
    : motion.operation === "put"
      ? `Pool ${String(motion.destination_slot).padStart(2, "0")}`
      : motion.operation === "load_mill"
        ? `${motion.source_slot ? `Pool ${String(motion.source_slot).padStart(2, "0")}` : "Robot-held"} -> Mill`
        : `Mill -> Pool ${String(motion.destination_slot).padStart(2, "0")}`;
  const status = motion.status === "faulted"
    ? "Movement fault"
    : palletIsHeld
      ? "Pallet secured, robot retreating"
      : motion.status === "running"
        ? "Robot moving"
        : "Movement requested";
  ui.robotMotionStatus.className = `robot-motion-status ${motion.status}`;
  ui.robotMotionStatus.classList.toggle("hidden", dismissedMotionKey === motionKey);
  ui.robotMotionSummary.innerHTML = `<strong>${status}: ${escapeHtml(motion.pallet_name || "Pallet")}</strong><span>${escapeHtml(motion.operation)} ${target} | ${escapeHtml(motion.program_path)}${motion.failure_detail ? ` | ${escapeHtml(motion.failure_detail)}` : ""}</span>`;
}

ui.toast.addEventListener("click", event => {
  if (event.target.closest(".toast-dismiss")) ui.toast.classList.add("hidden");
});

ui.warningDismiss.addEventListener("click", async () => {
  dismissedProgramWarning = board?.run_mode?.alert || null;
  ui.warning.classList.add("hidden");
  renderNotificationCenter();
  try {
    board = await api("/api/run-mode/alert/dismiss", {method: "POST", body: "{}"});
    renderBoard();
  } catch (error) {
    showToast(`Could not dismiss alert: ${error.message}`, "error");
  }
});

ui.robotMotionDismiss.addEventListener("click", () => {
  const motion = board?.robot_motion?.active;
  dismissedMotionKey = motion ? JSON.stringify([
    motion.id,
    motion.status,
    motion.operation,
    motion.source_slot,
    motion.destination_slot,
    motion.failure_detail,
    motion.operation === "pick" && board.pallets.some(pallet => pallet.id === motion.pallet_id && pallet.location === "robot_held"),
  ]) : null;
  ui.robotMotionStatus.classList.add("hidden");
  renderNotificationCenter();
});

function renderRunMode() {
  const run = board.run_mode || {};
  const pendingStart = runModeStartPending && !run.enabled;
  ui.runModeToggle.textContent = pendingStart
    ? (runModeStopQueued ? "Cancelling run start..." : "Cancel pending start")
    : run.enabled ? (run.state === "start_requested" ? "Cancel run start" : "Stop run mode")
      : run.state === "stopping" ? "Stopping run mode..." : "Start run mode";
  ui.runModeToggle.disabled = run.state === "stopping" || (pendingStart && runModeStopQueued);
  ui.runModeToggle.classList.toggle("active", Boolean(run.enabled));
  ui.resumeQueueAfterManualRobot.classList.toggle("hidden", !run.manual_robot_pause);
  ui.runModeStatus.className = `run-mode-status ${escapeHtml(run.state || "idle")}`;
  const pallet = run.current_pallet_name ? ` · ${escapeHtml(run.current_pallet_name)}` : "";
  const showDetail = (
    !run.enabled && ["faulted", "interrupted", "stopped"].includes(run.state)
  ) || [
    "idle_waiting_queue",
    "telemetry_unavailable",
    "telemetry_restored",
    "recovering_startup_telemetry",
    "recovering_cnc_telemetry",
    "recovering_robot_telemetry",
  ].includes(run.state);
  const detail = showDetail ? `<span>${escapeHtml(run.detail || "Run Mode needs operator attention.")}</span>` : "";
  ui.runModeStatus.innerHTML = `<div><span class="run-mode-light"></span><strong>${run.enabled ? "Run mode active" : "Run mode " + escapeHtml(run.state || "idle")}${pallet}</strong></div>${detail}`;

  if (run.confirmation_token && run.pending_action && shownRunConfirmationToken !== run.confirmation_token) {
    shownRunConfirmationToken = run.confirmation_token;
    const cncFault = run.pending_action === "retry_cnc_program";
    const cncPreflight = run.pending_action === "retry_cnc_preflight";
    const robotRetry = run.pending_action === "retry_robot_transfer";
    document.querySelector("#run-confirm-title").textContent = cncFault
      ? "Mill program stopped"
      : cncPreflight ? "PathPilot connection unavailable"
      : robotRetry ? "Robot connection interrupted"
      : `Approve ${run.pending_action.replaceAll("_", " ")}`;
    document.querySelector("#run-confirm-message").textContent = run.detail;
    document.querySelector("#run-confirm-stop").textContent = cncFault || cncPreflight || robotRetry
      ? "Stop and leave pallet"
      : "Stop run mode";
    document.querySelector("#run-confirm-approve").textContent = cncFault
      ? "Retry same program"
      : cncPreflight ? "Retry connection check"
      : robotRetry ? "Reconnect and retry robot only"
      : "Approve action";
    if (!ui.runConfirmDialog.open) ui.runConfirmDialog.showModal();
  }
  if (!run.confirmation_token) {
    shownRunConfirmationToken = null;
    if (ui.runConfirmDialog.open) ui.runConfirmDialog.close();
  }
}

function renderMillControls() {
  const mill = board?.mill_control || {};
  const active = Boolean(mill.running || mill.paused);
  ui.millOptionalStopOff.classList.toggle("hidden", mill.optional_stop !== true);
  ui.millOptionalStopOff.disabled = !mill.can_control;
  ui.millFeedHold.disabled = !mill.can_control || !active || mill.paused === true;
  ui.millStop.disabled = !mill.can_control || !active;
}

async function requestMillControl(action, confirmed = false) {
  const result = await api(`/api/cnc/control/${action}`, {
    method: "POST",
    body: JSON.stringify({expected_revision: board.revision, confirmed}),
  });
  board = result.board;
  renderBoard();
  showToast(result.message);
  return result;
}

ui.millOptionalStopOff.addEventListener("click", async () => {
  ui.millOptionalStopOff.disabled = true;
  try {
    await requestMillControl("optional_stop_off");
  } catch (error) {
    showToast(error.message, "error");
    renderMillControls();
  }
});

ui.millFeedHold.addEventListener("click", async () => {
  ui.millFeedHold.disabled = true;
  try {
    await requestMillControl("feed_hold");
  } catch (error) {
    showToast(error.message, "error");
    renderMillControls();
  }
});

ui.millStop.addEventListener("click", async () => {
  const choice = await window.mpsConfirm({
    eyebrow: "Immediate mill control",
    title: "Stop the mill program?",
    message: "PathPilot will abort the active program. Run Mode will stop and will not move the pallet or start another program.",
    tone: "danger",
    primaryLabel: "Stop mill",
  });
  if (choice !== "primary") return;
  ui.millStop.disabled = true;
  try {
    await requestMillControl("stop", true);
  } catch (error) {
    showToast(error.message, "error");
    renderMillControls();
  }
});

function recoveryList(items, service = false) {
  if (!items?.length) return `<p class="field-help">${service ? "No enabled service faults detected." : "No blocking conditions detected."}</p>`;
  return `<ul class="recovery-list">${items.map(item => {
    const tone = item.severity === "handoff" || item.action === "Handoff required" ? "error" : item.connected === true || item.action === "Recovered" ? "healthy" : "warning";
    const title = item.title || `${item.controller || item.name || "Recovery"}: ${item.action || item.state || "status"}`;
    return `<li class="${tone}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(item.detail || item.message || item.state || "")}</span></li>`;
  }).join("")}</ul>`;
}

function renderSystemRecovery() {
  const data = recoveryState;
  const session = data?.session;
  const existingQuestion = ui.recoveryQuestions.querySelector("[data-recovery-answer]");
  const existingAnswerKey = existingQuestion?.dataset.recoveryAnswer;
  const existingAnswerValue = existingQuestion?.checked === true;
  const existingChoices = Object.fromEntries(
    [...ui.recoveryQuestions.querySelectorAll("[data-recovery-choice]:checked")]
      .map(input => [input.dataset.recoveryChoice, input.value]),
  );
  if (!session) {
    ui.recoveryMessage.textContent = "No recovery session is active.";
    ui.recoveryFaults.innerHTML = recoveryList(data?.faults || []);
    ui.recoveryServices.innerHTML = recoveryList(data?.services || [], true);
    ui.recoveryQuestions.innerHTML = "";
    ui.recoveryActions.innerHTML = "";
    ui.recoveryContinue.classList.add("hidden");
    return;
  }
  ui.recoveryMessage.textContent = session.message || "Recovery status updated.";
  ui.recoveryFaults.innerHTML = recoveryList(data.faults?.length ? data.faults : session.faults);
  ui.recoveryServices.innerHTML = recoveryList(data.services || [], true);
  const recoveryBusy = ["running", "awaiting_restart"].includes(session.status);
  const recordedActions = session.actions?.length
    ? recoveryList(session.actions)
    : `<p class="field-help">The next controller check is starting.</p>`;
  ui.recoveryActions.innerHTML = recoveryBusy
    ? `<div class="recovery-progress" role="status" aria-live="polite"><span class="recovery-spinner" aria-hidden="true"></span><div><strong>Recovery is working</strong><span>${escapeHtml(session.message || "Checking controller connections…")}</span></div></div>${recordedActions}`
    : recordedActions;
  const guidance = data.guidance || (data.faults || []).map(item => item.guidance).filter(Boolean);
  const guidedQuestions = guidance.map(item => item.type === "choice"
    ? `<fieldset class="recovery-resolution"><legend>${escapeHtml(item.title)}</legend><p>${escapeHtml(item.detail || "")}</p>${(item.options || []).map(option => `<label class="recovery-question"><input type="radio" name="recovery-${escapeHtml(item.key)}" data-recovery-choice="${escapeHtml(item.key)}" value="${escapeHtml(option.value)}"><span><strong>${escapeHtml(option.label)}</strong>${option.detail ? `<small>${escapeHtml(option.detail)}</small>` : ""}</span></label>`).join("")}</fieldset>`
    : `<section class="recovery-instruction"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail || "")}</p></section>`
  ).join("");
  const instructionAcknowledgement = guidance.some(item => item.type === "instruction")
    ? `<label class="recovery-question"><input type="checkbox" data-recovery-answer="retry"><span><strong>I completed the checks that are physically possible.</strong><small>Recheck the system now. Recovery will still refuse unsafe or ambiguous state.</small></span></label>`
    : "";
  ui.recoveryQuestions.innerHTML = session.status === "awaiting_safety"
    ? `<label class="recovery-question"><input type="checkbox" data-recovery-answer="cell_clear"><span><strong>I inspected the cell.</strong><small>Everyone is clear, no robot or mill motion is active, and I understand that the wizard will not move either machine.</small></span></label>`
    : session.status === "handoff"
      ? guidedQuestions
        ? `${guidedQuestions}${instructionAcknowledgement}`
        : `<label class="recovery-question"><input type="checkbox" data-recovery-answer="retry"><span><strong>The reported physical condition has been handled.</strong><small>I completed the displayed controller, power, network, or configuration checks and it is safe to retry software recovery.</small></span></label>`
      : session.status === "ready"
        ? `<label class="recovery-question"><input type="checkbox" data-recovery-answer="final_approval"><span><strong>Approve the ready state.</strong><small>All displayed services are acceptable. Production will not start automatically.</small></span></label>`
        : "";
  const renderedQuestion = ui.recoveryQuestions.querySelector("[data-recovery-answer]");
  if (renderedQuestion && renderedQuestion.dataset.recoveryAnswer === existingAnswerKey) {
    renderedQuestion.checked = existingAnswerValue;
  }
  ui.recoveryQuestions.querySelectorAll("[data-recovery-choice]").forEach(input => {
    if (existingChoices[input.dataset.recoveryChoice] === input.value) input.checked = true;
  });
  ui.recoveryContinue.classList.toggle("hidden", !["awaiting_safety", "handoff", "ready"].includes(session.status));
  ui.recoveryContinue.disabled = ["running", "awaiting_restart", "completed", "cancelled"].includes(session.status);
  ui.recoveryContinue.textContent = session.status === "ready"
    ? "Approve ready state"
    : session.status === "handoff" && guidance.length ? "Continue guided recovery"
      : session.status === "handoff" ? "Retry recovery" : "Start software recovery";
}

async function loadRecoveryStatus({showErrors = false} = {}) {
  try {
    recoveryState = await api("/api/recovery/status", {cache: "no-store"});
    renderSystemRecovery();
    return recoveryState;
  } catch (error) {
    if (showErrors) showToast(`Recovery status unavailable: ${error.message}`, "error");
    return null;
  }
}

function scheduleRecoveryPoll() {
  window.clearTimeout(recoveryPollTimer);
  if (!recoveryState?.active) return;
  recoveryPollTimer = window.setTimeout(async () => {
    await loadRecoveryStatus();
    scheduleRecoveryPoll();
  }, 1000);
}

async function openSystemRecovery() {
  ui.recoveryLaunch.disabled = true;
  try {
    recoveryState = await api("/api/recovery/start", {method: "POST", body: "{}"});
    renderSystemRecovery();
    if (!ui.recoveryDialog.open) ui.recoveryDialog.showModal();
    scheduleRecoveryPoll();
  } catch (error) {
    showToast(`Could not start recovery: ${error.message}`, "error");
  } finally {
    ui.recoveryLaunch.disabled = false;
  }
}

if (ui.recoveryLaunch) ui.recoveryLaunch.addEventListener("click", openSystemRecovery);
ui.recoveryContinue.addEventListener("click", async () => {
  const session = recoveryState?.session;
  if (!session) return openSystemRecovery();
  const answers = {};
  ui.recoveryQuestions.querySelectorAll("[data-recovery-answer]").forEach(input => {
    answers[input.dataset.recoveryAnswer] = input.checked;
  });
  const choices = Object.fromEntries(
    [...ui.recoveryQuestions.querySelectorAll("[data-recovery-choice]:checked")]
      .map(input => [input.dataset.recoveryChoice, input.value]),
  );
  const requiredChoices = [...ui.recoveryQuestions.querySelectorAll("[data-recovery-choice]")]
    .map(input => input.dataset.recoveryChoice)
    .filter((value, index, values) => values.indexOf(value) === index);
  const missingChoice = requiredChoices.find(key => !choices[key]);
  if (session.status === "handoff" && missingChoice) {
    showToast("Answer each displayed recovery question before continuing.", "error");
    return;
  }
  ui.recoveryContinue.disabled = true;
  try {
    recoveryState = await api("/api/recovery/answer", {
      method: "POST",
      body: JSON.stringify({session_id: session.id, answers, choices}),
    });
    renderSystemRecovery();
    scheduleRecoveryPoll();
    await loadBoard();
  } catch (error) {
    showToast(`Recovery could not continue: ${error.message}`, "error");
    ui.recoveryContinue.disabled = false;
  }
});

ui.recoveryCancel.addEventListener("click", async () => {
  const session = recoveryState?.session;
  if (session && recoveryState.active) {
    try {
      recoveryState = await api("/api/recovery/cancel", {
        method: "POST",
        body: JSON.stringify({session_id: session.id}),
      });
      renderSystemRecovery();
      scheduleRecoveryPoll();
    } catch (error) {
      showToast(`Recovery could not be cancelled: ${error.message}`, "error");
      return;
    }
  }
  ui.recoveryDialog.close();
});

async function loadBoard() {
  if (boardLoadPromise) return boardLoadPromise;
  boardLoadPromise = (async () => {
    try {
      const nextBoard = await api("/api/board");
      const nextBoardKey = JSON.stringify(nextBoard);
      const startLockCleared = reconcilePendingRunModeStart(nextBoard);
      board = nextBoard;
      if (startLockCleared || nextBoardKey !== renderedBoardKey) renderBoard();
    } catch (error) {
      ui.state.classList.remove("online");
      ui.state.lastChild.textContent = " Unavailable";
      showToast(error.message, "error");
    }
  })();
  try {
    return await boardLoadPromise;
  } finally {
    boardLoadPromise = null;
  }
}

async function pollBoard() {
  if (!document.hidden) await loadBoard();
  window.setTimeout(pollBoard, board?.robot_motion?.active ? 500 : 1500);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadBoard();
});

function palletById(id) {
  return board.pallets.find(item => item.id === id);
}

function openPalletDialog(pallet = null, focusProgram = false) {
  document.querySelector("#pallet-id").value = pallet?.id || "";
  document.querySelector("#pallet-dialog-mode").textContent = pallet ? "Edit" : "Create";
  document.querySelector("#pallet-dialog-title").textContent = pallet ? pallet.name : "New automatic pallet";
  document.querySelector("#pallet-workholding").value = pallet?.workholding || "";
  document.querySelector("#pallet-weight").value = pallet ? inputWeight(pallet.weight_kg).toFixed(3) : "";
  document.querySelector("#pallet-contents").value = pallet?.content_status || "empty";
  // Load once per dialog from the same PathPilot SFTP source as Mill Programs.
  palletDialogPrograms = [];
  renderProgramOptions(pallet?.program_path || "", []);
  ui.palletDialog.showModal();
  void loadPalletProgramOptions(pallet?.program_path || "");
  (focusProgram ? document.querySelector("#pallet-program") : document.querySelector("#pallet-workholding")).focus();
}

async function savePallet(event) {
  event.preventDefault();
  if (palletSaveInProgress) return;
  if (!ui.palletForm.reportValidity()) return;
  const id = document.querySelector("#pallet-id").value;
  const program = document.querySelector("#pallet-program").value.trim();
  if (program && !palletDialogPrograms.includes(program)) {
    showToast("Choose a program from the PathPilot Gcode folder.", "error");
    return;
  }
  const payload = {
    expected_revision: board.revision,
    workholding: document.querySelector("#pallet-workholding").value,
    weight_kg: canonicalWeight(Number(document.querySelector("#pallet-weight").value)),
    content_status: document.querySelector("#pallet-contents").value,
    program_path: program || null,
  };
  const saveButton = document.querySelector("#save-pallet");
  palletSaveInProgress = true;
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";
  try {
    board = await api(id ? `/api/pallets/${id}` : "/api/pallets", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    ui.palletDialog.close();
    renderBoard();
    const savedPallet = id
      ? board.pallets.find(pallet => pallet.id === id)
      : board.pallets.find(pallet => pallet.program_path === program && pallet.workholding === payload.workholding);
    if (program && savedPallet?.program_metadata_state !== "parsed") {
      showToast(`${id ? "Pallet updated" : "Pallet created"}, but program metadata is unavailable: ${savedPallet?.program_metadata_detail || "repost with the updated Fusion post"}`, "error");
    } else {
      showToast(id ? "Pallet updated." : "Pallet created.");
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    palletSaveInProgress = false;
    saveButton.disabled = false;
    saveButton.textContent = "Save pallet";
  }
}

function askConfirmation(title, message, callback, options = {}) {
  void window.mpsConfirm({
    eyebrow: options.eyebrow || "Confirm action",
    title,
    message,
    tone: options.tone || "warning",
    primaryLabel: options.primaryLabel || "Continue",
    secondaryLabel: options.secondaryLabel || "",
    cancelLabel: options.cancelLabel || "Cancel",
  }).then(async choice => {
    if (choice === "primary") await callback();
    if (choice === "secondary" && options.secondaryCallback) await options.secondaryCallback();
  });
}

function renderNotificationCenter() {
  const run = board?.run_mode || {};
  const runNeedsAttention = !run.enabled && [
    "faulted", "interrupted", "stopped", "telemetry_unavailable", "telemetry_restored",
    "recovering_startup_telemetry", "recovering_cnc_telemetry", "recovering_robot_telemetry",
  ].includes(run.state);
  const visible = Boolean(
    run.enabled || runNeedsAttention
      || !ui.warning.classList.contains("hidden")
      || !ui.robotMotionStatus.classList.contains("hidden"),
  );
  ui.notificationCenter.classList.toggle("hidden", !visible);
}

async function mutate(url, options, successMessage) {
  try {
    board = await api(url, options);
    renderBoard();
    showToast(successMessage);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function movePallet(id, destination, poolSlotNumber = null) {
  await mutate(`/api/pallets/${id}/move`, {
    method: "POST",
    body: JSON.stringify({
      expected_revision: board.revision,
      destination,
      pool_slot_number: poolSlotNumber,
    }),
  }, `Moved ${palletById(id)?.name || "pallet"}.`);
}

async function queuePallet(id, queueIndex = null, convertCompletedToRaw = null) {
  const pallet = palletById(id);
  if (pallet?.content_status === "complete_parts" && convertCompletedToRaw === null) {
    askConfirmation(
      "Completed pallet",
      `${pallet.name} is marked as containing completed parts. Choose how it should enter the production queue.`,
      () => queuePallet(id, queueIndex, true),
      {
        eyebrow: "Queue preparation",
        tone: "warning",
        primaryLabel: "Change to Raw stock",
        secondaryLabel: "Queue as completed",
        secondaryCallback: () => queuePallet(id, queueIndex, false),
      },
    );
    return;
  }
  try {
    board = await api(`/api/pallets/${id}/queue`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: board.revision,
        queue_index: queueIndex,
        convert_completed_to_raw: Boolean(convertCompletedToRaw),
      }),
    });
    const queuedPallet = board.pallets.find(item => item.id === id);
    // Older live backends ignore the new queue flag. Keep the confirmation
    // useful without requiring an immediate production-service restart.
    if (convertCompletedToRaw && queuedPallet?.content_status === "complete_parts") {
      board = await api(`/api/pallets/${id}`, {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: board.revision,
          workholding: queuedPallet.workholding,
          weight_kg: queuedPallet.weight_kg,
          content_status: "raw_stock",
          program_path: queuedPallet.program_path,
        }),
      });
    }
    renderBoard();
    showToast(`${pallet?.name || "Pallet"} queued${convertCompletedToRaw ? " as Raw stock" : ""}.`);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function toolList(values) {
  return values?.length ? values.join(", ") : "None";
}

function renderAutoschedulePlan(plan) {
  const savings = plan.savings.tool_movements;
  ui.autoscheduleSummary.innerHTML = `
    <article><span>Current movements</span><strong>${plan.original.tool_movements}</strong></article>
    <article><span>Optimized movements</span><strong>${plan.optimized.tool_movements}</strong></article>
    <article><span>Estimated savings</span><strong>${savings}</strong></article>
    <article><span>ATC baseline</span><strong>${plan.atc.initial_tools.length}/${plan.atc.capacity}</strong></article>`;
  ui.autoscheduleWarning.classList.toggle("hidden", !plan.warning);
  ui.autoscheduleWarning.textContent = plan.warning || "";
  ui.autoscheduleSteps.innerHTML = plan.optimized.steps.length
    ? plan.optimized.steps.map((step, index) => `
      <li>
        <span class="autoschedule-position">${index + 1}</span>
        <div><strong>${escapeHtml(step.name)}</strong><small>${escapeHtml(step.program)}</small></div>
        <div><span>Required</span><b>${escapeHtml(toolList(step.required_tools))}</b></div>
        <div><span>Before job</span><b class="tool-load">Load: ${escapeHtml(toolList(step.load_before))}</b><b class="tool-unload">Remove: ${escapeHtml(toolList(step.unload_before))}</b></div>
      </li>`).join("")
    : `<li class="autoschedule-empty">No queued pallets have active program tool requirements.</li>`;
  const fixedNote = plan.fixed_pallets.length
    ? ` ${plan.fixed_pallets.length} pallet${plan.fixed_pallets.length === 1 ? "" : "s"} without active tool requirements will remain in place.`
    : "";
  ui.autoscheduleNote.textContent = `${plan.algorithm}. ${plan.automation.note}${fixedNote}`;
  document.querySelector("#apply-autoschedule").disabled = !plan.can_apply;
  document.querySelector("#apply-autoschedule").textContent = plan.can_apply ? "Apply optimized order" : "Already optimized";
}

async function previewAutoschedule() {
  const button = document.querySelector("#autoschedule-queue");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Analyzing ATC...";
  try {
    autoschedulePlan = await api("/api/queue/autoschedule/preview", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision}),
    });
    renderAutoschedulePlan(autoschedulePlan);
    ui.autoscheduleDialog.showModal();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.textContent = label;
    const activeCount = board.pallets.filter(item => item.queue_position !== null && item.program_tools?.length).length;
    button.disabled = activeCount < 2;
  }
}

document.querySelector("#create-pallet").addEventListener("click", () => openPalletDialog());
document.querySelector("#autoschedule-queue").addEventListener("click", previewAutoschedule);

ui.runModeToggle.addEventListener("click", async () => {
  if (runModeStartPending && !board.run_mode?.enabled) {
    runModeStopQueued = true;
    renderRunMode();
    return;
  }
  if (board.run_mode?.enabled) {
    askConfirmation("Stop run mode", "Stop after the current controller command finishes? No next automated step will start.", async () => {
      await mutate("/api/run-mode/stop", {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision}),
      }, "Run mode stop requested.");
    });
    return;
  }
  if (board.mill_control?.optional_stop === true) {
    const optionalStopChoice = await window.mpsConfirm({
      eyebrow: "PathPilot warning",
      title: "Optional Stop is on",
      message: "The mill will pause at M01. Turn Optional Stop off now, or continue starting Run Mode with it enabled.",
      tone: "warning",
      secondaryLabel: "Turn off Optional Stop",
      primaryLabel: "Continue with it on",
    });
    if (!optionalStopChoice) return;
    if (optionalStopChoice === "secondary") {
      try {
        await requestMillControl("optional_stop_off");
      } catch (error) {
        showToast(error.message, "error");
        return;
      }
    }
  }
  const queued = board.pallets.filter(item => item.queue_position !== null).length;
  const machine = board.pallets.find(item => item.location === "machine");
  let loadedMachineAction = null;
  if (machine) {
    const choice = await window.mpsConfirm({
      eyebrow: "Pallet already in mill",
      title: `What should MPS do with ${machine.name}?`,
      message: "Choose the first action. MPS returns this pallet to its reserved pool slot, then continues the queue. It will still check that PathPilot is idle before moving the mill or robot.",
      tone: "warning",
      secondaryLabel: "Unload, then start queue",
      primaryLabel: "Run program, then unload",
    });
    if (!choice) return;
    loadedMachineAction = choice === "primary" ? "run_machine_program" : "unload_then_queue";
  } else {
    const startMessage = queued
      ? `Run ${queued} queued pallet${queued === 1 ? "" : "s"} in order, then remain armed for later pallets?`
      : "Arm Run Mode and wait for pallets added to the production queue?";
    if (await window.mpsConfirm({title: "Start run mode", message: startMessage}) !== "primary") return;
  }
  runModeStartPending = true;
  runModeStopQueued = false;
  pendingRunModeRequestId = newRunModeRequestId();
  renderRunMode();
  try {
    board = await api("/api/run-mode/start", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, request_id: pendingRunModeRequestId, loaded_machine_action: loadedMachineAction}),
    });
    renderBoard();
    if (runModeStopQueued) {
      board = await api("/api/run-mode/stop", {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision}),
      });
      renderBoard();
      showToast("Run Mode start cancelled.");
    } else {
      showToast("Run mode start requested. Controller checks are running.");
    }
  } catch (error) {
    showToast(error.message, "error");
    try {
      board = await api("/api/board");
      renderBoard();
      if (reconcilePendingRunModeStart(board)) {
        renderRunMode();
        showToast("Run Mode did not start. The control is ready to try again.", "error");
        return;
      }
    } catch (_refreshError) {
      showToast("Run Mode start status is unknown. The control remains locked until the connection is restored.", "error");
      return;
    }
  }
  runModeStartPending = false;
  runModeStopQueued = false;
  pendingRunModeRequestId = null;
  renderRunMode();
});

ui.resumeQueueAfterManualRobot.addEventListener("click", async () => {
  ui.resumeQueueAfterManualRobot.disabled = true;
  try {
    board = await api("/api/run-mode/resume-after-manual-robot", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision}),
    });
    renderBoard();
    showToast("Manual robot control ended. Run Mode will continue safely.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    ui.resumeQueueAfterManualRobot.disabled = false;
  }
});

async function answerRunConfirmation(approved) {
  const token = board.run_mode?.confirmation_token;
  if (!token) return;
  try {
    board = await api("/api/run-mode/confirm", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, token, approved}),
    });
    ui.runConfirmDialog.close();
    renderBoard();
    showToast(approved ? "Run-mode action approved." : "Run mode stopped.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

document.querySelector("#run-confirm-approve").addEventListener("click", () => answerRunConfirmation(true));
document.querySelector("#run-confirm-stop").addEventListener("click", () => answerRunConfirmation(false));
ui.runConfirmDialog.addEventListener("cancel", event => {
  event.preventDefault();
  answerRunConfirmation(false);
});

ui.palletForm.addEventListener("submit", savePallet);
document.querySelectorAll("[data-close-pallet]").forEach(button => {
  button.addEventListener("click", () => ui.palletDialog.close());
});

document.addEventListener("click", async event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  const card = event.target.closest(".pallet-card");
  const pallet = palletById(card?.dataset.palletId);
  if (!pallet) return;
  if (action === "edit") openPalletDialog(pallet);
  if (action === "queue") queuePallet(pallet.id);
  if (action === "dequeue") {
    await mutate(
      `/api/pallets/${pallet.id}/queue?expected_revision=${board.revision}`,
      {method: "DELETE"},
      `${pallet.name} removed from the queue.`,
    );
  }
  if (action === "pick") startRobotMotion("pick", pallet.pool_slot_number, pallet.id);
  if (action === "automatic-put-away") {
    const preferred = pallet.return_pool_slot_number
      ? `Pool ${String(pallet.return_pool_slot_number).padStart(2, "0")}`
      : "the best available pool position";
    askConfirmation(
      "Put away pallet",
      `Use Mongo to return ${pallet.name} to ${preferred}? If that position is unavailable, the nearest unreserved position will be used.`,
      async () => {
        await mutate(`/api/pallets/${pallet.id}/put-away`, {
          method: "POST",
          body: JSON.stringify({expected_revision: board.revision}),
        }, `Mongo is putting away ${pallet.name}.`);
      },
    );
  }
  if (action === "mongo-unload") openMillPutAwayDialog(pallet);
  if (action === "send-to-pool") openStorageSendToPoolDialog(pallet);
  if (action === "return-to-storage") {
    askConfirmation(
      "Return pallet to storage",
      `Record ${pallet.name} in Storage? This is a schedule-only update and removes it from the production queue if it is queued. No robot or mill command will be sent.`,
      () => movePallet(pallet.id, "storage"),
    );
  }
  if (action === "manual-return-to-pool") {
    const preferred = pallet.return_pool_slot_number
      ? `Pool ${String(pallet.return_pool_slot_number).padStart(2, "0")}`
      : "the first available pool position";
    askConfirmation(
      "Record manual return to pool",
      `Confirm that ${pallet.name} is physically out of the mill and already back in the pallet pool. The schedule will place it in ${preferred}. This sends no robot or mill command.`,
      async () => {
        await mutate(`/api/pallets/${pallet.id}/manual-return-to-pool`, {
          method: "POST",
          body: JSON.stringify({expected_revision: board.revision}),
        }, `${pallet.name} was recorded back in the pallet pool. No controller command was sent.`);
      },
    );
  }
  if (action === "duplicate") {
    askConfirmation("Duplicate pallet", `Create a pool copy of ${pallet.name}?`, async () => {
      await mutate(`/api/pallets/${pallet.id}/duplicate`, {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision}),
      }, `${pallet.name} duplicated.`);
    });
  }
  if (action === "delete") {
    const program = pallet.program_path || "no assigned program";
    askConfirmation(
      "Delete pallet",
      `Permanently delete ${pallet.name} from ${pallet.location}? It has ${program}.`,
      async () => {
        await mutate(`/api/pallets/${pallet.id}?expected_revision=${board.revision}`, {
          method: "DELETE",
        }, `${pallet.name} deleted.`);
      },
    );
  }
});

async function startRobotMotion(operation, poolSlotNumber, palletId = null) {
  try {
    board = await api("/api/robot-motions", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, operation, pool_slot_number: poolSlotNumber, pallet_id: palletId}),
    });
    renderBoard();
    showToast(operation === "pick" ? "Pick command sent to Mongo." : "Put-away command sent to Mongo.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function openMillPutAwayDialog(pallet) {
  const openSlots = availablePoolSlots(pallet);
  if (!openSlots.length) {
    showToast("No empty pallet-pool positions are available.", "error");
    return;
  }
  document.querySelector("#mill-putaway-pallet-id").value = pallet.id;
  document.querySelector("#mill-putaway-pallet-name").textContent = pallet.name;
  document.querySelector("#mill-putaway-slot").innerHTML = openSlots
    .map(slot => `<option value="${slot}">Pool ${String(slot).padStart(2, "0")}</option>`).join("");
  document.querySelector("#mill-putaway-dialog").showModal();
}

function availablePoolSlots(pallet) {
  return Array.from({length: board.settings.pool_slot_count}, (_, index) => index + 1)
    .filter(slot => !board.pallets.some(item =>
      (item.location === "pool" && item.pool_slot_number === slot)
      || (item.id !== pallet.id && ["machine", "robot_held"].includes(item.location) && item.return_pool_slot_number === slot),
    ));
}

function openStorageSendToPoolDialog(pallet) {
  const openSlots = availablePoolSlots(pallet);
  if (!openSlots.length) {
    showToast("No empty pallet-pool positions are available.", "error");
    return;
  }
  const suggestedSlot = openSlots[0];
  const input = document.querySelector("#storage-send-to-pool-slot");
  document.querySelector("#storage-send-to-pool-pallet-id").value = pallet.id;
  document.querySelector("#storage-send-to-pool-pallet-name").textContent = pallet.name;
  document.querySelector("#storage-send-to-pool-suggestion").textContent = `Suggested position: Pool ${String(suggestedSlot).padStart(2, "0")}. Enter another open position if needed.`;
  input.max = String(board.settings.pool_slot_count);
  input.value = String(suggestedSlot);
  document.querySelector("#storage-send-to-pool-dialog").showModal();
}

async function startMillTransfer(operation, palletId = null, poolSlotNumber = null) {
  try {
    board = await api("/api/robot-motions/mill-transfer", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, operation, pallet_id: palletId, pool_slot_number: poolSlotNumber}),
    });
    renderBoard();
    showToast(operation === "load" ? "Mongo is loading the pallet into the mill." : "Mongo is unloading and putting away the pallet.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

document.querySelector("#mill-putaway-form").addEventListener("submit", async event => {
  event.preventDefault();
  const palletId = document.querySelector("#mill-putaway-pallet-id").value;
  const slot = Number(document.querySelector("#mill-putaway-slot").value);
  document.querySelector("#mill-putaway-dialog").close();
  await startMillTransfer("unload", palletId, slot);
});
document.querySelector("#cancel-mill-putaway").addEventListener("click", () => {
  document.querySelector("#mill-putaway-dialog").close();
});

document.querySelector("#storage-send-to-pool-form").addEventListener("submit", async event => {
  event.preventDefault();
  const palletId = document.querySelector("#storage-send-to-pool-pallet-id").value;
  const slot = Number(document.querySelector("#storage-send-to-pool-slot").value);
  if (!Number.isInteger(slot) || slot < 1 || slot > board.settings.pool_slot_count) {
    showToast(`Enter a pool position from 1 to ${board.settings.pool_slot_count}.`, "error");
    return;
  }
  document.querySelector("#storage-send-to-pool-dialog").close();
  const name = palletById(palletId)?.name || "Pallet";
  await mutate(`/api/pallets/${palletId}/move`, {
    method: "POST",
    body: JSON.stringify({
      expected_revision: board.revision,
      destination: "pool",
      pool_slot_number: slot,
    }),
  }, `${name} was recorded in Pool ${String(slot).padStart(2, "0")}. No controller command was sent.`);
});
document.querySelector("#cancel-storage-send-to-pool").addEventListener("click", () => {
  document.querySelector("#storage-send-to-pool-dialog").close();
});

document.addEventListener("click", event => {
  const put = event.target.closest("[data-put-slot]");
  if (put) startRobotMotion("put", Number(put.dataset.putSlot));
});

document.querySelector("#confirm-action").addEventListener("click", async event => {
  event.preventDefault();
  ui.confirmDialog.close();
  if (confirmCallback) await confirmCallback();
  confirmCallback = null;
});

document.querySelector("#apply-autoschedule").addEventListener("click", async () => {
  if (!autoschedulePlan?.can_apply) return;
  const button = document.querySelector("#apply-autoschedule");
  button.disabled = true;
  button.textContent = "Applying...";
  try {
    board = await api("/api/queue", {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: autoschedulePlan.revision,
        pallet_ids: autoschedulePlan.optimized.pallet_ids,
      }),
    });
    ui.autoscheduleDialog.close();
    renderBoard();
    showToast(`Queue optimized. Estimated ${autoschedulePlan.savings.tool_movements} fewer tool movements.`);
    autoschedulePlan = null;
  } catch (error) {
    button.disabled = false;
    button.textContent = "Apply optimized order";
    showToast(error.message, "error");
  }
});

document.addEventListener("dragstart", event => {
  const card = event.target.closest(".pallet-card");
  if (!card) return;
  const pallet = palletById(card.dataset.palletId);
  if (
    pallet?.location === "machine"
    || pallet?.location === "robot_held"
    || pallet?.id === board.robot_motion?.active?.pallet_id
  ) {
    event.preventDefault();
    return;
  }
  draggedPalletId = card.dataset.palletId;
  draggedCardContext = card.dataset.cardContext;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", draggedPalletId);
  card.classList.add("dragging");
});

document.addEventListener("dragend", event => {
  event.target.closest(".pallet-card")?.classList.remove("dragging");
  document.querySelectorAll(".drag-over").forEach(item => item.classList.remove("drag-over"));
  draggedPalletId = null;
  draggedCardContext = null;
});

document.addEventListener("dragover", event => {
  const target = event.target.closest(".drop-target");
  if (!target || !draggedPalletId) return;
  event.preventDefault();
  target.classList.add("drag-over");
});

document.addEventListener("dragleave", event => {
  event.target.closest(".drop-target")?.classList.remove("drag-over");
});

document.addEventListener("drop", async event => {
  const target = event.target.closest(".drop-target");
  if (!target || !draggedPalletId) return;
  event.preventDefault();
  const destination = target.dataset.destination;
  let queueIndex = null;
  if (destination === "queue") {
    const card = event.target.closest(".pallet-card");
    if (card && card.dataset.palletId !== draggedPalletId) {
      queueIndex = [...ui.queue.querySelectorAll(".pallet-card")].indexOf(card);
    }
    await queuePallet(draggedPalletId, queueIndex);
    return;
  }
  if (
    destination === "pool"
    && draggedCardContext === "queue"
    && palletById(draggedPalletId)?.location === "pool"
  ) {
    await mutate(
      `/api/pallets/${draggedPalletId}/queue?expected_revision=${board.revision}`,
      {method: "DELETE"},
      `Removed ${palletById(draggedPalletId)?.name || "pallet"} from the queue.`,
    );
    return;
  }
  if (destination === "machine" && palletById(draggedPalletId)?.location === "pool") {
    const pallet = palletById(draggedPalletId);
    const choice = await window.mpsConfirm({
      eyebrow: "Mill transfer",
      title: `Load ${pallet.name} into the mill?`,
      message: `Use Mongo to move this pallet from Pool ${String(pallet.pool_slot_number).padStart(2, "0")} into the mill.`,
      tone: "warning",
      primaryLabel: "Use Mongo",
      secondaryLabel: "Update schedule only",
    });
    if (choice === "primary") {
      await startMillTransfer("load", pallet.id);
    } else if (choice === "secondary") {
      await movePallet(draggedPalletId, destination);
    }
    return;
  }
  if (destination === "machine" && palletById(draggedPalletId)?.location === "robot_held") {
    const pallet = palletById(draggedPalletId);
    const choice = await window.mpsConfirm({
      eyebrow: "Mill transfer",
      title: `Load ${pallet.name} into the mill?`,
      message: "Mongo will first run the mill loading-position program, then load the Robot-held pallet.",
      tone: "warning",
      primaryLabel: "Use Mongo",
    });
    if (choice === "primary") await startMillTransfer("load", pallet.id);
    return;
  }
  await movePallet(
    draggedPalletId,
    destination,
    target.dataset.poolSlot ? Number(target.dataset.poolSlot) : null,
  );
});

document.querySelector("#refresh-programs").addEventListener("click", async () => {
  try {
    const result = await api("/api/programs/refresh", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision}),
    });
    board = result.board;
    renderBoard();
    if (ui.palletDialog.open) {
      palletDialogPrograms = [...(result.programs || board.programs || [])];
      renderProgramOptions(document.querySelector("#pallet-program").value, palletDialogPrograms);
    }
    if (board.program_warning) {
      showToast(`Programs could not be refreshed: ${board.program_warning}`, "error");
      return;
    }
    const suffix = result.cleared_assignments.length
      ? ` Cleared assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    const refreshedPrograms = result.programs || board.programs || [];
    const metadataCount = result.metadata_refreshed
      ?? board.pallets.filter(pallet => pallet.program_path).length;
    showToast(
      `Refreshed ${refreshedPrograms.length} programs and metadata for ${metadataCount} assigned programs.${suffix}`,
    );
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll("[data-debug-signal]").forEach(button => {
  button.addEventListener("click", async () => {
    const signal = button.dataset.debugSignal;
    try {
      board = await api(`/api/debug/signals/${signal}`, {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision}),
      });
      renderBoard();
      const messages = {
        complete: "Simulated completed job and Pool unload.",
        out_of_spec: "Simulated out-of-spec job and Pool unload.",
        error: "Simulated machine error.",
      };
      showToast(messages[signal]);
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

void loadRecoveryStatus();
pollBoard();
