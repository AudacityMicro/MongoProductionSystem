(() => {
  let dialog;
  let resolveChoice;
  let promptDialog;
  let resolvePrompt;

  function close(choice) {
    const resolve = resolveChoice;
    resolveChoice = null;
    if (dialog.open) dialog.close();
    resolve?.(choice);
  }

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "mps-confirm-dialog dialog-tone-info";
    dialog.innerHTML = `
      <form method="dialog">
        <div class="dialog-heading">
          <div>
            <p class="eyebrow" data-confirm-eyebrow></p>
            <h2 data-confirm-title></h2>
          </div>
        </div>
            <p class="dialog-message" data-confirm-message></p>
            <div class="dialog-actions">
          <button class="button ghost" type="button" data-confirm-cancel>Cancel</button>
          <button class="button secondary hidden" type="button" data-confirm-secondary></button>
          <button class="button secondary hidden" type="button" data-confirm-tertiary></button>
          <button class="button primary" type="button" data-confirm-primary>Continue</button>
        </div>
      </form>`;
    dialog.querySelector("[data-confirm-cancel]").addEventListener("click", () => close(null));
    dialog.querySelector("[data-confirm-secondary]").addEventListener("click", () => close("secondary"));
    dialog.querySelector("[data-confirm-tertiary]").addEventListener("click", () => close("tertiary"));
    dialog.querySelector("[data-confirm-primary]").addEventListener("click", () => close("primary"));
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      close(null);
    });
    dialog.addEventListener("close", () => {
      if (resolveChoice) close(null);
    });
    document.body.append(dialog);
    return dialog;
  }

  function ensurePromptDialog() {
    if (promptDialog) return promptDialog;
    promptDialog = document.createElement("dialog");
    promptDialog.className = "mps-confirm-dialog dialog-tone-info";
    promptDialog.innerHTML = `
      <form method="dialog">
        <div class="dialog-heading"><div><p class="eyebrow" data-prompt-eyebrow></p><h2 data-prompt-title></h2></div></div>
        <p class="dialog-message" data-prompt-message></p>
        <label data-prompt-label><input data-prompt-input autocomplete="off"></label>
        <div class="dialog-actions"><button class="button ghost" type="button" data-prompt-cancel>Cancel</button><button class="button primary" type="button" data-prompt-submit>Save</button></div>
      </form>`;
    const finish = value => {
      const resolve = resolvePrompt;
      resolvePrompt = null;
      if (promptDialog.open) promptDialog.close();
      resolve?.(value);
    };
    promptDialog.querySelector("[data-prompt-cancel]").addEventListener("click", () => finish(null));
    promptDialog.querySelector("[data-prompt-submit]").addEventListener("click", () => finish(promptDialog.querySelector("[data-prompt-input]").value.trim()));
    promptDialog.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      finish(promptDialog.querySelector("[data-prompt-input]").value.trim());
    });
    promptDialog.addEventListener("cancel", event => { event.preventDefault(); finish(null); });
    promptDialog.addEventListener("close", () => { if (resolvePrompt) finish(null); });
    document.body.append(promptDialog);
    return promptDialog;
  }

  window.mpsConfirm = ({
    eyebrow = "Confirm action",
    title = "Confirm",
    message,
    tone = "info",
    primaryLabel = "Continue",
    secondaryLabel = "",
    tertiaryLabel = "",
    cancelLabel = "Cancel",
  }) => new Promise(resolve => {
    const popup = ensureDialog();
    if (resolveChoice) close(null);
    resolveChoice = resolve;
    popup.className = `mps-confirm-dialog dialog-tone-${tone}`;
    popup.querySelector("[data-confirm-eyebrow]").textContent = eyebrow;
    popup.querySelector("[data-confirm-title]").textContent = title;
    popup.querySelector("[data-confirm-message]").textContent = message;
    popup.querySelector("[data-confirm-cancel]").textContent = cancelLabel;
    const secondary = popup.querySelector("[data-confirm-secondary]");
    secondary.textContent = secondaryLabel;
    secondary.classList.toggle("hidden", !secondaryLabel);
    const tertiary = popup.querySelector("[data-confirm-tertiary]");
    tertiary.textContent = tertiaryLabel;
    tertiary.classList.toggle("hidden", !tertiaryLabel);
    const primary = popup.querySelector("[data-confirm-primary]");
    primary.textContent = primaryLabel;
    primary.className = `button ${tone === "danger" ? "danger" : tone === "warning" ? "warning" : "primary"}`;
    popup.showModal();
    primary.focus();
  });

  window.mpsPrompt = ({
    eyebrow = "Input required",
    title = "Enter a value",
    message = "",
    label = "Value",
    value = "",
    placeholder = "",
    submitLabel = "Save",
  }) => new Promise(resolve => {
    const popup = ensurePromptDialog();
    if (resolvePrompt) {
      const previous = resolvePrompt;
      resolvePrompt = null;
      previous(null);
    }
    resolvePrompt = resolve;
    popup.querySelector("[data-prompt-eyebrow]").textContent = eyebrow;
    popup.querySelector("[data-prompt-title]").textContent = title;
    popup.querySelector("[data-prompt-message]").textContent = message;
    popup.querySelector("[data-prompt-label]").firstChild.textContent = "";
    popup.querySelector("[data-prompt-label]").prepend(document.createTextNode(label));
    const input = popup.querySelector("[data-prompt-input]");
    input.value = value;
    input.placeholder = placeholder;
    popup.querySelector("[data-prompt-submit]").textContent = submitLabel;
    popup.showModal();
    input.focus();
    input.select();
  });
})();
