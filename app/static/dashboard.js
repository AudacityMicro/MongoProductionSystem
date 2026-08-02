const ui = {
  state: document.querySelector("#system-state"), queueTime: document.querySelector("#queue-time"), queueCount: document.querySelector("#queue-count"), currentCycle: document.querySelector("#current-cycle"), currentPallet: document.querySelector("#current-pallet"), queueTools: document.querySelector("#queue-tools"), atcTools: document.querySelector("#atc-tools"), queue: document.querySelector("#dashboard-queue"), updated: document.querySelector("#dashboard-updated"), cameraGrid: document.querySelector("#camera-grid"), cameraPhase: document.querySelector("#camera-phase"), toast: document.querySelector("#toast"),
};
let cameraRenderSignature = "";
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
function duration(seconds) { if (!seconds) return "--"; return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`; }
function toolChips(tools, empty) { return tools.length ? tools.map(tool => `<span class="tool-chip">${escapeHtml(tool)}</span>`).join("") : `<span class="muted">${empty}</span>`; }
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
function render(data) {
  ui.queueTime.textContent = duration(data.queue_cycle_seconds); ui.queueCount.textContent = `${data.queue.length} pallet${data.queue.length === 1 ? "" : "s"}`;
  ui.currentCycle.textContent = duration(data.current_cycle_seconds); ui.currentPallet.textContent = data.machine_pallet ? data.machine_pallet.name : "No pallet in Mill";
  ui.queueTools.innerHTML = toolChips(data.queue_tools, "No queued program tools"); ui.atcTools.innerHTML = toolChips(data.atc_tools, data.atc_source || "Mill telemetry not connected");
  ui.queue.innerHTML = data.queue.length ? data.queue.map((pallet, index) => `<article class="dashboard-queue-item"><span class="queue-number">${index + 1}</span><div><strong>${escapeHtml(pallet.name)}</strong><small>${escapeHtml(pallet.program_path || "No program assigned")}</small></div><div>${toolChips(pallet.program_tools, "No active tools")}</div><strong>${duration(pallet.expected_cycle_seconds)}</strong></article>`).join("") : `<p class="debug-table-empty">No pallets are queued.</p>`;
  renderCameras(data.cameras);
  ui.updated.textContent = `Updated ${new Date().toLocaleTimeString()}`; ui.state.classList.add("online"); ui.state.lastChild.textContent = " Online";
}
async function load() { try { const response = await fetch("/api/dashboard", {cache: "no-store"}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Dashboard unavailable"); render(data); const settings = await (await fetch("/api/settings", {cache: "no-store"})).json(); document.querySelectorAll("[data-robot-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.robot_programs_page_enabled)); document.querySelectorAll("[data-mill-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.mill_programs_page_enabled)); } catch (error) { ui.state.lastChild.textContent = " Unavailable"; } }
async function poll() { if (!document.hidden) await load(); window.setTimeout(poll, 5000); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
poll();
