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
  robotMotionRecover: document.querySelector("#recover-robot-motion"),
  robotMotionDismiss: document.querySelector("#dismiss-robot-motion"),
  motionRecoveryDialog: document.querySelector("#motion-recovery-dialog"),
  motionRecoveryForm: document.querySelector("#motion-recovery-form"),
  runModeToggle: document.querySelector("#run-mode-toggle"),
  runModeStatus: document.querySelector("#run-mode-status"),
  runConfirmDialog: document.querySelector("#run-confirm-dialog"),
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
  const queueAction = canManage && pallet.queue_position === null
    ? (pallet.location === "pool" ? `<button class="text-button" data-action="queue">Queue</button>` : "")
    : "";
  const dequeueAction = canManage && position !== null && pallet.queue_position !== null
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
  const queueBadge = pallet.queue_position !== null && position === null
    ? `<span class="queue-chip">Queued #${pallet.queue_position + 1}</span>`
    : "";
  const cardContext = position === null ? "physical" : "queue";
  const showProgramDetails = pallet.program_path && !["complete_parts", "defective_parts"].includes(pallet.content_status);
  const programDetails = !showProgramDetails
    ? ""
    : pallet.program_metadata_state === "parsed"
      ? `<div><dt>Tools</dt><dd>${escapeHtml(pallet.program_tools.join(", ") || "None")}</dd></div>
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
    ? stored.map(item => palletCard(item)).join("")
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

  const runAlert = board.run_mode?.alert || "";
  if (runAlert !== dismissedProgramWarning) dismissedProgramWarning = null;
  ui.warning.classList.toggle("hidden", !runAlert || dismissedProgramWarning === runAlert);
  ui.warningMessage.textContent = runAlert;
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
    ui.robotMotionRecover.classList.add("hidden");
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
  ui.robotMotionRecover.classList.toggle("hidden", motion.status !== "faulted");
  if (motion.status === "faulted") {
    const options = motion.operation === "pick"
      ? [["source_pool", `Return to Pool ${String(motion.source_slot).padStart(2, "0")}`], ["robot_held", "Robot-held"]]
      : motion.operation === "put"
        ? [["robot_held", "Robot-held"], ["destination_pool", `Pool ${String(motion.destination_slot).padStart(2, "0")}`]]
      : motion.operation === "load_mill"
          ? (motion.source_slot
            ? [["source_pool", `Pool ${String(motion.source_slot).padStart(2, "0")}`], ["robot_held", "Robot-held"], ["machine", "Mill"]]
            : [["robot_held", "Robot-held"], ["machine", "Mill"]])
          : [["machine", "Mill"], ["robot_held", "Robot-held"], ["destination_pool", `Pool ${String(motion.destination_slot).padStart(2, "0")}`]];
    document.querySelector("#motion-recovery-message").textContent = `${motion.failure_detail || "Movement fault."} Verify the actual pallet location before saving.`;
    document.querySelector("#motion-recovery-resolution").innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
    if (!ui.motionRecoveryDialog.open) ui.motionRecoveryDialog.showModal();
  }
}

ui.toast.addEventListener("click", event => {
  if (event.target.closest(".toast-dismiss")) ui.toast.classList.add("hidden");
});

ui.warningDismiss.addEventListener("click", async () => {
  dismissedProgramWarning = board?.run_mode?.alert || null;
  ui.warning.classList.add("hidden");
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
  ui.runModeStatus.className = `run-mode-status ${escapeHtml(run.state || "idle")}`;
  const pallet = run.current_pallet_name ? ` · ${escapeHtml(run.current_pallet_name)}` : "";
  const showDetail = (
    !run.enabled && ["faulted", "interrupted"].includes(run.state)
  ) || [
    "telemetry_unavailable",
    "telemetry_restored",
    "recovering_startup_telemetry",
    "recovering_cnc_telemetry",
    "recovering_robot_telemetry",
  ].includes(run.state);
  const detail = showDetail ? `<span>${escapeHtml(run.detail || "Run Mode needs operator attention.")}</span>` : "";
  const machinePallet = board.pallets.find(item => item.location === "machine");
  const recoveryActions = showDetail && machinePallet?.return_pool_slot_number
    ? `<div class="run-mode-recovery-actions">
        <button class="button secondary" type="button" data-recover-run-mode="retry_robot_only">Retry robot unload only</button>
        <button class="button ghost" type="button" data-recover-run-mode="reposition_and_retry">Reposition mill, then retry</button>
      </div>`
    : "";
  const clearStatus = !run.enabled && ["faulted", "interrupted"].includes(run.state)
    ? '<button class="notice-dismiss run-mode-clear" type="button" data-clear-run-mode-status>Clear warning</button>'
    : "";
  ui.runModeStatus.innerHTML = `<div><span class="run-mode-light"></span><strong>${run.enabled ? "Run mode active" : "Run mode " + escapeHtml(run.state || "idle")}${pallet}</strong></div>${detail}${recoveryActions}${clearStatus}`;

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

ui.runModeStatus.addEventListener("click", async event => {
  const recoveryButton = event.target.closest("[data-recover-run-mode]");
  if (recoveryButton && board) {
    const strategy = recoveryButton.dataset.recoverRunMode;
    const prompt = strategy === "retry_robot_only"
      ? "Confirm the mill is still at its loading position. Retry only the robot unload?"
      : "Move the mill to its loading position again, then retry the robot unload?";
    if (!window.confirm(prompt)) return;
    document.querySelectorAll("[data-recover-run-mode]").forEach(item => { item.disabled = true; });
    recoveryButton.textContent = "Starting recovery...";
    try {
      board = await api("/api/run-mode/recover", {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision, strategy}),
      });
      renderBoard();
      showToast("Run Mode recovery started.");
    } catch (error) {
      showToast(`Could not start recovery: ${error.message}`, "error");
      await loadBoard();
    }
    return;
  }
  const button = event.target.closest("[data-clear-run-mode-status]");
  if (!button || !board) return;
  button.disabled = true;
  button.textContent = "Clearing...";
  try {
    board = await api("/api/run-mode/status/clear", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision}),
    });
    renderBoard();
    showToast("Stale Run Mode warning cleared.");
  } catch (error) {
    showToast(`Could not clear warning: ${error.message}`, "error");
    await loadBoard();
  }
});

async function loadBoard() {
  if (boardLoadPromise) return boardLoadPromise;
  boardLoadPromise = (async () => {
    try {
      const nextBoard = await api("/api/board");
      const nextBoardKey = JSON.stringify(nextBoard);
      board = nextBoard;
      if (nextBoardKey !== renderedBoardKey) renderBoard();
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

function askConfirmation(title, message, callback) {
  document.querySelector("#confirm-title").textContent = title;
  document.querySelector("#confirm-message").textContent = message;
  confirmCallback = callback;
  ui.confirmDialog.showModal();
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

async function queuePallet(id, queueIndex = null) {
  await mutate(`/api/pallets/${id}/queue`, {
    method: "POST",
    body: JSON.stringify({
      expected_revision: board.revision,
      queue_index: queueIndex,
    }),
  }, `Queued ${palletById(id)?.name || "pallet"}.`);
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

ui.runModeToggle.addEventListener("click", () => {
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
  const queued = board.pallets.filter(item => item.queue_position !== null).length;
  askConfirmation("Start run mode", `Run all ${queued} queued pallet${queued === 1 ? "" : "s"} in order?`, async () => {
    runModeStartPending = true;
    runModeStopQueued = false;
    pendingRunModeRequestId = newRunModeRequestId();
    renderRunMode();
    try {
      board = await api("/api/run-mode/start", {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision, request_id: pendingRunModeRequestId}),
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
        if (!board.run_mode?.enabled) {
          showToast("The start request may still be in transit. The control remains locked until the connection is restored or the page is reloaded.", "error");
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
  const openSlots = Array.from({length: board.settings.pool_slot_count}, (_, index) => index + 1)
    .filter(slot => !board.pallets.some(item =>
      (item.location === "pool" && item.pool_slot_number === slot)
      || (item.id !== pallet.id && ["machine", "robot_held"].includes(item.location) && item.return_pool_slot_number === slot),
    ));
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

document.addEventListener("click", event => {
  const put = event.target.closest("[data-put-slot]");
  if (put) startRobotMotion("put", Number(put.dataset.putSlot));
});

ui.robotMotionRecover.addEventListener("click", () => {
  if (!ui.motionRecoveryDialog.open) ui.motionRecoveryDialog.showModal();
});
document.querySelector("#cancel-motion-recovery").addEventListener("click", () => {
  ui.motionRecoveryDialog.close();
});

ui.motionRecoveryForm.addEventListener("submit", async event => {
  event.preventDefault();
  const motion = board.robot_motion?.active;
  if (!motion) return;
  const saveButton = document.querySelector("#save-motion-recovery");
  saveButton.disabled = true;
  saveButton.textContent = "Saving...";
  try {
    board = await api(`/api/robot-motions/${motion.id}/recover`, {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, resolution: document.querySelector("#motion-recovery-resolution").value}),
    });
    ui.motionRecoveryDialog.close();
    renderBoard();
    showToast("Pallet movement fault reconciled.");
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "Save recovery";
  }
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
  if (pallet?.location === "machine" || pallet?.location === "robot_held") {
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
    const useMongo = window.confirm(`Use Mongo to move ${pallet.name} from Pool ${String(pallet.pool_slot_number).padStart(2, "0")} into the mill?\n\nOK: run the physical pick and mill-load sequence.\nCancel: update the schedule only.`);
    if (useMongo) {
      await startMillTransfer("load", pallet.id);
    } else {
      await movePallet(draggedPalletId, destination);
    }
    return;
  }
  if (destination === "machine" && palletById(draggedPalletId)?.location === "robot_held") {
    const pallet = palletById(draggedPalletId);
    const useMongo = window.confirm(
      `Load the Robot-held pallet ${pallet.name} into the mill?\n\nMongo will first run the mill loading-position program, then load the held pallet.`,
    );
    if (useMongo) await startMillTransfer("load", pallet.id);
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

pollBoard();
