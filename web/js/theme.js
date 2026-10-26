export const THEME_KEY = "pdf-magic-theme";

export function resolveTheme(preference, systemDark) {
  if (preference === "dark" || preference === "light") return preference;
  return systemDark ? "dark" : "light";
}

export function oppositeTheme(theme) {
  return theme === "dark" ? "light" : "dark";
}

export function initThemeToggle() {
  const toggle = document.querySelector("#theme-toggle");
  if (!toggle) return;

  const icon = toggle.querySelector(".theme-icon");
  const label = toggle.querySelector(".theme-label");
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = null;
  try { preference = localStorage.getItem(THEME_KEY); } catch {}

  function render(theme) {
    document.documentElement.dataset.theme = theme;
    const isDark = theme === "dark";
    toggle.setAttribute("aria-pressed", String(isDark));
    toggle.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
    icon.textContent = isDark ? "☀" : "☾";
    label.textContent = isDark ? "Light mode" : "Dark mode";
  }

  render(resolveTheme(preference, media.matches));

  toggle.addEventListener("click", () => {
    preference = oppositeTheme(document.documentElement.dataset.theme);
    try { localStorage.setItem(THEME_KEY, preference); } catch {}
    render(preference);
  });

  media.addEventListener?.("change", (event) => {
    if (preference !== "dark" && preference !== "light") {
      render(resolveTheme(null, event.matches));
    }
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== THEME_KEY && event.key !== null) return;
    preference = event.key === null ? null : event.newValue;
    render(resolveTheme(preference, media.matches));
  });
}
