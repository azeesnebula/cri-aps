/* ------------------------------------------------------------------
   Sieger APS - the light/dark switch.

   The stamp itself is written pre-paint by an inline script in
   base_site.html; this file only builds the control and stores the
   choice. Keeping the two apart is deliberate - a toggle that waits for
   this file to load would flash the wrong theme first.

   Three states, not two: no stored value means "follow the OS", which is
   what most people want and what prefers-color-scheme already handles.
   The button therefore cycles auto -> light -> dark -> auto.
   ------------------------------------------------------------------ */
(function () {
  "use strict";

   var KEY = "cri-pumps-theme";
  var ORDER = ["auto", "light", "dark"];
  /* Font Awesome 4.7 glyphs - the same icon set the rest of the navbar
     uses, so the control reads as part of frePPLe rather than bolted on. */
  var ICON = { auto: "fa-adjust", light: "fa-sun-o", dark: "fa-moon-o" };
  var LABEL = { auto: "Auto", light: "Light", dark: "Dark" };
  var TITLE = {
    auto: "Appearance: following your system — click for light",
    light: "Appearance: light — click for dark",
    dark: "Appearance: dark — click to follow your system"
  };

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "auto";
    } catch (e) {
      return "auto";
    }
  }

  function apply(mode) {
    var root = document.documentElement;
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    try {
      if (mode === "auto") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch (e) { /* private mode - the choice just will not persist */ }
  }

  function build() {
    // #nav-menu-end is the right-hand group that holds the notification bell
    // and the account menu. The other .ms-auto list in this navbar is the
    // database selector, which is collapsed at most widths - dropping the
    // button in there makes it invisible.
    var bar = document.querySelector("#nav-menu-end .navbar-nav.ms-auto")
           || document.querySelector("#nav-menu-end .navbar-nav")
           || document.querySelector("#nav-menu-header .navbar-nav.ms-auto");
    if (!bar) return;

    var mode = stored();
    var li = document.createElement("li");
    li.className = "navbar-item d-flex align-items-center ms-2";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sg-theme";

    var icon = document.createElement("span");
    icon.className = "fa " + ICON[mode];
    var text = document.createElement("span");
    text.className = "sg-theme-lbl";
    text.textContent = LABEL[mode];
    btn.appendChild(icon);
    btn.appendChild(text);

    function paint() {
      icon.className = "fa " + ICON[mode];
      text.textContent = LABEL[mode];
      btn.title = TITLE[mode];
      btn.setAttribute("aria-label", TITLE[mode]);
    }
    paint();

    btn.addEventListener("click", function () {
      mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
      apply(mode);
      paint();
    });

    li.appendChild(btn);
    bar.appendChild(li);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", build);
  else
    build();
})();
