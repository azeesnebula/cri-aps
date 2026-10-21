/*
 * Machine schedule board - client behaviour.
 *
 * The board itself is server-rendered; this file only adds what genuinely
 * needs a browser: the click-through modal (fetched per operation), the
 * collapsible sections, and the colour-by-order mode. Vanilla JS, no
 * framework, same house style as build-graph.js.
 *
 * URLs come from data attributes on the #sg-sc root, which the template
 * fills with {{ request.prefix }} - never hardcode a path here, it breaks
 * on scenario databases (/aps1/...).
 */
(function () {
  "use strict";

  var root = document.getElementById("sg-sc");
  if (!root) return;
  var opUrl = root.dataset.opUrl || "/machine-schedule/op/";
  var chart = root.querySelector(".sg-sc-chart");

  /* ------------------------------------------------------------ modal */
  var modal = document.getElementById("sg-md");
  var body = document.getElementById("sg-md-body");
  var cache = {};
  var opener = null;

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function open() { modal.hidden = false; document.body.classList.add("sg-md-on"); }

  function close() {
    modal.hidden = true;
    document.body.classList.remove("sg-md-on");
    if (opener) { opener.focus(); opener = null; }
  }

  function rows(list, cols) {
    return list.map(function (r) {
      return "<tr" + (r.is_this ? ' class="is-this"' : "") + ">" +
        cols.map(function (c) { return "<td>" + c(r) + "</td>"; }).join("") + "</tr>";
    }).join("");
  }

  function section(title, inner, note) {
    if (!inner) return "";
    return '<section class="sg-md-sec"><h3>' + esc(title) + "</h3>" +
      (note ? '<p class="sg-md-note">' + note + "</p>" : "") + inner + "</section>";
  }

  function lateness(d) { return d + (d === 1 ? " day" : " days") + " late"; }

  function render(d) {
    var late = d.delay_days > 0.05;
    var h = "";

    h += '<header class="sg-md-hd is-' + esc(d.tone) + '">' +
      '<p class="sg-md-eyebrow">' + esc(d.type === "WO" ? "routing step" : d.type) +
        " &middot; ref " + esc(d.ref) + "</p>" +
      '<h2 id="sg-md-title">' + esc(d.item || d.operation || d.ref) + "</h2>" +
      (d.item_description ? '<p class="sg-md-sub">' + esc(d.item_description) + "</p>" : "") +
      (d.operation ? '<p class="sg-md-op">' + esc(d.operation) + "</p>" : "") +
      "</header>";

    // -- the numbers a planner reads first
    var badge = late
      ? '<span class="sieger-stat sieger-stat-warn">' + esc(lateness(d.delay_days)) + "</span>"
      : '<span class="sieger-stat sieger-stat-ok">on time</span>';
    h += '<div class="sg-md-facts">' + badge +
      '<span class="sieger-stat">qty <b>' + esc(d.quantity) + "</b></span>" +
      '<span class="sieger-stat"><b>' + d.hours + "</b> h</span>" +
      '<span class="sieger-stat sieger-stat-quiet">' + esc(d.status) + "</span>" +
      (d.criticality >= 999 ? '<span class="sieger-stat sieger-stat-bad">infeasible</span>' : "") +
      "</div>";

    h += '<dl class="sg-md-kv">' +
      "<dt>starts</dt><dd>" + esc(d.start) + "</dd>" +
      "<dt>ends</dt><dd>" + esc(d.end) + "</dd>" +
      "<dt>machine</dt><dd>" +
        (d.machines.length
          ? d.machines.map(function (m) {
              return esc(m.name) + (m.setup ? " <em>setup " + esc(m.setup) + "</em>" : "");
            }).join(", ")
          : "&mdash;") +
      "</dd></dl>";

    // -- the whole routing, this step highlighted
    if (d.job.steps.length) {
      h += section("routing — job " + d.job.ref,
        '<table class="sg-md-tb"><thead><tr><th>step</th><th>machine</th>' +
        "<th>from</th><th>to</th><th>qty</th><th>late</th></tr></thead><tbody>" +
        rows(d.job.steps, [
          function (r) { return esc(r.operation || r.ref); },
          function (r) { return esc(r.machines.join(", ")) || "&mdash;"; },
          function (r) { return esc(r.start); },
          function (r) { return esc(r.end); },
          function (r) { return esc(r.quantity); },
          function (r) { return r.delay_days > 0.05 ? r.delay_days + " d" : "&mdash;"; }
        ]) + "</tbody></table>",
        "The highlighted row is the block you clicked.");
    }

    // -- components
    var matTable = function (list) {
      return '<table class="sg-md-tb"><thead><tr><th>part</th><th>description</th>' +
        "<th>qty</th><th>when</th><th>on hand</th></tr></thead><tbody>" +
        rows(list, [
          function (r) { return esc(r.item); },
          function (r) { return esc(r.description); },
          function (r) { return esc(r.quantity); },
          function (r) { return esc(r.date); },
          function (r) { return esc(r.onhand) || "&mdash;"; }
        ]) + "</tbody></table>";
    };
    h += section("components consumed",
      d.consumes.length ? matTable(d.consumes) : "<p class=\"sg-md-empty\">No components are linked to this step yet &mdash; the bill of materials has not been loaded.</p>");
    if (d.produces.length) h += section("produces", matTable(d.produces));

    // -- what it feeds, and why it slips
    if (d.orders.length) {
      h += section("customer orders this feeds",
        d.orders.map(function (o) {
          return '<div class="sg-md-ord' + (o.delay_days > 0.05 ? " is-late" : "") + '">' +
            '<p class="sg-md-ord-hd"><a class="sg-md-trace" href="' + esc(o.trace) +
              '" title="trace this order on one axis"><b>' + esc(o.name) + "</b></a> &middot; " + esc(o.item) +
              (o.customer ? " &middot; " + esc(o.customer) : "") + "</p>" +
            '<dl class="sg-md-kv sg-md-kv-tight">' +
              "<dt>due</dt><dd>" + esc(o.due) + "</dd>" +
              "<dt>planned</dt><dd>" + esc(o.delivery) +
                (o.delay_days > 0.05 ? ' <b class="sg-md-bad">' + esc(lateness(o.delay_days)) + "</b>" : "") + "</dd>" +
              "<dt>qty</dt><dd>" + esc(o.quantity) + "</dd>" +
            "</dl>" +
            (o.reasons.length
              ? '<ul class="sg-md-why">' + o.reasons.map(function (r) {
                  return "<li><b>" + esc(r.name) + "</b> &mdash; " + esc(r.description) +
                    '<span class="sg-md-when">' + esc(r.start) + " &rarr; " + esc(r.end) + "</span></li>";
                }).join("") + "</ul>"
              : "") +
            "</div>";
        }).join(""));
    }

    /* 'trace' first and emphasised: following one job across the board
       means paging the window, which is the thing this link exists to
       stop. It lands on the order's project timeline - every operation
       of the order on one axis fitted to its own span. */
    h += '<footer class="sg-md-ft">' +
      (d.links.trace
        ? '<a class="sieger-btn sieger-btn-go" href="' + esc(d.links.trace) +
          '">trace this job</a>'
        : "") +
      '<a class="sieger-btn" href="' + esc(d.links.mo) + '">open the job</a>' +
      '<a class="sieger-btn" href="' + esc(d.links.constraint) + '">why late (all)</a>' +
      (d.job.operation ? '<a class="sieger-btn" href="' + esc(d.links.routing) + '">routing</a>' : "") +
      "</footer>";

    body.innerHTML = h;
  }

  function show(ref, el) {
    opener = el;
    open();
    if (cache[ref]) { render(cache[ref]); return; }
    body.innerHTML = '<p class="sg-md-load">loading&hellip;</p>';
    fetch(opUrl + encodeURIComponent(ref) + "/", {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) { cache[ref] = d; render(d); })
      .catch(function (e) {
        body.innerHTML = '<p class="sg-md-load">Could not load this operation (' +
          esc(e.message) + ").</p>";
      });
  }

  /* -------------------------------------------- collapsible sections */
  // Which sections are folded away survives navigation - a sheet-metal
  // planner should not have to re-collapse the machine shop every page.
  var LS_COLLAPSED = "sieger.sched.collapsed";

  function readCollapsed() {
    try { return JSON.parse(localStorage.getItem(LS_COLLAPSED)) || []; }
    catch (e) { return []; }
  }

  function writeCollapsed(list) {
    try { localStorage.setItem(LS_COLLAPSED, JSON.stringify(list)); }
    catch (e) { /* private mode - collapse still works, just not sticky */ }
  }

  function setCollapsed(sec, collapsed) {
    var bodyEl = sec.querySelector(".sg-sc-sec-body");
    var hd = sec.querySelector(".sg-sc-sec-hd");
    if (!bodyEl || !hd) return;
    bodyEl.hidden = collapsed;
    hd.setAttribute("aria-expanded", collapsed ? "false" : "true");
    sec.classList.toggle("is-collapsed", collapsed);
  }

  var collapsed = readCollapsed();
  Array.prototype.forEach.call(root.querySelectorAll(".sg-sc-sec"), function (sec) {
    if (collapsed.indexOf(sec.dataset.sec) !== -1) setCollapsed(sec, true);
    var hd = sec.querySelector(".sg-sc-sec-hd");
    if (!hd) return;
    hd.addEventListener("click", function () {
      var now = !sec.classList.contains("is-collapsed");
      setCollapsed(sec, now);
      var list = readCollapsed().filter(function (c) { return c !== sec.dataset.sec; });
      if (now) list.push(sec.dataset.sec);
      writeCollapsed(list);
    });
  });

  /* ------------------------------------------------ order filter nav */
  var demandSel = document.getElementById("sg-sc-demand");
  if (demandSel) {
    demandSel.addEventListener("change", function () {
      var base = demandSel.dataset.base || "?";
      location.href = demandSel.value
        ? base + "&demand=" + encodeURIComponent(demandSel.value)
        : base;
    });
  }

  /* --------------------------------------------- shipment filter nav */
  var dsqSel = document.getElementById("sg-sc-dsq");
  if (dsqSel) {
    dsqSel.addEventListener("change", function () {
      var base = dsqSel.dataset.base || "?";
      location.href = dsqSel.value
        ? base + "&dsq=" + encodeURIComponent(dsqSel.value)
        : base;
    });
  }

  /* ------------------------------------- colour by order + highlight */
  var LS_BYORDER = "sieger.sched.byorder";
  var byOrderBtn = document.getElementById("sg-byorder");
  var hlDemand = null;

  function byOrderOn() { return root.classList.contains("is-byorder"); }

  function setByOrder(on) {
    root.classList.toggle("is-byorder", on);
    if (byOrderBtn) {
      byOrderBtn.setAttribute("aria-pressed", on ? "true" : "false");
      byOrderBtn.classList.toggle("sieger-btn-go", on);
    }
    if (!on) clearHighlight();
    try { localStorage.setItem(LS_BYORDER, on ? "1" : "0"); } catch (e) {}
  }

  function clearHighlight() {
    hlDemand = null;
    Array.prototype.forEach.call(root.querySelectorAll(".sg-sc-bar"), function (b) {
      b.classList.remove("is-dim", "is-hl");
    });
  }

  function highlight(demand) {
    hlDemand = demand;
    Array.prototype.forEach.call(root.querySelectorAll(".sg-sc-bar"), function (b) {
      var same = b.dataset.demand && b.dataset.demand === demand;
      b.classList.toggle("is-hl", same);
      b.classList.toggle("is-dim", !same);
    });
  }

  if (byOrderBtn) {
    byOrderBtn.addEventListener("click", function () { setByOrder(!byOrderOn()); });
    try { if (localStorage.getItem(LS_BYORDER) === "1") setByOrder(true); } catch (e) {}
  }

  /* ------------------------------------------------- event delegation */
  if (chart) {
    chart.addEventListener("click", function (ev) {
      var bar = ev.target.closest(".sg-sc-bar");
      if (!bar || !bar.dataset.ref) {
        // A click on empty track clears the highlight.
        if (hlDemand && !ev.target.closest(".sg-sc-sec-hd")) clearHighlight();
        return;
      }
      if (byOrderOn()) {
        // In colour mode a single click traces the order; details move
        // to double-click / Enter (the legend says so).
        if (hlDemand === bar.dataset.demand) clearHighlight();
        else if (bar.dataset.demand) highlight(bar.dataset.demand);
        return;
      }
      show(bar.dataset.ref, bar);
    });
    chart.addEventListener("dblclick", function (ev) {
      var bar = ev.target.closest(".sg-sc-bar");
      if (bar && bar.dataset.ref && byOrderOn()) show(bar.dataset.ref, bar);
    });
    chart.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      var bar = ev.target.closest(".sg-sc-bar");
      if (bar && bar.dataset.ref) { ev.preventDefault(); show(bar.dataset.ref, bar); }
    });
  }

  if (modal) {
    modal.addEventListener("click", function (ev) {
      if (ev.target.hasAttribute("data-close")) close();
    });
  }
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      if (modal && !modal.hidden) close();
      else if (hlDemand) clearHighlight();
    }
  });
})();

