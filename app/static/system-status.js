(() => {
  const container = document.querySelector("#system-status-details");
  if (!container) return;
  let latestBoard = null;
  const optionalStopStorageKey = "mps-optional-stop-active";
  const banner = document.createElement("section");
  banner.className = "optional-stop-banner hidden";
  banner.setAttribute("role", "alert");
  banner.setAttribute("aria-live", "assertive");
  banner.innerHTML = `<strong>Optional Stop is active</strong><span>PathPilot will pause when a program reaches M01.</span><button class="button warning" type="button">Turn off Optional Stop</button>`;
  document.querySelector(".topbar")?.insertAdjacentElement("afterend", banner);

  const showOptionalStopPopup = () => {
    const options = {
      eyebrow: "PathPilot warning",
      title: "Optional Stop was turned on",
      message: "The mill will pause when a running program reaches M01.",
      tone: "warning",
      primaryLabel: "Understood",
    };
    if (typeof window.mpsConfirm === "function") window.mpsConfirm(options);
    else window.alert(`${options.title}\n\n${options.message}`);
  };

  const renderOptionalStop = control => {
    const active = control?.optional_stop === true;
    banner.classList.toggle("hidden", !active);
    const previous = sessionStorage.getItem(optionalStopStorageKey);
    if (active && previous === "false") showOptionalStopPopup();
    if (control?.optional_stop !== null && control?.optional_stop !== undefined) {
      sessionStorage.setItem(optionalStopStorageKey, active ? "true" : "false");
    }
    const button = banner.querySelector("button");
    button.disabled = !control?.can_control;
  };

  banner.querySelector("button").addEventListener("click", async event => {
    const button = event.currentTarget;
    if (!latestBoard) return;
    button.disabled = true;
    try {
      const response = await fetch("/api/cnc/control/optional_stop_off", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({expected_revision: latestBoard.revision, confirmed: false}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Optional Stop could not be turned off.");
      latestBoard = result.board;
      renderOptionalStop(latestBoard.mill_control);
    } catch (error) {
      window.alert(error.message);
      button.disabled = false;
    }
  });
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
      latestBoard = boardResponse.ok ? await boardResponse.json() : null;
      render(connections);
      setAmbientMode(connections, latestBoard);
      renderOptionalStop(connections.mill_control || latestBoard?.mill_control);
    } catch {
      render({backend: {state: "offline", label: "Backend: Unavailable"}, robot: {state: "neutral", label: "Robot: Unknown"}, mill: {state: "neutral", label: "Mill: Unknown"}});
      setAmbientMode({backend: {state: "offline"}}, null);
    }
  };
  load();
  window.setInterval(() => { if (!document.hidden) load(); }, 5000);
})();
