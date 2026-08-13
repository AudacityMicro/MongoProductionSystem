(() => {
  const container = document.querySelector("#system-status-details");
  if (!container) return;
  const render = data => {
    container.innerHTML = [data.backend, data.robot, data.mill].map(item =>
      `<span class="system-status-item ${item.state}" title="${item.label}"><i aria-hidden="true"></i>${item.label}</span>`
    ).join("");
  };
  const setAmbientMode = (connections, board) => {
    const run = board?.run_mode || {};
    const hasAlarm = run.state === "faulted" || Boolean(run.alert)
      || Object.values(connections).some(item => item.state === "offline");
    const mode = hasAlarm ? "alarm" : run.enabled ? "running" : "idle";
    document.body.classList.remove("ambient-running", "ambient-idle", "ambient-alarm");
    document.body.classList.add(`ambient-${mode}`);
    const intensity = Number(board?.settings?.background_stack_light_intensity);
    document.body.style.setProperty("--ambient-strength", `${Number.isFinite(intensity) ? intensity : 65}%`);
  };
  const load = async () => {
    try {
      const [response, boardResponse] = await Promise.all([
        fetch("/api/system/status", {cache: "no-store"}),
        fetch("/api/board", {cache: "no-store"}),
      ]);
      if (!response.ok) throw new Error("status unavailable");
      const connections = await response.json();
      render(connections);
      setAmbientMode(connections, boardResponse.ok ? await boardResponse.json() : null);
    } catch {
      render({backend: {state: "offline", label: "Backend: Unavailable"}, robot: {state: "neutral", label: "Robot: Unknown"}, mill: {state: "neutral", label: "Mill: Unknown"}});
      setAmbientMode({backend: {state: "offline"}}, null);
    }
  };
  load();
  window.setInterval(() => { if (!document.hidden) load(); }, 5000);
})();
