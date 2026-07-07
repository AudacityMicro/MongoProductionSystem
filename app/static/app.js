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
  storage: document.querySelector("#storage-list"),
  warning: document.querySelector("#program-warning"),
  toast: document.querySelector("#toast"),
  palletDialog: document.querySelector("#pallet-dialog"),
  palletForm: document.querySelector("#pallet-form"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  debugPanel: document.querySelector("#debug-panel"),
  debugState: document.querySelector("#debug-state"),
};

let board = null;
let draggedPalletId = null;
let draggedCardContext = null;
let confirmCallback = null;

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
    throw new Error(data.detail || `Request failed with status ${response.status}`);
  }
  return data;
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
  const queueAction = pallet.queue_position === null
    ? (pallet.location === "pool" ? `<button class="text-button" data-action="queue">Queue</button>` : "")
    : "";
  const queueBadge = pallet.queue_position !== null && position === null
    ? `<span class="queue-chip">Queued #${pallet.queue_position + 1}</span>`
    : "";
  const cardContext = position === null ? "physical" : "queue";
  return `
    <article class="pallet-card content-${pallet.content_status}" draggable="true"
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
      </dl>
      <div class="card-actions">
        ${queueAction}
        <button class="text-button" data-action="edit">Edit</button>
        <button class="text-button" data-action="duplicate">Duplicate</button>
        <button class="text-button danger-text" data-action="delete">Delete</button>
      </div>
    </article>`;
}

function renderBoard() {
  const pallets = board.pallets;
  const queue = pallets.filter(item => item.queue_position !== null)
    .sort((a, b) => a.queue_position - b.queue_position);
  const pool = pallets.filter(item => item.location === "pool")
    .sort((a, b) => a.pool_slot_number - b.pool_slot_number);
  const machine = pallets.find(item => item.location === "machine");
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
        ${occupant ? palletCard(occupant) : emptyState("Available")}
      </div>`;
    },
  ).join("");
  ui.machine.innerHTML = machine
    ? palletCard(machine)
    : emptyState("Machine is available");

  ui.storage.innerHTML = stored.length
    ? stored.map(item => palletCard(item)).join("")
    : emptyState("Stored pallets appear here");

  document.querySelector("#queue-count").textContent = `${queue.length} pallet${queue.length === 1 ? "" : "s"}`;
  document.querySelector("#pool-count").textContent = `${pool.length} pallet${pool.length === 1 ? "" : "s"}`;
  document.querySelector("#storage-count").textContent = `${stored.length} pallet${stored.length === 1 ? "" : "s"}`;
  document.querySelector("#weight-unit-label").textContent = `(${board.settings.weight_unit})`;
  document.querySelector("#program-options").innerHTML = board.programs
    .map(program => `<option value="${escapeHtml(program)}"></option>`).join("");

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

document.querySelector("#create-pallet").addEventListener("click", () => openPalletDialog());
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

document.querySelector("#confirm-action").addEventListener("click", async event => {
  event.preventDefault();
  ui.confirmDialog.close();
  if (confirmCallback) await confirmCallback();
  confirmCallback = null;
});

document.addEventListener("dragstart", event => {
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
