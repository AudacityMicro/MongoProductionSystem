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
  motionRecoveryDialog: document.querySelector("#motion-recovery-dialog"),
  motionRecoveryForm: document.querySelector("#motion-recovery-form"),
  runModeToggle: document.querySelector("#run-mode-toggle"),
  runSafetyConfirm: document.querySelector("#run-safety-confirm"),
  runModeStatus: document.querySelector("#run-mode-status"),
  runConfirmDialog: document.querySelector("#run-confirm-dialog"),
};

let board = null;
let draggedPalletId = null;
let draggedCardContext = null;
let confirmCallback = null;
let autoschedulePlan = null;
let shownRunConfirmationToken = null;

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
  ui.toast.textContent = message;
  ui.toast.className = `toast ${kind}`;
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => ui.toast.classList.add("hidden"), 4200);
}

function emptyState(label) {
  return `<div class="zone-empty"><span>+</span><p>${escapeHtml(label)}</p></div>`;
}

function palletCard(pallet, position = null) {
  const program = pallet.program_path || "No program";
  const runLocked = Boolean(board.run_mode?.enabled);
  const queueAction = !runLocked && pallet.queue_position === null
    ? (pallet.location === "pool" ? `<button class="text-button" data-action="queue">Queue</button>` : "")
    : "";
  const motionLocked = Boolean(board.robot_motion?.active) || runLocked;
  const pickAction = pallet.location === "pool" && !motionLocked
    ? `<button class="text-button" data-action="pick">Pick</button>`
    : "";
  const millPutAwayAction = pallet.location === "machine" && !motionLocked
    ? `<button class="text-button" data-action="mongo-unload">Put away with Mongo</button>`
    : "";
  const queueBadge = pallet.queue_position !== null && position === null
    ? `<span class="queue-chip">Queued #${pallet.queue_position + 1}</span>`
    : "";
  const cardContext = position === null ? "physical" : "queue";
  const programDetails = pallet.program_tools?.length && pallet.expected_cycle_seconds
    ? `<div><dt>Tools</dt><dd>${escapeHtml(pallet.program_tools.join(", "))}</dd></div>
       <div><dt>Cycle</dt><dd>${displayCycleTime(pallet.expected_cycle_seconds)}</dd></div>`
    : "";
  return `
    <article class="pallet-card content-${pallet.content_status}" draggable="${runLocked ? "false" : "true"}"
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
        ${pickAction}
        ${millPutAwayAction}
        ${runLocked ? "" : `<button class="text-button" data-action="edit">Edit</button>
        <button class="text-button" data-action="duplicate">Duplicate</button>
        <button class="text-button danger-text" data-action="delete">Delete</button>`}
      </div>
    </article>`;
}

