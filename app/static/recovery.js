const recoveryUi = {
  launch: document.querySelector("#system-recovery-launch"),
  dialog: document.querySelector("#system-recovery-dialog"),
  message: document.querySelector("#system-recovery-message"),
  prompt: document.querySelector("#system-recovery-prompt"),
  actions: document.querySelector("#system-recovery-actions"),
  cancel: document.querySelector("#system-recovery-cancel"),
};

let recoveryState = null;
let recoveryPollTimer = null;

function recoveryPrompt(title, detail, buttons = []) {
  return `<section class="recovery-prompt"><p class="eyebrow">Next step</p><h3>${escapeHtml(title)}</h3><p>${escapeHtml(detail)}</p>${buttons.length ? `<div class="dialog-actions">${buttons.map(button => `<button class="button ${button.tone || "primary"}" type="button" data-recovery-action="${escapeHtml(button.action)}" data-recovery-key="${escapeHtml(button.key || "")}" data-recovery-value="${escapeHtml(button.value || "")}">${escapeHtml(button.label)}</button>`).join("")}</div>` : ""}</section>`;
}

function latestAction(actions) {
  const action = actions?.at(-1);
  return action ? `<p class="field-help"><strong>${escapeHtml(action.controller || "Recovery")}:</strong> ${escapeHtml(action.detail || action.action || "Completed.")}</p>` : "";
}

function renderSystemRecovery() {
  const session = recoveryState?.session;
  recoveryUi.actions.innerHTML = "";
  recoveryUi.cancel.textContent = session?.status === "completed" ? "Continue" : "Cancel";
  if (!session) {
    recoveryUi.message.textContent = "Recovery is not running.";
    recoveryUi.prompt.innerHTML = recoveryPrompt("Start recovery when the cell is safe", "Recovery checks connections and clears software faults. It never commands production motion.");
    return;
  }
  const guidance = recoveryState.guidance || (recoveryState.faults || []).map(item => item.guidance).filter(Boolean);
  const current = guidance[0];
  const busy = ["running", "awaiting_restart"].includes(session.status);
  recoveryUi.message.textContent = busy ? "Recovery is working. Please wait." : "Follow the single step below.";

  if (session.status === "awaiting_safety") {
    recoveryUi.prompt.innerHTML = recoveryPrompt("Is the cell clear?", "Confirm no person is exposed and the robot and mill are stopped.", [
      {action: "answer", key: "cell_clear", value: "true", label: "Cell is clear — continue"},
    ]);
  } else if (session.status === "handoff" && current?.type === "choice") {
    recoveryUi.prompt.innerHTML = recoveryPrompt(current.title, current.detail || "Choose the observed condition.", (current.options || []).map(option => ({
      action: "choice", key: current.key, value: option.value, label: option.label, tone: "secondary",
    })));
  } else if (session.status === "handoff") {
    recoveryUi.prompt.innerHTML = recoveryPrompt(current?.title || "Resolve the reported condition", current?.detail || session.message, [
      {action: "answer", key: "retry", value: "true", label: "Recheck recovery"},
    ]);
  } else if (session.status === "ready") {
    recoveryUi.prompt.innerHTML = recoveryPrompt("Recovery ready", "Connections and software checks are complete. Production will not start automatically.", [
      {action: "answer", key: "final_approval", value: "true", label: "Finish recovery"},
    ]);
  } else if (session.status === "completed") {
    recoveryUi.message.textContent = "Recovery completed successfully.";
    recoveryUi.prompt.innerHTML = recoveryPrompt("System ready", "Recovery is complete. Start production separately when you are ready.", [
      {action: "close", label: "Continue"},
    ]);
  } else if (busy) {
    recoveryUi.prompt.innerHTML = recoveryPrompt("Checking controllers", session.message || "Checking connections and clearing recoverable software faults.");
  } else {
    recoveryUi.prompt.innerHTML = recoveryPrompt("Recovery paused", session.message || "Review the system and start recovery again when ready.");
  }
  recoveryUi.actions.innerHTML = latestAction(session.actions);
}

async function loadRecoveryStatus(showErrors = false) {
  try { recoveryState = await api("/api/recovery/status", {cache: "no-store"}); renderSystemRecovery(); }
  catch (error) { if (showErrors) showToast(`Recovery status unavailable: ${error.message}`, "error"); }
}

function scheduleRecoveryPoll() {
  window.clearTimeout(recoveryPollTimer);
  if (!recoveryState?.active) return;
  recoveryPollTimer = window.setTimeout(async () => { await loadRecoveryStatus(); scheduleRecoveryPoll(); }, 1000);
}

async function answerRecovery(answers = {}, choices = {}) {
  const session = recoveryState?.session;
  if (!session) return;
  recoveryUi.prompt.querySelectorAll("button").forEach(button => { button.disabled = true; });
  try {
    recoveryState = await api("/api/recovery/answer", {method: "POST", body: JSON.stringify({session_id: session.id, answers, choices})});
    renderSystemRecovery();
    scheduleRecoveryPoll();
  } catch (error) { showToast(`Recovery could not continue: ${error.message}`, "error"); renderSystemRecovery(); }
}

recoveryUi.launch.addEventListener("click", async () => {
  recoveryUi.launch.disabled = true;
  try {
    recoveryState = await api("/api/recovery/start", {method: "POST", body: "{}"});
    renderSystemRecovery();
    if (!recoveryUi.dialog.open) recoveryUi.dialog.showModal();
    scheduleRecoveryPoll();
  } catch (error) { showToast(`Could not start recovery: ${error.message}`, "error"); }
  finally { recoveryUi.launch.disabled = false; }
});

recoveryUi.prompt.addEventListener("click", event => {
  const button = event.target.closest("[data-recovery-action]");
  if (!button) return;
  const key = button.dataset.recoveryKey;
  if (button.dataset.recoveryAction === "choice") void answerRecovery({}, {[key]: button.dataset.recoveryValue});
  else if (button.dataset.recoveryAction === "answer") void answerRecovery({[key]: button.dataset.recoveryValue === "true"});
  else if (button.dataset.recoveryAction === "close") recoveryUi.dialog.close();
});

recoveryUi.cancel.addEventListener("click", async () => {
  const session = recoveryState?.session;
  if (session && recoveryState.active) {
    try { recoveryState = await api("/api/recovery/cancel", {method: "POST", body: JSON.stringify({session_id: session.id})}); }
    catch (error) { showToast(`Could not cancel recovery: ${error.message}`, "error"); return; }
  }
  window.clearTimeout(recoveryPollTimer);
  recoveryUi.dialog.close();
  await loadRecoveryStatus();
});
