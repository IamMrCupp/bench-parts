/* The Appearance choice: System, Light, Dark, or a theme from the themes
   folder. burnwright's model, kept in this browser's localStorage.

   Loaded in <head> without defer, so a page never flashes the wrong look.
   The themes the server found are on <html data-themes>; their colours were
   checked server-side (bench_parts/themes.py) and are only ever set as --bw-*
   custom properties. */
(function () {
  "use strict";
  var KEY = "bench.appearance";
  var root = document.documentElement;
  var system = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  var themes = [];
  try {
    themes = JSON.parse(root.getAttribute("data-themes") || "[]") || [];
  } catch (e) {
    themes = [];
  }

  function load() {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (e) {
      return "system";
    }
  }

  function save(choice) {
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch (e) {
      /* A browser that won't store it keeps the choice for this page only. */
    }
  }

  function themeFor(choice) {
    if (choice.indexOf("theme:") !== 0) return null;
    var id = choice.slice(6);
    var found = null;
    themes.forEach(function (theme) {
      if (theme.id === id) found = theme;
    });
    return found;
  }

  var painted = [];

  function apply(choice) {
    painted.forEach(function (name) {
      root.style.removeProperty(name);
    });
    painted = [];
    var theme = themeFor(choice);
    var look;
    if (theme) {
      look = theme.base;
      for (var name in theme.colors) {
        if (Object.prototype.hasOwnProperty.call(theme.colors, name)) {
          root.style.setProperty("--bw-" + name, theme.colors[name]);
          painted.push("--bw-" + name);
        }
      }
    } else if (choice === "light" || choice === "dark") {
      look = choice;
    } else {
      look = system && system.matches ? "dark" : "light";
    }
    root.setAttribute("data-theme", look);
  }

  var current = load();
  /* A theme whose file has gone falls back to following the system. */
  if (current.indexOf("theme:") === 0 && !themeFor(current)) current = "system";
  apply(current);

  if (system) {
    var follow = function () {
      if (current === "system") apply(current);
    };
    if (system.addEventListener) system.addEventListener("change", follow);
    else if (system.addListener) system.addListener(follow);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("appearance");
    if (!select) return;
    if (themes[0]) {
      var group = document.createElement("optgroup");
      group.label = "Themes";
      themes.forEach(function (theme) {
        var option = document.createElement("option");
        option.value = "theme:" + theme.id;
        option.textContent = theme.name;
        group.appendChild(option);
      });
      select.appendChild(group);
    }
    select.value = current;
    select.hidden = false;
    select.addEventListener("change", function () {
      current = select.value;
      save(current);
      apply(current);
    });
  });
})();
