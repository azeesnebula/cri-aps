/* ------------------------------------------------------------------
   Sieger APS - the shipment map.

   A progressive mind map of the plan. Three kinds of card:

     ship   one delivery sequence, placed on the time axis
     group  one section of the plant working for that shipment
     run    one production run inside that section

   NOTHING IS DRAWN UNTIL IT IS ASKED FOR. The old screen built every
   run of a shipment up front - 2,148 cards, 2,400 wires - and was both
   unreadable and slow. Here a level is fetched when its arrow is
   clicked, so what is on screen is only ever what a planner opened:
   tens of cards, not thousands, and the page stays instant however big
   the plan gets.

   ONE BRANCH AT A TIME
   --------------------
   Opening a card closes anything open that is not on its own path.
   Open another shipment and the previous one folds away completely.
   That is what keeps the picture a mind map rather than an everything-
   at-once diagram, and it is also why the node count stays bounded.

   THE TIME AXIS COMPRESSES DEAD SPACE
   -----------------------------------
   Cards sit on a real date axis, latest at the top. But a plan has
   weeks where nothing happens, and drawing those to scale leaves a
   screen of blank page. So a gap longer than GAP_DAYS collapses to a
   fixed-height marker that says how much time passed. Distance is
   therefore honest about ORDER and about DURATION-within-a-stretch,
   and explicit about what it skipped.
   ------------------------------------------------------------------ */

