const ui = {
  state: document.querySelector("#system-state"), queueTime: document.querySelector("#queue-time"), queueCount: document.querySelector("#queue-count"), currentCycle: document.querySelector("#current-cycle"), currentPallet: document.querySelector("#current-pallet"), queueTools: document.querySelector("#queue-tools"), atcTools: document.querySelector("#atc-tools"), queue: document.querySelector("#dashboard-queue"), updated: document.querySelector("#dashboard-updated"), toast: document.querySelector("#toast"),
};
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
function duration(seconds) { if (!seconds) return "--"; return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`; }
function toolChips(tools, empty) { return tools.length ? tools.map(tool => `<span class="tool-chip">${escapeHtml(tool)}</span>`).join("") : `<span class="muted">${empty}</span>`; }
function render(data) {
  ui.queueTime.textContent = duration(data.queue_cycle_seconds); ui.queueCount.textContent = `${data.queue.length} pallet${data.queue.length === 1 ? "" : "s"}`;
  ui.currentCycle.textContent = duration(data.current_cycle_seconds); ui.currentPallet.textContent = data.machine_pallet ? data.machine_pallet.name : "No pallet in Mill";
  ui.queueTools.innerHTML = toolChips(data.queue_tools, "No queued program tools"); ui.atcTools.innerHTML = toolChips(data.atc_tools, data.atc_source || "Mill telemetry not connected");
  ui.queue.innerHTML = data.queue.length ? data.queue.map((pallet, index) => `<article class="dashboard-queue-item"><span class="queue-number">${index + 1}</span><div><strong>${escapeHtml(pallet.name)}</strong><small>${escapeHtml(pallet.program_path || "No program assigned")}</small></div><div>${toolChips(pallet.program_tools, "No active tools")}</div><strong>${duration(pallet.expected_cycle_seconds)}</strong></article>`).join("") : `<p class="debug-table-empty">No pallets are queued.</p>`;
  ui.updated.textContent = `Updated ${new Date().toLocaleTimeString()}`; ui.state.classList.add("online"); ui.state.lastChild.textContent = " Online";
}
async function load() { try { const response = await fetch("/api/dashboard", {cache: "no-store"}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Dashboard unavailable"); render(data); const settings = await (await fetch("/api/settings", {cache: "no-store"})).json(); document.querySelectorAll("[data-robot-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.robot_programs_page_enabled)); document.querySelectorAll("[data-mill-programs-nav]").forEach(link => link.classList.toggle("hidden", !settings.settings.mill_programs_page_enabled)); } catch (error) { ui.state.lastChild.textContent = " Unavailable"; } }
async function poll() { if (!document.hidden) await load(); window.setTimeout(poll, 5000); }
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
poll();
