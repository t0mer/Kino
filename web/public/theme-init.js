// Applies the saved theme before first paint (kept in sync with src/lib/theme.ts).
(function () {
  var pref = "system";
  try {
    pref = localStorage.getItem("kino.theme") || "system";
  } catch (e) {}
  var dark =
    pref === "dark" ||
    (pref !== "light" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
})();