(function () {
  "use strict";

  var root = document.getElementById("sg-mp");
  if (!root) return;

  // -- geometry ---------------------------------------------------------
  var CARD_W_FULL = 232, CARD_W_BRIEF = 150;
  var CARD_W = CARD_W_FULL;
  var H_GAP_FULL = 20, H_GAP_BRIEF = 12;
  var H_GAP = H_GAP_FULL;
  var ROW_GAP_FULL = 26, ROW_GAP_BRIEF = 14;
  var ROW_GAP = ROW_GAP_FULL;
  var PAD = 34;
  var PX_PER_DAY = 11;     // inside a continuous stretch
  var GAP_DAYS = 2.5;      // longer than this collapses to a marker
  var GAP_BOX = 46;        // the marker's height
  var BAND_MIN = 30;
  var MAX_COLS_FULL = 8, MAX_COLS_BRIEF = 14;
  var MAX_COLS = MAX_COLS_FULL;   // cards per row before wrapping

  // -- elements ---------------------------------------------------------
  var canvas = document.getElementById("mp-canvas");
  var world = document.getElementById("mp-world");
  var wires = document.getElementById("mp-wires");
  var timeIn = document.getElementById("mp-time-in");
  var msg = document.getElementById("mp-msg");
  var sub = document.getElementById("mp-sub");
  var findBox = document.getElementById("mp-q");
  var hits = document.getElementById("mp-hits");
  var stepLbl = document.getElementById("mp-step");

  // -- state ------------------------------------------------------------
  var SHIPS = [];          // level 0, loaded once
  var node = {};           // id -> node data (every node ever loaded)
  var kids = {};           // id -> [child id] once its level is loaded
  var parent = {};         // id -> parent id
  var open = {};           // id -> true when its children are shown
  var loading = {};        // id -> true while its fetch is in flight
  var els = {};            // id -> element CURRENTLY in the DOM
  var cardEl = {};         // id -> element, built once and reused
  var cardOpen = {};       // id -> the open state its HTML was built for
  var kindH = {};          // card kind -> measured height
  var rowHeights = {};     // row -> height, from the last layout
  var VIRT_PAD = 700;      // px of off-screen margin kept alive
  var L = null;            // current layout
  var selected = null;
  var focusId = null;      // the card the delay walk is standing on

  /* DETAILED or BRIEF.

     Detailed is the full card: dates, counts, machines, pills. Brief
     strips a node back to a dot, a name and one number, so the SHAPE
     of a build is readable at a glance instead of the detail of it -
     the same graph, read from further away. The planner's choice
     sticks, because it is a working preference, not a one-off. */
  var MODE = "detailed";
  try {
    var saved = localStorage.getItem("sg-mp-mode");
    if (saved === "brief" || saved === "detailed") MODE = saved;
  } catch (e) { /* private window: the default is fine */ }
  var brief = function () { return MODE === "brief"; };
  var delays = [];         // the delay walk, worst first
  var delayAt = -1;
  var secCache = {};       // tag -> sections payload
  var runCache = {};       // "tag|section" -> runs payload

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  /* Switching mode changes every measurement, so the cached card
     elements and the measured heights must all go - otherwise brief
     nodes get laid out on detailed-sized rows. */
  function applyMode() {
    CARD_W = brief() ? CARD_W_BRIEF : CARD_W_FULL;
    H_GAP = brief() ? H_GAP_BRIEF : H_GAP_FULL;
    ROW_GAP = brief() ? ROW_GAP_BRIEF : ROW_GAP_FULL;
    MAX_COLS = brief() ? MAX_COLS_BRIEF : MAX_COLS_FULL;
    root.classList.toggle("is-brief", brief());
    cardEl = {};
    cardOpen = {};
    kindH = {};
    Object.keys(els).forEach(function (id) {
      if (els[id].parentNode) els[id].parentNode.removeChild(els[id]);
    });
    els = {};
    Array.prototype.forEach.call(
      root.querySelectorAll("[data-act=\"mode-detailed\"],[data-act=\"mode-brief\"]"),
      function (b) {
        b.classList.toggle("is-on",
          b.getAttribute("data-act") === (brief() ? "mode-brief" : "mode-detailed"));
      });
  }

  // ------------------------------------------------------------ helpers

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function ms(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isNaN(t) ? null : t;
  }

  function dayLabel(t) {
    var d = new Date(t);
    return d.getDate() + " " + MONTHS[d.getMonth()];
  }

  function spanLabel(days) {
    if (days >= 60) return Math.round(days / 30) + " months pass";
    if (days >= 13) return Math.round(days / 7) + " weeks pass";
    if (days >= 2) return Math.round(days) + " days pass";
    return Math.round(days * 24) + " hours pass";
  }

  function api(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function say(text, bad) {
    if (!msg) return;
    msg.hidden = false;
    msg.innerHTML = "<p" + (bad ? ' class="is-bad"' : "") + ">" + esc(text) + "</p>";
  }

  // --------------------------------------------------------- the tree
  //
  // visible() walks from the shipments down through whatever is open,
  // which IS the render list. Nothing else is in the DOM.

  function visible() {
    var out = [];
    SHIPS.forEach(function (s) {
      out.push(s.id);
      if (open[s.id]) descend(s.id, out);
    });
    return out;
  }

  function descend(id, out) {
    (kids[id] || []).forEach(function (c) {
      out.push(c);
      if (open[c]) descend(c, out);
    });
  }

  /* Everything from a node up to its root - the path that stays open. */
  function chainOf(id) {
    var out = {}, cur = id;
    while (cur) { out[cur] = true; cur = parent[cur]; }
    return out;
  }

  /* The "unrelated things fold away" rule.

     Opening a card keeps open: its own ancestors, anything beneath it,
     and any SECTION IT EXCHANGES WORK WITH - because two sections that
     feed each other are part of one story and folding one of them away
     while reading the other would hide the connection. Everything else
     closes. That is what keeps this a mind map, and it is also the
     bound on how many cards can ever be on screen at once. */
  function related(id) {
    var keep = chainOf(id);
    var n = node[id];
    if (!n || n.kind !== "group") return keep;

    // Walk the section flow both ways from this one, within its shipment.
    var sibs = (kids[parent[id]] || []);
    var feeds = {}, fedBy = {};
    sibs.forEach(function (sid) {
      (node[sid].feeders || []).forEach(function (f) {
        (feeds[f] = feeds[f] || []).push(sid);
        (fedBy[sid] = fedBy[sid] || []).push(f);
      });
    });
    var seen = {}, queue = [id];
    while (queue.length) {
      var cur = queue.shift();
      if (seen[cur]) continue;
      seen[cur] = true;
      keep[cur] = true;
      (feeds[cur] || []).forEach(function (x) { if (!seen[x]) queue.push(x); });
      (fedBy[cur] || []).forEach(function (x) { if (!seen[x]) queue.push(x); });
    }
    return keep;
  }

  function pruneTo(id) {
    var keep = related(id);
    Object.keys(open).forEach(function (k) {
      if (!keep[k] && !isAncestor(id, k)) delete open[k];
    });
  }

  function isAncestor(id, maybe) {
    var cur = parent[maybe];
    while (cur) { if (cur === id) return true; cur = parent[cur]; }
    return false;
  }

  function register(list, parentId) {
    var ids = [];
    list.forEach(function (n) {
      node[n.id] = n;
      parent[n.id] = parentId;
      ids.push(n.id);
    });
    if (parentId) kids[parentId] = ids;
    return ids;
  }

  // ------------------------------------------------------------ loading

  function openNode(id) {
    var n = node[id];
    if (!n || n.kind === "run") return Promise.resolve();
    if (kids[id]) {                       // already fetched
      pruneTo(id);
      open[id] = true;
      render();
      return Promise.resolve();
    }
    if (loading[id]) return Promise.resolve();
    loading[id] = true;
    paintLoading(id, true);

    var url;
    if (n.kind === "ship") {
      url = root.dataset.mapUrl + encodeURIComponent(n.tag) + "/sections/";
    } else {
      url = root.dataset.mapUrl + encodeURIComponent(n.tag) + "/" +
            encodeURIComponent(n.section) + "/runs/";
    }

    return api(url).then(function (d) {
      loading[id] = false;
      if (n.kind === "ship") {
        secCache[n.tag] = d;
        register(d.nodes, id);
        (d.edges || []).forEach(function (e) {
          var s = node[e.src], t = node[e.dst];
          if (s && t) { (t.feeders = t.feeders || []).push(e.src); }
        });
      } else {
        runCache[n.tag + "|" + n.section] = d;
        register(d.nodes, id);
      }
      pruneTo(id);
      open[id] = true;
      render();
    }).catch(function (e) {
      loading[id] = false;
      paintLoading(id, false);
      say("Could not open that card (" + e.message + ").", true);
    });
  }

  function closeNode(id) {
    delete open[id];
    render();
  }

  function toggle(id) {
    if (open[id]) closeNode(id); else openNode(id);
  }

  function paintLoading(id, on) {
    if (els[id]) els[id].classList.toggle("is-loading", !!on);
  }

  // ------------------------------------------------------------- layout
  //
  // One column per branch, time down the page, dead space collapsed.

  function layout(ids, rowH) {
    rowH = rowH || {};
    var V = {};
    ids.forEach(function (id) { V[id] = node[id]; });

    // 1. a row per distinct date, latest first
    var stamps = {};
    ids.forEach(function (id) {
      var t = ms(V[id].end) || ms(V[id].due) || null;
      if (t !== null) stamps[t] = true;
    });
    var times = Object.keys(stamps).map(Number).sort(function (a, b) {
      return b - a;
    });
    if (!times.length) times = [0];
    var rowOf = {};
    times.forEach(function (t, i) { rowOf[t] = i; });

    var row = {};
    ids.forEach(function (id) {
      var t = ms(V[id].end) || ms(V[id].due);
      row[id] = t === null || t === undefined ? times.length - 1 : rowOf[t];
    });

    // 2. row tops. Heights come from MEASUREMENT (rowH, filled by the
    //    first pass of render) - a card's height depends on how its text
    //    wraps, and assuming a fixed 76px silently overlapped rows whose
    //    cards ran to three lines.
    var top = [], gaps = [], y = PAD;
    for (var i = 0; i < times.length; i++) {
      top[i] = y;
      y += (rowH[i] || 86) + ROW_GAP;
      if (i + 1 < times.length) {
        var days = (times[i] - times[i + 1]) / 86400000;
        if (days > GAP_DAYS) {
          gaps.push({ y: y, days: days, label: spanLabel(days) });
          y += GAP_BOX;
        } else {
          y += Math.max(BAND_MIN, days * PX_PER_DAY) - BAND_MIN;
        }
      }
    }

    // 3. x. Shipments hold one column down the left, in date order, so
    //    the whole promise reads as a list against the time axis. The
    //    one that is OPEN hands its subtree the space to the right -
    //    only one shipment can be open at a time, so that space is
    //    never contested.
    //
    //    Inside the subtree a per-row cursor places cards left to
    //    right in tree order: siblings cluster under their parent and
    //    two cards can never share pixels, whatever the dates do. The
    //    earlier version spaced everything by CARD_W while ship cards
    //    are wider, so they overlapped.
    var px = {}, byParent = {};
    ids.forEach(function (id) {
      var p = parent[id] || "__root";
      (byParent[p] = byParent[p] || []).push(id);
    });
    var inView = {};
    ids.forEach(function (id) { inView[id] = true; });

    var SHIP_W = Math.round(CARD_W * 1.18);
    var xBase = PAD + SHIP_W + 56;   // recomputed once ships are placed
    var rowN = {};                   // row -> how many cards placed so far
    var sub = {};                    // id -> sub-row inside its row

    function widthOf(id) {
      return V[id].kind === "ship" ? SHIP_W : CARD_W;
    }
    function placeSub(id) {
      var r = row[id];
      var i = rowN[r] === undefined ? 0 : rowN[r];
      rowN[r] = i + 1;
      px[id] = xBase + (i % MAX_COLS) * (CARD_W + H_GAP);
      sub[id] = Math.floor(i / MAX_COLS);
      (byParent[id] || []).forEach(function (c) {
        if (inView[c]) placeSub(c);
      });
    }

    // Ships first, in their own column - but several shipments can share
    // a ship DATE, and two cards on one row at one x is a total overlap.
    // So the ship column has a per-row cursor too.
    var shipX = {}, shipRight = PAD + SHIP_W;
    (byParent.__root || []).forEach(function (id) {
      var r = row[id];
      var x = shipX[r] === undefined ? PAD : shipX[r];
      px[id] = x;
      shipX[r] = x + SHIP_W + H_GAP;
      if (x + SHIP_W > shipRight) shipRight = x + SHIP_W;
    });

    // The open shipment's subtree starts clear of the widest ship row.
    xBase = shipRight + 56;
    (byParent.__root || []).forEach(function (id) {
      (byParent[id] || []).forEach(function (c) {
        if (inView[c]) placeSub(c);
      });
    });

    var widest = 0;
    Object.keys(rowN).forEach(function (r) {
      var cols = Math.min(rowN[r], MAX_COLS);
      if (cols > widest) widest = cols;
    });
    var W = Math.max(xBase + widest * (CARD_W + H_GAP), shipRight + H_GAP) + PAD;
    var H = y + PAD;
    return { ids: ids, V: V, row: row, sub: sub, top: top, times: times,
             gaps: gaps, px: px, W: Math.max(W, 800), H: H };
  }

  // -------------------------------------------------------------- cards

  /* A brief node: a status dot, a name, one number, and the arrow.
     Nothing else - the point is to see the structure, not to read it. */
  function briefHTML(id) {
    var n = node[id];
    var openable = n.kind !== "run";
    var name, num = "";
    if (n.kind === "ship") {
      name = n.name || n.tag;
      num = n.unplanned ? n.unplanned + "!" :
            (n.delay_days > 0 ? Math.round(n.delay_days) + "d" : "ok");
    } else if (n.kind === "group") {
      name = n.section;
      num = n.late_runs ? n.late_runs + "/" + n.runs : String(n.runs);
    } else {
      name = n.item || n.ref;
      num = n.delay_days > 0 ? Math.round(n.delay_days) + "d" : "";
    }
    return '<span class="sg-mp-dot"></span>' +
      '<span class="sg-mp-bname" title="' + esc(name) + '">' + esc(name) +
      "</span>" +
      (num ? '<span class="sg-mp-bnum">' + esc(num) + "</span>" : "") +
      (openable
        ? '<button class="sg-mp-arrow" type="button" data-toggle="' + esc(id) +
          '" aria-label="open">' + (open[id] ? "\u25b4" : "\u25be") + "</button>"
        : "");
  }

  function cardHTML(id) {
    if (brief()) return briefHTML(id);
    var n = node[id];
    var openable = n.kind !== "run";
    var arrow = openable
      ? '<button class="sg-mp-arrow" type="button" data-toggle="' + esc(id) +
        '" aria-label="open">' + (open[id] ? "▴" : "▾") + "</button>"
      : "";

    var head, meta, tags = [];

    if (n.kind === "ship") {
      head = '<span class="sg-mp-kind">shipment</span>' +
             '<span class="sg-mp-title">' + esc(n.name || n.tag) +
             ' <small class="sg-mp-code">' + esc(n.tag) + "</small></span>";
      meta = "<b>" + n.orders + "</b> orders &middot; due " +
             esc(n.due_txt || "—") + " &middot; ships " +
             esc(n.end_txt || "—");
      if (n.unplanned) tags.push(["is-crit", n.unplanned + " unplanned"]);
      else if (n.delay_days > 0) tags.push(["is-late", n.delay_days + " d late"]);
      else tags.push(["is-ok", "on time"]);
      if (n.blocker) tags.push(["", "held by " + n.blocker]);
    } else if (n.kind === "group") {
      head = '<span class="sg-mp-kind">section</span>' +
             '<span class="sg-mp-title">' + esc(n.section) + "</span>";
      meta = esc(n.descr || "") + "<br><b>" + n.runs + "</b> run" +
             (n.runs === 1 ? "" : "s") +
             (n.machines ? " &middot; " + n.machines + " machine" +
                           (n.machines === 1 ? "" : "s") : "") +
             " &middot; to " + esc(n.end_txt || "—");
      if (n.late_runs) tags.push(["is-late", n.late_runs + " late"]);
      else tags.push(["is-ok", "all on time"]);
      if (n.feeds_ship) tags.push(["is-key", "feeds the shipment"]);
    } else {
      head = '<span class="sg-mp-kind">run ' + esc(n.ref) + "</span>" +
             '<span class="sg-mp-title">' + esc(n.item || n.ref) + "</span>";
      meta = esc(n.descr || "") + "<br>qty <b>" + esc(n.qty) + "</b>" +
             (n.machines ? " &middot; " + esc(n.machines) : "") +
             " &middot; to " + esc(n.end_txt || "—");
      if (n.delay_days > 0) tags.push(["is-late", n.delay_days + " d late"]);
      else tags.push(["is-ok", "on time"]);
      if (n.shared) tags.push(["is-share", "also for " + n.ships + " shipments"]);
      if (n.steps) tags.push(["", n.steps + " step" + (n.steps === 1 ? "" : "s")]);
    }

    return '<div class="sg-mp-hd">' + head + arrow + "</div>" +
      '<p class="sg-mp-meta">' + meta + "</p>" +
      '<div class="sg-mp-tags">' +
      tags.map(function (t) {
        return '<span class="sg-mp-pill ' + t[0] + '">' + esc(t[1]) + "</span>";
      }).join("") +
      "</div>" +
      '<button class="sg-mp-more" type="button" data-more="' + esc(id) +
      '">details &rsaquo;</button>';
  }

  // ------------------------------------------------------------- render

  /* Build one card once and keep it. Rebuilding every card's innerHTML
     on every frame was a large part of what made a big expansion heavy. */
  function makeCard(id) {
    var el = document.createElement("div");
    el.dataset.id = id;
    el.innerHTML = cardHTML(id);
    cardEl[id] = el;
    cardOpen[id] = !!open[id];
    return el;
  }

  function classFor(id) {
    var n = node[id];
    return "sg-mp-node k-" + n.kind + " t-" + (n.tone || "ok") +
      (open[id] ? " is-open" : "") + (selected === id ? " is-sel" : "") +
      (focusId === id ? " is-focus" : "") +
      (loading[id] ? " is-loading" : "");
  }

  /* ONLY WHAT IS ON SCREEN IS IN THE DOM.

     "Open all" on a big shipment lays out thousands of cards, and no
     browser keeps that many boxes, shadows and wires live at 60fps -
     that was the lag. Layout still covers every open node (pure
     arithmetic, sub-millisecond), but only the slice inside the
     viewport plus a margin is ever created, and scrolling swaps the
     slice. The DOM stays ~100 cards however much is open. */
  /* A card's y: its row, plus its sub-row if the row wrapped. */
  function yOf(id) {
    var base = L.top[L.row[id]];
    var sr = (L.sub && L.sub[id]) || 0;
    return base + sr * (cardUnit() + 10);
  }
  function cardUnit() { return kindH.run || 86; }

  function paint() {
    if (!L) return;
    var top = canvas.scrollTop - VIRT_PAD;
    var bot = canvas.scrollTop + canvas.clientHeight + VIRT_PAD;
    var left = canvas.scrollLeft - VIRT_PAD;
    var right = canvas.scrollLeft + canvas.clientWidth + VIRT_PAD;

    var want = {};
    L.ids.forEach(function (id) {
      var y = yOf(id);
      var h = cardUnit();
      var x = L.px[id];
      var w = node[id].kind === "ship" ? Math.round(CARD_W * 1.18) : CARD_W;
      if (y + h >= top && y <= bot && x + w >= left && x <= right) {
        want[id] = true;
      }
    });
    if (selected && L.row[selected] !== undefined) want[selected] = true;

    Object.keys(els).forEach(function (id) {
      if (!want[id]) {
        if (els[id].parentNode) els[id].parentNode.removeChild(els[id]);
        delete els[id];
      }
    });

    var frag = document.createDocumentFragment();
    Object.keys(want).forEach(function (id) {
      if (els[id]) return;
      var el = cardEl[id] || makeCard(id);
      el.style.left = L.px[id] + "px";
      el.style.top = yOf(id) + "px";
      el.className = classFor(id);
      els[id] = el;
      frag.appendChild(el);
    });
    if (frag.childNodes.length) world.appendChild(frag);

    Object.keys(els).forEach(function (id) {
      var c = classFor(id);
      if (els[id].className !== c) els[id].className = c;
    });

    drawWires();
  }

  /* One live sample per card kind: measuring every card would defeat
     virtualisation, and the three kinds have fixed internal layouts. */
  function measureKinds() {
    ["ship", "group", "run"].forEach(function (k) {
      if (kindH[k]) return;
      var id = null;
      for (var i = 0; i < L.ids.length; i++) {
        if (node[L.ids[i]].kind === k) { id = L.ids[i]; break; }
      }
      if (id === null) return;
      var el = cardEl[id] || makeCard(id);
      var detached = !el.parentNode;
      if (detached) {
        el.style.visibility = "hidden";
        el.className = classFor(id);
        world.appendChild(el);
      }
      kindH[k] = el.offsetHeight || 86;
      if (detached) { world.removeChild(el); el.style.visibility = ""; }
    });
  }

  function render() {
    var ids = visible();

    // only cards whose arrow state changed need their HTML rebuilt
    ids.forEach(function (id) {
      if (cardEl[id] && cardOpen[id] !== !!open[id]) {
        cardEl[id].innerHTML = cardHTML(id);
        cardOpen[id] = !!open[id];
      }
    });

    L = layout(ids);
    measureKinds();

    var rowH = {};
    ids.forEach(function (id) {
      var r = L.row[id];
      var stack = ((L.sub && L.sub[id]) || 0) + 1;
      var h = (kindH[node[id].kind] || 86) * stack + (stack - 1) * 10;
      if (!rowH[r] || h > rowH[r]) rowH[r] = h;
    });
    L = layout(ids, rowH);
    rowHeights = rowH;

    world.style.width = L.W + "px";
    world.style.height = L.H + "px";

    Array.prototype.forEach.call(
      world.querySelectorAll(".sg-mp-gap"),
      function (e) { e.parentNode.removeChild(e); });
    drawGaps();

    paint();
    drawTime();
    updateSub();
  }

  function drawGaps() {
    L.gaps.forEach(function (g) {
      var d = document.createElement("div");
      d.className = "sg-mp-gap";
      d.style.top = g.y + "px";
      d.style.width = L.W + "px";
      d.innerHTML = "<span>" + esc(g.label) + "</span>";
      world.appendChild(d);
    });
  }

  function drawWires() {
    var parts = [];
    L.ids.forEach(function (id) {
      var p = parent[id];
      if (!p || !els[p] || !els[id]) return;   // both ends must be live
      var wp = node[p].kind === "ship" ? Math.round(CARD_W * 1.18) : CARD_W;
      var wc = node[id].kind === "ship" ? Math.round(CARD_W * 1.18) : CARD_W;
      var x1 = L.px[p] + wp / 2, y1 = yOf(p) + cardUnit();
      var x2 = L.px[id] + wc / 2, y2 = yOf(id);
      if (y2 < y1) { var t = y1; y1 = y2; y2 = t; }
      var dy = Math.max(brief() ? 10 : 14, (y2 - y1) * (brief() ? 0.55 : 0.4));
      var n = node[id];
      parts.push('<path class="sg-mp-wire w-' + (n.tone || "ok") +
        (n.shared ? " is-share" : "") + '" d="M' + x1 + "," + y1 +
        "C" + x1 + "," + (y1 + dy) + " " + x2 + "," + (y2 - dy) +
        " " + x2 + "," + y2 + '"/>');
    });
    wires.setAttribute("width", L.W);
    wires.setAttribute("height", L.H);
    wires.setAttribute("viewBox", "0 0 " + L.W + " " + L.H);
    wires.innerHTML = parts.join("");
  }

  /* The left axis: a real date per row, and the collapsed gaps named so
     the skipped time is stated rather than silently removed. */
  function drawTime() {
    var out = [];
    var last = -1e9;
    L.times.forEach(function (t, i) {
      var y = L.top[i];
      if (y - last < 26 && i !== 0 && i !== L.times.length - 1) return;
      last = y;
      out.push('<div class="sg-mp-tick" style="top:' + y + 'px">' +
        "<b>" + esc(dayLabel(t)) + "</b><span>" +
        new Date(t).getFullYear() + "</span></div>");
    });
    L.gaps.forEach(function (g) {
      out.push('<div class="sg-mp-tgap" style="top:' + (g.y + 10) + 'px">' +
        esc(g.label) + "</div>");
    });
    timeIn.innerHTML = out.join("");
    timeIn.style.height = L.H + "px";
    syncTime();
  }

  function syncTime() {
    timeIn.style.transform = "translateY(" + -canvas.scrollTop + "px)";
  }

  function updateSub() {
    var nOpen = Object.keys(open).length;
    var late = SHIPS.filter(function (s) { return s.delay_days > 0; }).length;
    sub.textContent = SHIPS.length + " shipments · " + late + " late · " +
      L.ids.length + " cards shown" + (nOpen ? " · " + nOpen + " open" : "");
  }

  // ------------------------------------------------------------- detail
  //
  // What "details ›" opens. Ships and sections have everything on
  // their node already; a run fetches the solver's own record - the
  // pegging split across customer orders, the step sequence, the
  // machines - from the same /build-graph/run/<ref>/ payload the
  // per-order graph shows.

  var dt = document.getElementById("mp-dt");
  var dtKind = document.getElementById("mp-dt-kind");
  var dtTitle = document.getElementById("mp-dt-title");
  var dtBd = document.getElementById("mp-dt-bd");

  function closeDetail() {
    if (dt) dt.hidden = true;
  }

  function kv(pairs) {
    var out = "";
    pairs.forEach(function (p) {
      if (p[1] === "" || p[1] === null || p[1] === undefined) return;
      out += '<div class="sg-mp-dt-row"><i>' + esc(p[0]) + "</i><span>" +
             p[1] + "</span></div>";
    });
    return '<div class="sg-mp-dt-kv">' + out + "</div>";
  }

  function pill(txt, cls) {
    return '<span class="sg-mp-pill ' + (cls || "") + '">' + esc(txt) + "</span>";
  }

  function dtSec(title, body, note) {
    if (!body) return "";
    return '<div class="sg-mp-dt-sec"><p class="sg-mp-dt-sec-t">' + esc(title) +
      "</p>" + body +
      (note ? '<p class="sg-mp-dt-note">' + note + "</p>" : "") +
      "</div>";
  }

  function shipDetail(n) {
    return kv([
      ["shipment", esc(n.tag)],
      ["orders", esc(n.orders)],
      ["due", esc(n.due_txt || "—")],
      ["latest scheduled delivery", esc(n.end_txt || "—")],
      ["status", n.unplanned
        ? pill(n.unplanned + " unplanned", "is-crit")
        : n.delay_days > 0
          ? pill(n.delay_days + " d late", "is-late")
          : pill("on time", "is-ok")],
      ["scheduled lateness", n.delay_days > 0 ? esc(n.delay_days + " d late") : ""],
      ["recorded capacity blocker", n.blocker
        ? esc(n.blocker) + " - " + n.blocker_orders + " order" +
          (n.blocker_orders === 1 ? "" : "s")
        : ""]
    ]) + '<div class="sg-mp-dt-ft"><a class="sg-mp-btn" href="' +
      esc(root.dataset.shipmentUrl) + encodeURIComponent(n.tag) +
      '">Production orders for this shipment</a></div>' +
      (n.unplanned ? '<p class="sg-mp-dt-note">The complete shipment date is unknown while orders remain unplanned.</p>' : '');
  }

  function groupDetail(n) {
    var fedBy = (n.feeders || []).map(function (i) {
      return node[i] ? node[i].section : null;
    }).filter(Boolean);
    return kv([
      ["shipment", esc(n.tag)],
      ["section", esc(n.section)],
      ["description", esc(n.descr || "")],
      ["runs", esc(n.runs) + (n.late_runs ? " - " + n.late_runs + " late" : "")],
      ["worst lateness", n.worst_late > 0
        ? esc(Math.round(n.worst_late * 10) / 10 + " d") : ""],
      ["machines", n.machines ? esc(n.machines) : ""],
      ["window", esc(n.start_txt || "—") + " &rarr; " + esc(n.end_txt || "—")],
      ["fed by", fedBy.length ? esc(fedBy.join(", ")) : ""]
    ]) + (n.feeds_ship
      ? '<p class="sg-mp-dt-note">This section hands its work straight ' +
        "to the delivery - nothing downstream of it inside the shipment.</p>"
      : "");
  }

  function runDetail(d) {
    var orders = d.orders || [], steps = d.steps || [];

    var alloc = "";
    if (orders.length) {
      alloc = '<table class="sg-mp-dt-tb"><thead><tr><th>order</th>' +
        "<th>customer</th><th class=\"num\">qty</th>" +
        "<th class=\"num\">share</th><th>due</th></tr></thead><tbody>" +
        orders.map(function (o) {
          return "<tr><td><a href=\"" + esc(root.dataset.orderUrl) +
            encodeURIComponent(o.demand) + '/">' + esc(o.demand) + "</a></td><td>" +
            esc(o.customer || "—") + '</td><td class="num">' + esc(o.quantity) +
            '</td><td class="num">' + esc(o.share) + "%</td><td>" +
            esc(o.due || "—") + "</td></tr>";
        }).join("") + "</tbody></table>";
    }

    var seq = "";
    if (steps.length) {
      seq = '<table class="sg-mp-dt-tb"><thead><tr><th>seq</th>' +
        "<th>operation</th><th>machine</th><th>from</th><th>to</th>" +
        "</tr></thead><tbody>" +
        steps.map(function (s) {
          return "<tr><td>" + esc(s.seq === null ? "" : s.seq) + "</td><td>" +
            esc(s.operation) + "</td><td>" +
            esc((s.machines || []).join(", ") || "—") + "</td><td>" +
            esc(s.start) + "</td><td>" + esc(s.end) + "</td></tr>";
        }).join("") + "</tbody></table>";
    }

    var links = "";
    if (d.links) {
      links = '<div class="sg-mp-dt-ft">' +
        '<a class="sg-mp-btn" href="' + esc(d.links.mo) + '">open the job</a>' +
        '<a class="sg-mp-btn" href="' + esc(d.links.routing) + '">routing</a>' +
        '<a class="sg-mp-btn" href="' + esc(d.links.op) +
        '">machine schedule</a>' +
        "</div>";
    }

    return kv([
      ["production run", esc(d.ref)],
      ["part", esc(d.item)],
      ["description", esc(d.description)],
      ["routing", esc(d.operation)],
      ["location", esc(d.location)],
      ["quantity", esc(d.quantity) +
        (parseFloat(d.remaining) > 0
          ? " - " + esc(d.remaining) + " uncommitted" : "")],
      ["machines", esc((d.machines || []).join(", "))],
      ["planned start", esc(d.start)],
      ["planned finish", esc(d.end)],
      ["status", esc(d.status)],
      ["lateness", d.delay_days > 0.05
        ? pill(d.delay_days + " d late", "is-late")
        : pill("on time", "is-ok")]
    ]) +
      dtSec("who it is for" +
            (d.shared ? " - shared across " + d.order_count + " orders" : ""),
        alloc,
        d.shared
          ? "The split is the planning engine's own pegging, not an estimate."
          : "") +
      dtSec("operation sequence", seq) +
      links;
  }

  function showDetail(id) {
    var n = node[id];
    if (!n || !dt) return;
    selected = id;
    render();
    dtKind.textContent = n.kind === "ship" ? "shipment" :
      n.kind === "group" ? "section" : "production run";
    dtTitle.textContent = n.kind === "ship" ? n.tag :
      n.kind === "group" ? n.section : (n.item || n.ref);
    dt.hidden = false;
    if (n.kind === "run") {
      dtBd.innerHTML = '<p class="sg-mp-dt-note">loading the run&hellip;</p>';
      api(root.dataset.runUrl + encodeURIComponent(n.ref) + "/")
        .then(function (d) { dtBd.innerHTML = runDetail(d); })
        .catch(function (e) {
          dtBd.innerHTML =
            '<p class="sg-mp-dt-note is-bad">Could not load the run (' +
            esc(e.message) + ").</p>";
        });
    } else {
      dtBd.innerHTML = n.kind === "ship" ? shipDetail(n) : groupDetail(n);
    }
  }

  if (dt) {
    var dtX = document.getElementById("mp-dt-x");
    if (dtX) dtX.addEventListener("click", closeDetail);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !dt.hidden) closeDetail();
    });
  }

  // -------------------------------------------------------- delay walk
  //
  // "Show the delay" opens the branch the delay lives in, instead of
  // flying the camera over folded cards. Each press of "next delay"
  // steps to the next worst section of the same shipment.

  function startDelay() {
    var ship = currentShip();
    if (!ship) { say("Open a shipment first, or click one.", true); return; }
    openNode(ship.id).then(function () {
      var secs = (kids[ship.id] || []).map(function (i) { return node[i]; })
        .filter(function (s) { return s.late_runs > 0; })
        .sort(function (a, b) { return b.worst_late - a.worst_late; });
      if (!secs.length) {
        stepLbl.textContent = "nothing late in this shipment";
        delays = []; delayAt = -1; armNext();
        return;
      }
      delays = secs.map(function (s) { return s.id; });
      delayAt = -1;
      nextDelay();
    });
  }

  function nextDelay() {
    if (!delays.length) return;
    var wrapped = delayAt + 1 >= delays.length;
    delayAt = (delayAt + 1) % delays.length;
    var id = delays[delayAt];
    openNode(id).then(function () {
      focusOn(id);
      var n = node[id];
      stepLbl.textContent = "delay " + (delayAt + 1) + " of " + delays.length +
        " · " + n.section + " · worst " + n.worst_late + " d" +
        (wrapped ? " (back to the worst)" : "");
      armNext();
    });
  }

  function armNext() {
    var b = root.querySelector('[data-act="delay-next"]');
    if (b) b.disabled = delays.length < 1;
  }

  /* The shipment the planner is actually in: the one holding the
     selection, else the one that is open. No guessing beyond that. */
  function openShip() {
    if (selected) {
      var cur = selected;
      while (cur && node[cur] && node[cur].kind !== "ship") cur = parent[cur];
      if (cur && node[cur]) return node[cur];
    }
    var o = SHIPS.filter(function (s) { return open[s.id]; });
    return o.length ? o[0] : null;
  }

  /* The delay walk may legitimately start from nothing, so it falls
     back to the worst shipment. */
  function currentShip() {
    return openShip() || SHIPS.slice().sort(function (a, b) {
      return b.delay_days - a.delay_days;
    })[0];
  }

  /* Scroll to a card and mark it.

     Must work from the LAYOUT, never from els[id]: with virtualisation
     the target is usually NOT in the DOM yet - it is off screen, which
     is exactly why we are scrolling to it. The old version read
     els[id], found nothing and returned, so "next delay" moved the
     counter and the scrollbar but never lit anything.

     Focus is state, not a class poked onto an element, because every
     repaint rewrites className - the ring used to be wiped by the very
     scroll that was bringing it into view. */
  function focusOn(id) {
    if (!L || L.row[id] === undefined) return;
    focusId = id;
    selected = id;                     // keeps the card alive in paint()
    canvas.scrollTo({
      left: Math.max(0, L.px[id] +
            (node[id].kind === "ship" ? CARD_W * 1.18 : CARD_W) / 2 -
            canvas.clientWidth / 2),
      top: Math.max(0, yOf(id) - canvas.clientHeight / 3),
      behavior: "smooth"
    });
    paint();                           // create it now, ringed
  }

  // ------------------------------------------------------------- search

  /* Search runs against the DATABASE, across every shipment.

     Searching only the loaded cards would quietly miss most of the
     plan - a planner typing a part number has usually not opened the
     branch it lives in yet, which is exactly why they are searching.
     Each hit carries its path (shipment -> section), so picking one
     opens the branch down to it. */
  var searchTimer = 0, searchSeq = 0;
  function search(q) {
    q = (q || "").trim();
    if (searchTimer) clearTimeout(searchTimer);
    if (q.length < 2) { hits.hidden = true; return; }
    searchTimer = setTimeout(function () {
      var mine = ++searchSeq;
      hits.innerHTML = "<p>searching the whole plan\u2026</p>";
      hits.hidden = false;
      api(root.dataset.searchUrl + "?q=" + encodeURIComponent(q))
        .then(function (d) {
          if (mine !== searchSeq) return;          // a newer query won
          var list = d.hits || [];
          hits.innerHTML = list.length
            ? list.map(function (h) {
                return '<a href="#" data-path="' +
                  esc((h.path || []).join("|")) + '"><b>' +
                  esc(h.main) + "</b><em>" + esc(h.sub) + "</em></a>";
              }).join("")
            : "<p>Nothing in the plan matches that.</p>";
          hits.hidden = false;
        })
        .catch(function (e) {
          if (mine !== searchSeq) return;
          hits.innerHTML = "<p>Search failed (" + esc(e.message) + ").</p>";
        });
    }, 220);
  }

  /* Open shipment -> section -> focus. Used by search hits and by the
     trouble rail. */
  function goPath(parts) {
    if (!parts || !parts.length) return;
    hits.hidden = true;
    var tag = parts[0], section = parts[1];
    var shipId = "S:" + tag;
    if (!node[shipId]) return;
    openNode(shipId).then(function () {
      if (!section) { selected = shipId; render(); focusOn(shipId); return; }
      var gid = "G:" + tag + ":" + section;
      if (!node[gid]) { selected = shipId; render(); focusOn(shipId); return; }
      openNode(gid).then(function () {
        selected = gid;
        render();
        focusOn(gid);
      });
    });
  }

  /* A rail card names a machine group and the shipments it is holding.
     Clicking it TAKES YOU THERE - the worst shipment that machine
     holds, with that machine's own section already open - instead of
     only tinting cards, which showed a planner where the trouble was
     but not how to reach it. */
  function goBlocker(card) {
    var ships = (card.dataset.ships || "").split(",").filter(Boolean);
    if (!ships.length) return;
    goPath([ships[0], card.dataset.machine]);
  }

  function goTo(id) {
    hits.hidden = true;
    var path = [], cur = id;
    while (cur) { path.unshift(cur); cur = parent[cur]; }
    var i = 0;
    (function step() {
      if (i >= path.length - 1) { selected = id; render(); focusOn(id); return; }
      openNode(path[i++]).then(step);
    })();
  }

  // -------------------------------------------------------------- events

  root.addEventListener("click", function (ev) {
    var t = ev.target.closest("[data-toggle]");
    if (t) { ev.preventDefault(); toggle(t.dataset.toggle); return; }

    var m = ev.target.closest("[data-more]");
    if (m) { ev.preventDefault(); showDetail(m.dataset.more); return; }

    var g = ev.target.closest("[data-go]");
    if (g) { ev.preventDefault(); goTo(g.dataset.go); return; }

    var ph = ev.target.closest("[data-path]");
    if (ph) {
      ev.preventDefault();
      goPath((ph.dataset.path || "").split("|").filter(Boolean));
      return;
    }

    var card = ev.target.closest(".sg-mp-node");
    if (card) {
      selected = card.dataset.id;
      if (node[selected].kind !== "run") toggle(selected); else render();
      return;
    }

    var act = ev.target.closest("[data-act]");
    if (!act) return;
    var a = act.dataset.act;
    if (a === "collapse-all") { open = {}; selected = null; focusId = null;
                                delays = []; delayAt = -1;
                                stepLbl.textContent = "";
                                armNext(); render(); }
    else if (a === "expand-all") {
      // Strictly the shipment the planner is already in. Falling back
      // to "the worst one" would open a shipment they never asked for.
      var ship = openShip();
      if (!ship) {
        stepLbl.textContent = "open a shipment first";
        return;
      }
      // ONE request for the whole shipment, ONE render at the end. The
      // first version fetched each section in turn and re-laid the map
      // after every one - thirty round trips and thirty layouts, which
      // took minutes on a big shipment and read as a hang.
      stepLbl.textContent = "opening everything\u2026";
      openNode(ship.id).then(function () {
        return api(root.dataset.mapUrl + encodeURIComponent(ship.tag) +
                   "/allruns/");
      }).then(function (d) {
        (d.sections || []).forEach(function (sec) {
          if (!node[sec.parent]) return;
          if (!kids[sec.parent]) register(sec.nodes, sec.parent);
          open[sec.parent] = true;
        });
        open[ship.id] = true;
        render();
        stepLbl.textContent = d.dropped
          ? d.shown + " runs shown, " + d.dropped + " more not drawn"
          : d.shown + " runs shown";
      }).catch(function (e) {
        stepLbl.textContent = "";
        say("Could not open everything (" + e.message + ").", true);
      });
    }
    else if (a === "mode-brief" || a === "mode-detailed") {
      var want = a === "mode-brief" ? "brief" : "detailed";
      if (want === MODE) return;
      MODE = want;
      try { localStorage.setItem("sg-mp-mode", MODE); } catch (e) {}
      applyMode();
      render();
      if (focusId) focusOn(focusId);
    }
    else if (a === "delay-first") startDelay();
    else if (a === "delay-next") nextDelay();
  });

  var scrollRaf = 0;
  canvas.addEventListener("scroll", function () {
    syncTime();
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(function () {
      scrollRaf = 0;
      paint();
    });
  }, { passive: true });

  if (findBox) {
    findBox.addEventListener("input", function () { search(findBox.value); });
    findBox.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { findBox.value = ""; hits.hidden = true; }
    });
  }

  // Blocker rail: light the sections that book that machine group.
  var rail = document.getElementById("mp-rail");
  if (rail) {
    rail.addEventListener("click", function (ev) {
      var c = ev.target.closest(".sg-mp-blk");
      if (!c) return;
      ev.preventDefault();
      goBlocker(c);
    });
    rail.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      var c = ev.target.closest(".sg-mp-blk");
      if (!c) return;
      ev.preventDefault();
      goBlocker(c);
    });
  }
  function flag(machine) {   // kept: used to tint a machine's cards
    Object.keys(els).forEach(function (id) {
      var n = node[id];
      var hit = !!machine && (
        (n.kind === "group" && n.section === machine) ||
        (n.kind === "run" && (n.machines || "").indexOf(machine) >= 0) ||
        (n.kind === "ship" && n.blocker === machine));
      els[id].classList.toggle("is-flag", hit);
      els[id].classList.toggle("is-faded", !!machine && !hit);
    });
  }

  // ---------------------------------------------------------------- boot

  applyMode();
  api(root.dataset.shipsUrl).then(function (d) {
    SHIPS = d.ships || [];
    register(SHIPS, null);
    if (msg) msg.hidden = true;
    render();
    // A home-screen or plan-board link may name one shipment: open it.
    var want = new URLSearchParams(location.search).get("dsq");
    if (want && node["S:" + want]) goPath([want]);
  }).catch(function (e) {
    say("Could not load the shipments (" + e.message + ").", true);
  });
})();
