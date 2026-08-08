const ui = {
  state: document.querySelector("#system-state"), queueTime: document.querySelector("#queue-time"), queueCount: document.querySelector("#queue-count"), currentCycle: document.querySelector("#current-cycle"), currentPallet: document.querySelector("#current-pallet"), queueTools: document.querySelector("#queue-tools"), atcTools: document.querySelector("#atc-tools"), queue: document.querySelector("#dashboard-queue"), updated: document.querySelector("#dashboard-updated"), cameraGrid: document.querySelector("#camera-grid"), cameraPhase: document.querySelector("#camera-phase"), completions: document.querySelector("#program-completions"), toast: document.querySelector("#toast"),
};
let cameraRenderSignature = "";
let dashboard = null;
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
function duration(seconds) { if (!Number.isFinite(seconds)) return "--"; const whole = Math.max(0, Math.round(seconds)); return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`; }
function elapsedMachiningSeconds(data) {
  if (!data.current_cycle_started_at) return 0;
  const started = Date.parse(data.current_cycle_started_at);
  return Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
}
function currentRemainingSeconds(data) {
  if (!data.machine_pallet || !Number.isFinite(data.current_cycle_seconds)) return null;
  return Math.max(0, data.current_cycle_seconds - elapsedMachiningSeconds(data));
}
function queueRemainingSeconds(data) {
  let total = data.queue.reduce((sum, pallet) => sum + (Number(pallet.estimated_cycle_seconds ?? pallet.expected_cycle_seconds) || 0), 0);
  if (!data.machine_pallet) return total;
  const elapsed = elapsedMachiningSeconds(data);
  if (data.queue.some(pallet => pallet.id === data.machine_pallet.id)) return Math.max(0, total - elapsed);
  return total + (currentRemainingSeconds(data) || 0);
}
function renderTimers() {
  if (!dashboard) return;
  ui.queueTime.textContent = duration(queueRemainingSeconds(dashboard));
  ui.currentCycle.textContent = duration(currentRemainingSeconds(dashboard));
}
function toolChips(tools, empty, states = {}) { return tools.length ? tools.map(tool => { const status = states?.[tool.slice(1)]?.status || "unknown"; return `<span class="tool-chip tool-status-${escapeHtml(status)}">${escapeHtml(tool)}</span>`; }).join("") : `<span class="muted">${empty}</span>`; }
function renderCameras(cameras) {
  const list = cameras?.cameras || [];
  ui.cameraPhase.textContent = cameras?.phase ? `${cameras.phase[0].toUpperCase()}${cameras.phase.slice(1)}` : "Idle";
  const signature = JSON.stringify({active: cameras?.active_camera_id, fallback: cameras?.fallback, cameras: list.map(camera => [camera.id, camera.status, camera.error, camera.name])});
  if (signature === cameraRenderSignature) return;
  cameraRenderSignature = signature;
  if (!list.length) {
    ui.cameraGrid.innerHTML = `<p class="debug-table-empty">No cameras configured.</p>`;
    return;
  }
  const activeId = cameras.active_camera_id;
  ui.cameraGrid.innerHTML = list.map(camera => {
    const active = camera.id === activeId;
    const offline = !camera.status.startsWith("online");
    return `<article class="camera-tile ${active ? "active" : ""} ${offline ? "offline" : ""}">
      <header><strong>${escapeHtml(camera.name)}</strong><span>${active ? (cameras.fallback ? "Fallback view" : "Active view") : escapeHtml(camera.status)}</span></header>
      <div class="camera-frame">${offline ? `<div class="camera-offline">${escapeHtml(camera.error || "Camera offline")}</div>` : `<img src="${escapeHtml(camera.stream_url)}" alt="Live feed from ${escapeHtml(camera.name)}">`}</div>
    </article>`;
  }).join("");
}
function renderProgramCompletions(items) {
  if (!items?.length) {
    ui.completions.innerHTML = `<p class="debug-table-empty">Assign a program to a pallet to begin tracking its completed runs.</p>`;
    return;
  }
  ui.completions.innerHTML = items.map(item => `<article class="program-completion-row" data-program-path="${escapeHtml(item.program_path)}">
    <code>${escapeHtml(item.program_path)}</code>
    <label>Completed <input type="number" min="0" max="1000000000" value="${Number(item.completed_count) || 0}" aria-label="Completed runs for ${escapeHtml(item.program_path)}"></label>
    <small>${item.measured_run_count ? `Measured average: ${duration(item.average_run_seconds)} from ${item.measured_run_count} run${item.measured_run_count === 1 ? "" : "s"}` : "Using the posted cycle estimate until the first completed run."}</small>
    <button class="button secondary" type="button" data-save-completion>Save</button>
    <button class="button danger" type="button" data-reset-completion>Reset</button>
  </article>`).join("");
}
function render(data) {
  dashboard = data;
  renderTimers(); ui.queueCount.textContent = `${data.queue.length} pallet${data.queue.length === 1 ? "" : "s"}`;
  ui.currentPallet.textContent = data.machine_pallet ? `${data.machine_pallet.name}${data.current_cycle_started_at ? " — remaining" : ""}` : "No pallet in Mill";
  ui.queueTools.innerHTML = toolChips(data.queue_tools, "No queued program tools", data.queue_tool_states); ui.atcTools.innerHTML = toolChips(data.atc_tools, data.atc_source || "Mill telemetry not connected", Object.fromEntries(data.atc_tools.map(tool => [tool.slice(1), {status: "atc"}])));
  ui.queue.innerHTML = data.queue.length ? data.queue.map((pallet, index) => { const active = data.machine_pallet?.id === pallet.id; const time = active ? currentRemainingSeconds(data) : (pallet.estimated_cycle_seconds ?? pallet.expected_cycle_seconds); return `<article class="dashboard-queue-item"><span class="queue-number">${index + 1}</span><div><strong>${escapeHtml(pallet.name)}</strong><small>${escapeHtml(pallet.program_path || "No program assigned")}</small><small>${active ? "Machining — remaining" : escapeHtml(pallet.estimate_source || "Posted estimate")}</small></div><div>${toolChips(pallet.program_tools, "No active tools")}</div><strong>${duration(time)}</strong></article>`; }).join("") : `<p class="debug-table-empty">No pallets are queued.</p>`;
  renderCameras(data.cameras);
  renderProgramCompletions(data.program_completions);
  ui.updated.textContent = `Updated ${new Date().toLocaleTimeString()}`; ui.state.classList.add("online"); ui.state.lastChild.textContent = " Online";
}
async function updateCompletion(row, reset = false) {
  const input = row.querySelector("input");
  const completedCount = Number(input.value);
  if (!Number.isInteger(completedCount) || completedCount < 0) {
    input.focus();
    return;
  }
  const response = await fetch(reset ? "/api/program-completions/reset" : "/api/program-completions", {
    method: reset ? "POST" : "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({expected_revision: dashboard.revision, program_path: row.dataset.programPath, completed_count: reset ? 0 : completedCount}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Could not update program total.");
  render(data);
}
ui.completions.addEventListener("click", async event => {
  const button = event.target.closest("[data-save-completion], [data-reset-completion]");
  if (!button) return;
  const reset = button.hasAttribute("data-reset-completion");
  if (reset && !window.confirm("Reset this program's completed-run total to zero?")) return;
  button.disabled = true;
  try { await updateCompletion(button.closest(".program-completion-row"), reset); }
  catch (error) { window.alert(error.message); }
  finally { button.disabled = false; }
});
async function load() { try { const response = await fetch("/api/dashboard", {cache: "no-store"}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Dashboard unavailable"); render(data); const settings = await (await fetch("/api/settings", {cache: "no-store"})).json(); document.querySelectorAll("[data-robot-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.robot_programs_page_enabled)); document.querySelectorAll("[data-mill-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.mill_programs_page_enabled)); } catch (error) { ui.state.lastChild.textContent = " Unavailable"; } }
async function poll() { if (!document.hidden) await load(); window.setTimeout(poll, 5000); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
window.setInterval(() => { if (!document.hidden) renderTimers(); }, 1000);
poll();
