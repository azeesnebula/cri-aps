/* ------------------------------------------------------------------
   Sieger APS - the navigation menus.

   frePPLe renders each menu as one flat dropdown. Planning carries
   EIGHTEEN items in a single column, which is a scroll, not a menu - you
   cannot scan it, and nothing tells you which of the four "board" entries
   you actually want.

   The menu data already knows the answer: freppledb menu separators put
   dividers between the logical blocks. This file reads those dividers,
   lifts each block into its own column with a heading, and attaches a
   one-line description to the entries whose names do not speak for
   themselves. No server change - it is the same links, arranged.

   Runs once, on the markup Django already sent.
   ------------------------------------------------------------------ */
(function () {
  "use strict";

  /* Column headings per menu, in divider order. A menu with no entry here
     (or with more blocks than headings) simply keeps its blocks unlabelled
     rather than inventing a name. */
  var GROUPS = {
    "nav-planning": ["Plan", "Inspect the plan", "Analyse"],
    "nav-admin": ["Run and monitor", "Configuration", "People"]
  };

  /* One line of help, by PATH. Every planning row needs one: a row with a
     hint renders as title + sub-line, a row without keeps frePPLe's bare
     anchor, and the two sit at visibly different heights and type sizes
     in the same column.

     Keyed on the path rather than the raw href because a menu entry may
     carry a query string (the frePPLe gantt arrives sorted by resource),
     and matching the full string silently dropped its description. */
  var HINT = {
    "/": "Shipment delivery summary",
    "/machine-schedule/": "Every machine, every job, on a timeline",
    "/projects/": "One timeline per customer order",
    "/release/": "Checkpoints, then freeze the next 3 days",
    "/data/input/demand/": "The order book from LN",
    "/production-orders/": "Filter orders by shipment and delivery status",
    "/data/input/manufacturingorder/": "Every job as a table, to search and export",
    "/build-graph/": "What feeds what, for one part",
    "/problem/": "What the planner could not satisfy",
    "/constraint/": "The reason behind each late order",
    "/constraints/": "The planner declares the drum",
    "/improve/": "What the plan says to do about itself",
    "/sequences/": "When each delivery sequence completes",
    "/start-dates/": "The latest each shipment can start, and why",
    "/flow/": "Working time against waiting time",
    "/bottlenecks/": "Which machine group chokes which week",
    "/adherence/": "Is the plan on time, and is it drifting",
    "/compare/": "Production against the what-if runs"
  };

  function rebuild(toggle, menu) {
    var kids = [].slice.call(menu.children);
    if (kids.length < 8) return;        // short menus are fine as they are

    // Split on the dividers frePPLe already placed.
    var blocks = [[]];
    kids.forEach(function (li) {
      if (li.classList.contains("dropdown-divider")) blocks.push([]);
      else blocks[blocks.length - 1].push(li);
    });
    blocks = blocks.filter(function (b) { return b.length; });
    if (blocks.length < 2) return;

    var names = GROUPS[toggle.id] || [];
    var panel = document.createElement("div");
    panel.className = "sg-mm";

    blocks.forEach(function (block, i) {
      var col = document.createElement("div");
      col.className = "sg-mm-col";

      if (names[i]) {
        var h = document.createElement("div");
        h.className = "sg-mm-h";
        h.textContent = names[i];
        col.appendChild(h);
      }

      block.forEach(function (li) {
        var a = li.querySelector("a");
        if (!a) return;
        a.classList.add("sg-mm-a");
        a.classList.remove("text-nowrap");
        var href = a.getAttribute("href") || "";
        var hint = HINT[href.split("?")[0].split("#")[0]];
        if (hint && !a.querySelector(".sg-mm-d")) {
          var label = document.createElement("span");
          label.className = "sg-mm-t";
          label.textContent = a.textContent.trim();
          var desc = document.createElement("span");
          desc.className = "sg-mm-d";
          desc.textContent = hint;
          a.textContent = "";
          a.appendChild(label);
          a.appendChild(desc);
        }
        col.appendChild(li);
      });
      panel.appendChild(col);
    });

    menu.innerHTML = "";
    menu.appendChild(panel);
    menu.classList.add("sg-mm-menu");
  }

  function build() {
    // .nav-item is the menu proper; the database selector beside it is
    // .navbar-item and must be left alone. One letter apart, and picking
    // the wrong one silently does nothing.
    var items = document.querySelectorAll("#nav-menu li.nav-item.dropdown");
    [].slice.call(items).forEach(function (li) {
      var toggle = li.querySelector(".nav-link[data-bs-toggle=dropdown]");
      var menu = li.querySelector(".dropdown-menu");
      if (toggle && menu) rebuild(toggle, menu);
    });
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", build);
  else
    build();
})();