function renderBoard() {
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
      return `<div class="pool-position drop-target ${occupant ? "occupied" : ""}"
        data-destination="pool" data-pool-slot="${number}">
        <header><span>${String(number).padStart(2, "0")}</span><small>Pool position</small></header>
        ${occupant ? palletCard(occupant) : (robotHeld && !board.robot_motion?.active ? `<button class="button secondary pool-put-action" type="button" data-put-slot="${number}">Put Robot-held pallet here</button>` : emptyState("Available"))}
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
  document.querySelector("#create-pallet").disabled = Boolean(board.run_mode?.enabled);
  if (board.run_mode?.enabled) document.querySelector("#autoschedule-queue").disabled = true;
  document.querySelector("#pool-count").textContent = `${pool.length} pallet${pool.length === 1 ? "" : "s"}`;
  document.querySelector("#storage-count").textContent = `${stored.length} pallet${stored.length === 1 ? "" : "s"}`;
  document.querySelector("#weight-unit-label").textContent = `(${board.settings.weight_unit})`;
  document.querySelector("#program-options").innerHTML = board.programs
    .map(program => `<option value="${escapeHtml(program)}"></option>`).join("");
  document.querySelector("#workholding-options").innerHTML = (board.settings.workholding_library || [])
    .map(workholding => `<option value="${escapeHtml(workholding)}"></option>`).join("");
  renderRobotMotionStatus();
  renderRunMode();

  ui.warning.classList.toggle("hidden", !board.program_warning);
  ui.warning.textContent = board.program_warning || "";
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
  ui.robotMotionStatus.classList.toggle("hidden", !motion);
  if (!motion) return;
  const target = motion.operation === "pick"
    ? `Pool ${String(motion.source_slot).padStart(2, "0")}`
    : motion.operation === "put"
      ? `Pool ${String(motion.destination_slot).padStart(2, "0")}`
      : motion.operation === "load_mill"
        ? `Pool ${String(motion.source_slot).padStart(2, "0")} -> Mill`
        : `Mill -> Pool ${String(motion.destination_slot).padStart(2, "0")}`;
  const status = motion.status === "faulted" ? "Movement fault" : motion.status === "running" ? "Robot moving" : "Movement requested";
  const action = motion.status === "faulted"
    ? `<button class="button danger" type="button" id="recover-robot-motion">Recover</button>`
    : "";
  ui.robotMotionStatus.className = `robot-motion-status ${motion.status}`;
  ui.robotMotionStatus.innerHTML = `<div><strong>${status}: ${escapeHtml(motion.pallet_name || "Pallet")}</strong><span>${escapeHtml(motion.operation)} ${target} | ${escapeHtml(motion.program_path)}${motion.failure_detail ? ` | ${escapeHtml(motion.failure_detail)}` : ""}</span></div>${action}`;
}

function renderRunMode() {
  const run = board.run_mode || {};
  ui.runSafetyConfirm.checked = Boolean(run.safety_confirm);
  ui.runSafetyConfirm.disabled = Boolean(run.enabled);
  ui.runModeToggle.textContent = run.enabled ? "Stop run mode" : "Start run mode";
  ui.runModeToggle.classList.toggle("active", Boolean(run.enabled));
  ui.runModeStatus.className = `run-mode-status ${escapeHtml(run.state || "idle")}`;
  const pallet = run.current_pallet_name ? ` · ${escapeHtml(run.current_pallet_name)}` : "";
  const safety = run.safety_confirm ? "Step confirmation on" : "Step confirmation off";
  ui.runModeStatus.innerHTML = `<div><span class="run-mode-light"></span><strong>${run.enabled ? "Run mode active" : "Run mode " + escapeHtml(run.state || "idle")}${pallet}</strong></div><span>${escapeHtml(run.detail || "Ready to process the production queue in order.")} · ${safety}</span>`;

  if (run.confirmation_token && run.pending_action && shownRunConfirmationToken !== run.confirmation_token) {
    shownRunConfirmationToken = run.confirmation_token;
    document.querySelector("#run-confirm-title").textContent = `Approve ${run.pending_action.replaceAll("_", " ")}`;
    document.querySelector("#run-confirm-message").textContent = run.detail;
    if (!ui.runConfirmDialog.open) ui.runConfirmDialog.showModal();
  }
  if (!run.confirmation_token) {
    shownRunConfirmationToken = null;
    if (ui.runConfirmDialog.open) ui.runConfirmDialog.close();
  }
}

async function loadBoard() {
  try {
    board = await api("/api/board");
    renderBoard();
  } catch (error) {
    ui.state.classList.remove("online");
    ui.state.lastChild.textContent = " Unavailable";
    showToast(error.message, "error");
  }
}

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
  document.querySelector("#pallet-program").value = pallet?.program_path || "";
  ui.palletDialog.showModal();
  (focusProgram ? document.querySelector("#pallet-program") : document.querySelector("#pallet-workholding")).focus();
}

async function savePallet(event) {
  event.preventDefault();
  if (!ui.palletForm.reportValidity()) return;
  const id = document.querySelector("#pallet-id").value;
  const program = document.querySelector("#pallet-program").value.trim();
  if (program && !board.programs.includes(program)) {
    showToast("Choose a program from the configured source folder.", "error");
    return;
  }
  const payload = {
    expected_revision: board.revision,
    workholding: document.querySelector("#pallet-workholding").value,
    weight_kg: canonicalWeight(Number(document.querySelector("#pallet-weight").value)),
    content_status: document.querySelector("#pallet-contents").value,
    program_path: program || null,
  };
  try {
    board = await api(id ? `/api/pallets/${id}` : "/api/pallets", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    ui.palletDialog.close();
    renderBoard();
    showToast(id ? "Pallet updated." : "Pallet created.");
  } catch (error) {
    showToast(error.message, "error");
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

ui.runSafetyConfirm.addEventListener("change", async () => {
  const enabled = ui.runSafetyConfirm.checked;
  try {
    board = await api("/api/run-mode/safety", {
      method: "POST",
      body: JSON.stringify({expected_revision: board.revision, enabled}),
    });
    renderBoard();
    showToast(enabled ? "Run-step confirmations enabled." : "Run-step confirmations disabled.");
  } catch (error) {
    ui.runSafetyConfirm.checked = !enabled;
    showToast(error.message, "error");
  }
});

ui.runModeToggle.addEventListener("click", () => {
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
    try {
      board = await api("/api/run-mode/start", {
        method: "POST",
        body: JSON.stringify({expected_revision: board.revision, safety_confirm: ui.runSafetyConfirm.checked}),
      });
      renderBoard();
      showToast("Run mode started.");
    } catch (error) {
      showToast(error.message, "error");
    }
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

document.addEventListener("click", event => {
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  const card = event.target.closest(".pallet-card");
  const pallet = palletById(card?.dataset.palletId);
  if (!pallet) return;
  if (action === "edit") openPalletDialog(pallet);
  if (action === "queue") queuePallet(pallet.id);
  if (action === "pick") startRobotMotion("pick", pallet.pool_slot_number, pallet.id);
  if (action === "mongo-unload") openMillPutAwayDialog(pallet);
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
    .filter(slot => !board.pallets.some(item => item.location === "pool" && item.pool_slot_number === slot));
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

document.addEventListener("click", event => {
  const put = event.target.closest("[data-put-slot]");
  if (put) startRobotMotion("put", Number(put.dataset.putSlot));
  const recover = event.target.closest("#recover-robot-motion");
  if (!recover) return;
  const motion = board.robot_motion.active;
  const options = motion.operation === "pick"
    ? [["source_pool", `Return to Pool ${String(motion.source_slot).padStart(2, "0")}`], ["robot_held", "Robot-held"]]
    : motion.operation === "put"
      ? [["robot_held", "Robot-held"], ["destination_pool", `Pool ${String(motion.destination_slot).padStart(2, "0")}`]]
      : motion.operation === "load_mill"
        ? [["source_pool", `Pool ${String(motion.source_slot).padStart(2, "0")}`], ["robot_held", "Robot-held"], ["machine", "Mill"]]
        : [["machine", "Mill"], ["robot_held", "Robot-held"], ["destination_pool", `Pool ${String(motion.destination_slot).padStart(2, "0")}`]];
  document.querySelector("#motion-recovery-message").textContent = `${motion.failure_detail || "Movement fault."} Verify the actual pallet location before saving.`;
  document.querySelector("#motion-recovery-resolution").innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  ui.motionRecoveryDialog.showModal();
});

ui.motionRecoveryForm.addEventListener("submit", async event => {
  event.preventDefault();
  const motion = board.robot_motion?.active;
  if (!motion) return;
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
  if (board.run_mode?.enabled) {
    event.preventDefault();
    return;
  }
  const card = event.target.closest(".pallet-card");
  if (!card) return;
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
    const suffix = result.cleared_assignments.length
      ? ` Cleared assignments from: ${result.cleared_assignments.join(", ")}.`
      : "";
    showToast(`Program list refreshed.${suffix}`);
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

loadBoard();
window.setInterval(loadBoard, 1500);