/*
 * Drag-and-drop rescheduling (M4).
 *
 * A second, self-contained IIFE: pointer-capture drag (the technique
 * proven in build-graph.js), a server-side validate, a confirm dialog in
 * the existing modal shell, then pin + replan + reload. The banner and
 * its polling live here too. Everything is gated on data-can-move, which
 * the view sets from the user's actual permissions.
 */
(function () {
  "use strict";

  var root = document.getElementById("sg-sc");
  if (!root) return;
  var chart = root.querySelector(".sg-sc-chart");
  var moveUrl = root.dataset.moveUrl;
  var statusUrl = root.dataset.statusUrl;
  var canMove = root.dataset.canMove === "1";
  var windowStart = root.dataset.windowStart ? new Date(root.dataset.windowStart) : null;
  var windowSeconds = parseInt(root.dataset.windowSeconds, 10) || 0;

  function getCookie(name) {
    var m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function postJSON(url, payload, ok, bad) {
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken")
      },
      body: JSON.stringify(payload)
    })
      .then(function (r) {
        return r.json().then(function (d) { return { s: r.status, d: d }; });
      })
      .then(function (r) {
        if (r.s === 200 && r.d && r.d.ok) ok(r.d); else bad(r.d || {}, r.s);
      })
      .catch(function (e) { bad({ error: String(e) }, 0); });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function isoLocal(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function fmt(d) {
    var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return days[d.getDay()] + " " + pad(d.getDate()) + " " + mons[d.getMonth()] +
      ", " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  /* ------------------------------------------------------- replan poll */
  var banner = document.getElementById("sg-mv-banner");
  var pollTimer = null;

  function poll(taskid) {
    fetch(statusUrl + "?id=" + taskid, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var prog = document.getElementById("sg-mv-progress");
        if (prog && d.status) {
          var b = prog.querySelector("b");
          if (b) b.textContent = d.status;
        }
        if (d.status === "Done" || d.status === "Failed" || d.status === "Canceled") {
          location.reload();
        } else {
          pollTimer = setTimeout(function () { poll(taskid); }, 2500);
        }
      })
      .catch(function () {
        pollTimer = setTimeout(function () { poll(taskid); }, 5000);
      });
  }

  if (root.dataset.activeTask) poll(root.dataset.activeTask);

  /* ---------------------------------------------------- banner actions */
  var undoBtn = document.getElementById("sg-mv-undo");
  var dismissBtn = document.getElementById("sg-mv-dismiss");
  if (undoBtn && banner) {
    undoBtn.addEventListener("click", function () {
      undoBtn.disabled = true;
      postJSON(moveUrl + "undo/", { undo_id: banner.dataset.undoId },
        function () { location.reload(); },
        function (d) {
          undoBtn.disabled = false;
          alert(d.error === "replan_running"
            ? "A replan is still running - try again when it finishes."
            : "Undo failed: " + (d.error || "unknown"));
        });
    });
  }
  if (dismissBtn) {
    dismissBtn.addEventListener("click", function () {
      postJSON(moveUrl + "dismiss/", {},
        function () { location.reload(); },
        function () { location.reload(); });
    });
  }

  /* --------------------------------------------------------- the drag */
  if (!canMove || !chart || !windowStart || !windowSeconds) return;

  var drag = null;      // null | {bar, ref, ...}
  var justDragged = false;

  function movable(bar) {
    var s = bar.dataset.status;
    return s === "proposed" || s === "approved";
  }

  function rowRects() {
    return Array.prototype.map.call(
      chart.querySelectorAll(".sg-sc-row"),
      function (row) {
        var track = row.querySelector(".sg-sc-track");
        return {
          machine: row.dataset.machine,
          row: row,
          rect: row.getBoundingClientRect(),
          track: track,
          trackRect: track.getBoundingClientRect()
        };
      }
    );
  }

  function timeAt(clientX, trackRect, grabPx) {
    var frac = (clientX - grabPx - trackRect.left) / trackRect.width;
    var secs = frac * windowSeconds;
    secs = Math.round(secs / 900) * 900;               // 15-minute snap
    return new Date(windowStart.getTime() + secs * 1000);
  }

  function overlapHint(rowInfo, start, durMs, selfRef) {
    var end = new Date(start.getTime() + durMs);
    var bars = rowInfo.track.querySelectorAll(".sg-sc-bar");
    for (var i = 0; i < bars.length; i++) {
      var b = bars[i];
      if (b.dataset.ref === selfRef) continue;
      var bs = new Date(b.dataset.start), be = new Date(b.dataset.end);
      if (bs < end && be > start) return true;
    }
    return false;
  }

  function cleanup() {
    if (!drag) return;
    if (drag.ghost && drag.ghost.parentNode) drag.ghost.parentNode.removeChild(drag.ghost);
    if (drag.tip && drag.tip.parentNode) drag.tip.parentNode.removeChild(drag.tip);
    drag.bar.classList.remove("is-moving");
    Array.prototype.forEach.call(chart.querySelectorAll(".sg-sc-row.is-drop"),
      function (r) { r.classList.remove("is-drop"); });
    document.body.classList.remove("sg-mv-on");
    drag = null;
  }

  chart.addEventListener("pointerdown", function (ev) {
    if (ev.button !== 0 || drag) return;
    var bar = ev.target.closest(".sg-sc-bar");
    if (!bar || !bar.dataset.ref || !movable(bar)) return;
    var rect = bar.getBoundingClientRect();
    drag = {
      state: "armed",
      bar: bar,
      ref: bar.dataset.ref,
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      grabPx: ev.clientX - rect.left,
      width: rect.width,
      height: rect.height,
      durMs: new Date(bar.dataset.end) - new Date(bar.dataset.start),
      rows: null,
      ghost: null,
      tip: null,
      target: null,
      when: null
    };
    bar.setPointerCapture(ev.pointerId);
  });

  chart.addEventListener("pointermove", function (ev) {
    if (!drag || drag.state === "validating") return;

    if (drag.state === "armed") {
      if (Math.abs(ev.clientX - drag.startX) < 4 &&
          Math.abs(ev.clientY - drag.startY) < 4) return;
      // Crossing the threshold: build the ghost and the row map.
      drag.state = "dragging";
      drag.rows = rowRects();
      var ghost = drag.bar.cloneNode(true);
      ghost.className = drag.bar.className + " sg-mv-ghost";
      ghost.removeAttribute("title");
      ghost.style.left = "0";
      ghost.style.top = "0";
      ghost.style.width = drag.width + "px";
      ghost.style.height = drag.height + "px";
      document.body.appendChild(ghost);
      var tip = document.createElement("div");
      tip.className = "sg-mv-tip";
      document.body.appendChild(tip);
      drag.ghost = ghost;
      drag.tip = tip;
      drag.bar.classList.add("is-moving");
      document.body.classList.add("sg-mv-on");
    }

    ev.preventDefault();

    // Find the row under the pointer.
    var over = null;
    for (var i = 0; i < drag.rows.length; i++) {
      var r = drag.rows[i];
      if (ev.clientY >= r.rect.top && ev.clientY <= r.rect.bottom) { over = r; break; }
    }
    Array.prototype.forEach.call(chart.querySelectorAll(".sg-sc-row.is-drop"),
      function (el) { el.classList.remove("is-drop"); });

    drag.ghost.style.transform =
      "translate(" + (ev.clientX - drag.grabPx) + "px," +
      (ev.clientY - drag.height / 2) + "px)";

    if (over) {
      over.row.classList.add("is-drop");
      drag.target = over.machine;
      drag.when = timeAt(ev.clientX, over.trackRect, drag.grabPx);
      var clash = overlapHint(over, drag.when, drag.durMs, drag.ref);
      drag.tip.textContent = fmt(drag.when) + "  →  " + over.machine;
      drag.tip.classList.toggle("is-clash", clash);
    } else {
      drag.target = null;
      drag.when = null;
      drag.tip.textContent = "drop on a machine row";
      drag.tip.classList.remove("is-clash");
    }
    drag.tip.style.transform =
      "translate(" + (ev.clientX + 14) + "px," + (ev.clientY + 18) + "px)";
  });

  function confirmDialog(d) {
    var modal = document.getElementById("sg-md");
    var body = document.getElementById("sg-md-body");
    if (!modal || !body) { cleanup(); return; }
    var p = d.proposed;
    var h = '<header class="sg-md-hd"><p class="sg-md-eyebrow">move &middot; ref ' +
      esc(p.ref) + '</p><h2 id="sg-md-title">' + esc(p.label) + "</h2></header>";
    h += '<dl class="sg-md-kv">' +
      "<dt>to machine</dt><dd><b>" + esc(p.resource) + "</b>" +
      (p.from_resource && p.from_resource !== p.resource
        ? " (was " + esc(p.from_resource) + ")" : "") + "</dd>" +
      "<dt>new start</dt><dd>" + esc(p.start_txt) +
      " (was " + esc(p.old_start_txt) + ")</dd>" +
      "<dt>new end</dt><dd>" + esc(p.end_txt) + " &middot; " + p.hours + " h</dd></dl>";
    if (d.concerns.length) {
      h += '<section class="sg-md-sec"><h3>concerns</h3><ul class="sg-mv-concerns">' +
        d.concerns.map(function (c) {
          var inner = "<b>" + esc(c.message) + "</b>";
          if (c.detail && c.detail.length) {
            inner += "<br><small>" + c.detail.map(function (o) {
              return esc(o.ref) + (o.item ? " (" + esc(o.item) + ")" : "") +
                " " + esc(o.start) + " → " + esc(o.end);
            }).join("<br>") + "</small>";
          }
          return '<li class="is-' + esc(c.severity) + '">' + inner + "</li>";
        }).join("") + "</ul></section>";
    } else {
      h += '<p class="sg-md-note">No concerns - the slot is free, on shift and capable.</p>';
    }
    h += '<p class="sg-md-note">Confirming pins the job there (it will not move again ' +
      "until un-pinned) and re-plans everything else around it. That takes a few " +
      "minutes; the board reloads when it finishes.</p>";
    h += '<div id="sg-mv-err" class="sg-rl-err" hidden></div>';
    h += '<footer class="sg-md-ft">' +
      '<button type="button" class="sieger-btn sieger-btn-go" id="sg-mv-confirm">pin &amp; replan</button>' +
      '<button type="button" class="sieger-btn" data-close>cancel</button></footer>';
    body.innerHTML = h;
    modal.hidden = false;
    document.body.classList.add("sg-md-on");

    var confirmBtn = document.getElementById("sg-mv-confirm");
    confirmBtn.addEventListener("click", function () {
      confirmBtn.disabled = true;
      var err = document.getElementById("sg-mv-err");
      postJSON(moveUrl + "apply/", {
        ref: p.ref, resource: p.resource, start: p.start
      }, function () {
        location.reload();
      }, function (e) {
        confirmBtn.disabled = false;
        err.hidden = false;
        err.textContent = {
          replan_running: "A replan is already running - wait for it, then drop again.",
          pending_move: "The previous move is still replanning - undo or dismiss it first.",
          not_movable: "This job is no longer movable - the plan changed under you. Reload.",
          forbidden: "You do not have permission to move jobs."
        }[e.error] || ("Could not apply the move: " + (e.error || "unknown"));
      });
    });
  }

  chart.addEventListener("pointerup", function (ev) {
    if (!drag) return;
    if (drag.state !== "dragging") { drag = null; return; }  // plain click
    justDragged = true;
    setTimeout(function () { justDragged = false; }, 0);

    if (!drag.target || !drag.when) { cleanup(); return; }
    drag.state = "validating";
    drag.tip.textContent = "checking…";
    var ref = drag.ref, target = drag.target, when = isoLocal(drag.when);
    postJSON(moveUrl + "validate/", { ref: ref, resource: target, start: when },
      function (d) { cleanup(); confirmDialog(d); },
      function (e) {
        cleanup();
        alert(e.error === "replan_running"
          ? "A replan is running - try again when it finishes."
          : e.error === "not_movable"
            ? "This job cannot be moved (status " + (e.status_now || "?") + ")."
            : "Could not check the move: " + (e.error || "unknown"));
      });
  });

  chart.addEventListener("pointercancel", function () { cleanup(); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && drag) cleanup();
  });

  // A drag must not fire the click-through modal on release.
  chart.addEventListener("click", function (ev) {
    if (justDragged) { ev.stopPropagation(); ev.preventDefault(); }
  }, true);
})();
