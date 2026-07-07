const ui = {
  state: document.querySelector("#system-state"),
  toast: document.querySelector("#toast"),
  notes: document.querySelector("#debug-notes"),
  connectionLight: document.querySelector("#debug-connection-light"),
  connectionLabel: document.querySelector("#debug-connection-label"),
  source: document.querySelector("#debug-source"),
  machineState: document.querySelector("#debug-machine-state"),
  timestamp: document.querySelector("#debug-timestamp"),
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
  actualRows: document.querySelector("#actual-rows"),
};

let lastError = "";
let snapshotState = null;

function showToast(message, kind = "success") {
  ui.toast.textContent = message;
  ui.toast.className = `toast ${kind}`;
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => ui.toast.classList.add("hidden"), 4500);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed with status ${response.status}`);
  return data;
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

function tableEmpty(columns, label) {
  return `<tr><td colspan="${columns}" class="debug-table-empty">${escapeHtml(label)}</td></tr>`;
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

function render(snapshot) {
  snapshotState = snapshot;
  ui.notes.textContent = snapshot.notes;
  ui.connectionLight.className = `debug-connection-light ${snapshot.connected ? "active" : "unknown"}`;
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
  } catch (error) {
    ui.state.lastChild.textContent = " Unavailable";
    if (error.message !== lastError) {
      showToast(error.message, "error");
      lastError = error.message;
    }
  }
}

document.addEventListener("click", async event => {
  const renameButton = event.target.closest("[data-rename-debug-io]");
  if (renameButton && snapshotState) {
    const currentLabel = renameButton.dataset.debugLabel || "";
    const nextLabel = window.prompt("Enter a label for this I/O port. Leave blank to reset to the hardware name.", currentLabel);
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

loadDebugging();
window.setInterval(loadDebugging, 2000);
