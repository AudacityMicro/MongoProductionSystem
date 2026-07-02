const state = document.querySelector("#system-state");
const version = document.querySelector("#app-version");

async function checkSystem() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error(`Health check returned ${response.status}`);

    const health = await response.json();
    state.classList.add("online");
    state.lastChild.textContent = " System online";
    version.textContent = `Version ${health.version}`;
  } catch {
    state.classList.remove("online");
    state.lastChild.textContent = " System unavailable";
  }
}

checkSystem();

