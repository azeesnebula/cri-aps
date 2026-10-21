/* ------------------------------------------------------------------
   Sieger APS - Gantt time grid and pinned resource labels.

   Additive, like sieger.css: frePPLe's own ganttDrv.js is never edited.
   We hook the one function the chart calls last on every redraw.

   WHY A HOOK AND NOT A MutationObserver
   -------------------------------------
   ganttDrv.drawGantt() rebuilds the whole <table> from a string, then
   calls gantt.header("#ganttheader") as its final statement. Wrapping
   that global gives us an exact "the chart has just been drawn" signal
   for all three triggers - first paint, zoom, and horizontal scroll -
   with no polling and no observer feedback loop.

   WHY THE LINES GO INSIDE <g class="ganttrow">
   --------------------------------------------
   The bars live in a 0..10000 user-space that <g> scales down to pixels
   (ganttDrv.js: time2scale + transform="scale(scale,1)"). Drawing the
   lines in that same space with the same time2scale maths is what keeps
   them locked to the bars and to the date header's ticks at every zoom
   level. The horizontal scale would also squash the stroke to ~0.1px,
   so every line carries vector-effect="non-scaling-stroke".
   ------------------------------------------------------------------ */

(function () {
  "use strict";

  /* Sieger runs 3 x 8h shifts. Change this one number if that changes;
     set it to 0 to switch shift lines off. They are only drawn when the
     chart is zoomed in far enough for them to be legible. */
  var SHIFT_HOURS = 8;

  /* Must match $scope.rowheight in ganttDrv.js. */
  var ROWHEIGHT = 25;

  var SVGNS = "http://www.w3.org/2000/svg";
  var DAY = 86400000;

  if (typeof gantt === "undefined" || typeof gantt.header !== "function") return;

  var coreHeader = gantt.header;

  gantt.header = function (el) {
    coreHeader.apply(this, arguments);
    // Only the AngularJS resource Gantt. The jqGrid gantt draws into
    // #jqgh_grid_operationplans and has no .ganttrow to hang lines on.
    if (el !== "#ganttheader") return;
    // A broken decoration must never take the chart down with it.
    try { hideScrollbar(); } catch (e) { }
    try { armPan(); } catch (e) { }
    try { drawTimeGrid(); } catch (e) { }
    try { pinResourceLabels(); } catch (e) { }
  };

  /* --------------------------------------------------- navigation

     Core ships a horizontal scrollbar under the date header and two zoom
     buttons. Both misbehave, for different reasons, and the scrollbar is
     not wanted at all - so this block removes the bar and replaces the
     navigation with the two gestures the rest of Sieger APS already uses:
     buttons that zoom about the CENTRE, and drag to pan.

     WHY THE SCROLLBAR GOES
     ----------------------
     ganttDrv.js:289 binds its scroll handler ONCE at controller start,
     to #ganttheader - but drawGantt() rebuilds the whole table from a
     string on every paint (ganttDrv.js:203-250) and that element goes
     with it, so nothing is listening afterwards. Dragging the bar only
     scrolled the header's own overflow: the date ruler slid while the
     bars stayed put. Rather than keep a bar nobody wants, hide the
     overflow entirely and pan by dragging the chart.

     WHY ZOOM FELT BROKEN
     --------------------
     gantt.zoom() (frepple.js:3915) anchors on viewstart and moves only
     viewend:  viewend = viewstart + zone.  So every zoom-in walks the
     window toward the START of the horizon and whatever you were looking
     at slides off to the right - and once there, zooming in again cannot
     get you back, because nothing moves viewstart. We wrap it to hold the
     MIDPOINT of the current window instead, which is what a zoom button
     is expected to do. */

  var DAY_MS = 86400000;

  function haveView() {
    return typeof viewstart !== "undefined" && typeof viewend !== "undefined"
      && typeof horizonstart !== "undefined" && typeof horizonend !== "undefined";
  }

  // Move the window to [s, e], clamped inside the horizon, keeping its width.
  function setWindow(s, e) {
    var zone = e - s;
    var h0 = horizonstart.getTime();
    var h1 = horizonend.getTime();
    if (zone > h1 - h0) { s = h0; e = h1; }
    else {
      if (s < h0) { s = h0; e = s + zone; }
      if (e > h1) { e = h1; s = e - zone; }
    }
    viewstart.setTime(s);
    viewend.setTime(e);
  }

  function redraw() {
    try {
      var scope = angular.element(document.getElementById("ganttgraph")).scope();
      if (scope && typeof scope.drawGantt === "function") scope.drawGantt();
    } catch (e) { }
  }

  /* ------------------------------------------------------- zoom fix */

  if (typeof gantt.zoom === "function") {
    var coreZoom = gantt.zoom;
    gantt.zoom = function (factor, el) {
      // Only this chart. The jqGrid gantt and the pegging screen keep
      // core's behaviour.
      if (el !== "#ganttheader" || !haveView()) {
        return coreZoom.apply(this, arguments);
      }
      var mid = viewstart.getTime() + (viewend.getTime() - viewstart.getTime()) / 2;
      var zone = (viewend.getTime() - viewstart.getTime()) * factor;
      var horizon = horizonend.getTime() - horizonstart.getTime();
      if (zone > horizon) zone = horizon;
      if (zone < DAY_MS) zone = DAY_MS;          // core's own floor: one day
      setWindow(mid - zone / 2, mid + zone / 2);
      // Hand core a factor of 1: the window is already where we want it,
      // so this becomes a pure "re-transform and redraw the header".
      return coreZoom.call(this, 1, el);
    };
  }

  /* ------------------------- hide the scrollbar, keep its offset

     The two halves of this chart are panned by DIFFERENT mechanisms, and
     that asymmetry is the thing to know before touching this code:

       bars    ganttDrv draws them with time2scale(), which is relative
               to viewstart - so moving viewstart and redrawing is
               enough. They follow the variable.

       header  gantt.header() lays every tick out from HORIZONSTART with
               a running x (frepple.js:3822-3882, the daily branches),
               producing one wide svg that covers the entire horizon.
               viewstart only sets the SCALE, never the origin. The
               visible slice is chosen purely by the element's
               scrollLeft.

     That is why core needed a scrollbar at all. Hiding it does not
     remove the need for the offset - so we set scrollLeft ourselves,
     from viewstart, on every redraw. overflow-x:hidden stops the user
     dragging it but leaves scrollLeft writable, which is exactly the
     split we want: no bar, still aligned. Skip this and the dates read
     August while the bars are in November. */

  function hideScrollbar() {
    var head = document.getElementById("ganttheader");
    if (!head) return;
    // ganttDrv writes overflow-x:scroll as an INLINE style on the <th>,
    // so a stylesheet rule would need !important. Setting it back here,
    // on the element the redraw just created, keeps the intent visible
    // in code rather than hidden in a CSS override.
    head.style.overflowX = "hidden";
    alignHeader(head);
  }

  // Put the header's viewport where viewstart says it should be - the
  // inverse of the formula gantt.scroll() used to derive viewstart from
  // scrollLeft (frepple.js:3899-3903).
  function alignHeader(head) {
    if (!haveView() || !head.scrollWidth) return;
    var span = horizonend.getTime() - horizonstart.getTime();
    if (!(span > 0)) return;
    var frac = (viewstart.getTime() - horizonstart.getTime()) / span;
    head.scrollLeft = Math.round(Math.max(0, Math.min(1, frac)) * head.scrollWidth);
  }

  /* ------------------------------------------------- drag to pan */

  // One listener, delegated on a container that survives the redraw, so
  // rebuilding the table cannot orphan it - the mistake core made with
  // its scroll handler.
  var pan = null;

  function armPan() {
    var stage = document.getElementById("ganttgraph");
    if (!stage || stage.dataset.sgPan) return;
    stage.dataset.sgPan = "1";
    stage.style.cursor = "grab";

    stage.addEventListener("pointerdown", function (ev) {
      if (!haveView() || ev.button !== 0) return;
      // A click on a bar selects it - never start a pan there.
      if (ev.target.closest("rect.opplan")) return;
      var head = document.getElementById("ganttheader");
      if (!head || !head.clientWidth) return;
      pan = {
        x: ev.clientX,
        msPerPx: (viewend.getTime() - viewstart.getTime()) / head.clientWidth,
        start: viewstart.getTime(),
        end: viewend.getTime(),
        moved: false
      };
      stage.style.cursor = "grabbing";
      // Without this a drag across the chart selects the machine names
      // and the date ruler, which then stay highlighted.
      stage.style.userSelect = "none";
      stage.setPointerCapture(ev.pointerId);
    });

    stage.addEventListener("pointermove", function (ev) {
      if (!pan) return;
      var dx = ev.clientX - pan.x;
      if (!pan.moved && Math.abs(dx) < 4) return;   // let a click stay a click
      pan.moved = true;
      ev.preventDefault();
      var shift = -dx * pan.msPerPx;                 // drag left = go forward
      setWindow(pan.start + shift, pan.end + shift);
      redraw();
    });

    function endPan() {
      if (!pan) return;
      pan = null;
      stage.style.cursor = "grab";
      stage.style.userSelect = "";
    }
    stage.addEventListener("pointerup", endPan);
    stage.addEventListener("pointercancel", endPan);
  }

  /* ---------------------------------------------------------- the grid */

  function drawTimeGrid() {
    var rows = document.querySelectorAll("#ganttgraph g.ganttrow");
    if (!rows.length) return;
    if (typeof viewstart === "undefined" || typeof viewend === "undefined") return;

    var t0 = viewstart.getTime();
    var span = viewend.getTime() - t0;
    if (!(span > 0)) return;

    // Pixels per day. Only used to choose the granularity - the same
    // number gantt.header() uses to choose its bucket size, so the two
    // stay in step. Positions below are computed from time, not pixels,
    // so a few px of padding difference cannot misalign anything.
    var header = window.jQuery ? jQuery("#ganttheader") : null;
    var scaling = header && header.length ? DAY / span * header.width() : 40;

    var ticks = buildTicks(scaling, t0, viewend.getTime());
    if (!ticks.length) return;

    for (var r = 0; r < rows.length; r++) {
      var g = rows[r];

      var stale = g.querySelectorAll("line.sg-tg");
      for (var s = 0; s < stale.length; s++) stale[s].remove();

      // <g> is translated down by (layers-1)*ROWHEIGHT and bars sit at
      // y = -lane*ROWHEIGHT, so the band runs from the topmost lane to
      // one row below the baseline.
      var layers = parseInt(g.getAttribute("title"), 10) || 1;
      var yTop = -(layers - 1) * ROWHEIGHT;

      var frag = document.createDocumentFragment();
      for (var i = 0; i < ticks.length; i++) {
        var x = 10000 * (ticks[i].t - t0) / span;
        var line = document.createElementNS(SVGNS, "line");
        line.setAttribute("class", "sg-tg sg-tg-" + ticks[i].kind);
        line.setAttribute("x1", x);
        line.setAttribute("x2", x);
        line.setAttribute("y1", yTop);
        line.setAttribute("y2", ROWHEIGHT);
        line.setAttribute("vector-effect", "non-scaling-stroke");
        frag.appendChild(line);
      }
      // First in document order = painted first = behind the bars.
      g.insertBefore(frag, g.firstChild);
    }
  }

  /* Boundaries inside the visible window only. Everything outside is
     clipped by the svg viewport anyway, and the horizon can be years -
     walking it day by day would cost thousands of nodes per row. */
  function buildTicks(scaling, from, to) {
    var ticks = [];
    var d;

    if (scaling < 5) {
      // Header is on quarters and months - anything finer is a smear.
      d = new Date(new Date(from).getFullYear(), new Date(from).getMonth(), 1);
      while (d.getTime() <= to) {
        if (d.getTime() >= from) ticks.push({ t: d.getTime(), kind: "month" });
        d = new Date(d.getFullYear(), d.getMonth() + 1, 1);
      }
      return ticks;
    }

    d = midnight(from);
    if (scaling < 20) {
      // Header is on weeks - draw weeks, emphasise the month turn.
      while (d.getTime() <= to) {
        if (d.getTime() >= from) {
          if (d.getDate() === 1) ticks.push({ t: d.getTime(), kind: "month" });
          else if (d.getDay() === 0) ticks.push({ t: d.getTime(), kind: "week" });
        }
        d = nextDay(d);
      }
      return ticks;
    }

    // Header is on days - draw days, emphasise the week turn.
    while (d.getTime() <= to) {
      if (d.getTime() >= from)
        ticks.push({ t: d.getTime(), kind: d.getDay() === 0 ? "week" : "day" });
      if (scaling >= 350 && SHIFT_HOURS > 0) {
        for (var h = SHIFT_HOURS; h < 24; h += SHIFT_HOURS) {
          var shift = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h).getTime();
          if (shift >= from && shift <= to) ticks.push({ t: shift, kind: "shift" });
        }
      }
      d = nextDay(d);
    }
    return ticks;
  }

  // Local-time arithmetic throughout, matching gantt.header(), so a DST
  // change shifts our lines and its ticks by the same hour.
  function midnight(ms) {
    var d = new Date(ms);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }
  function nextDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
  }

  /* ------------------------------------------------- the row labels */

  /* ganttDrv writes the machine name as a bare text node:
       "<tr><td>" + opplan.resource + "</td>..."
     A bare <td> cannot be made sticky - it already fills the row, so it
     has no room to slide. Wrapping the text gives the sticky rule an
     element that CAN move inside the cell, which is what keeps the name
     on screen while a tall row scrolls past. */
  function pinResourceLabels() {
    var cells = document.querySelectorAll("#ganttgraph table tr > td:first-child");
    for (var i = 0; i < cells.length; i++) {
      var td = cells[i];
      if (td.querySelector(".sg-res")) continue;
      var name = (td.textContent || "").trim();
      if (!name) continue;
      var span = document.createElement("span");
      span.className = "sg-res";
      span.textContent = name;
      span.setAttribute("title", name);
      td.textContent = "";
      td.appendChild(span);
    }
  }
})();
