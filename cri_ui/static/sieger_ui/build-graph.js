/* ------------------------------------------------------------------
   Sieger APS - build graph.

   Lays out one order's dependency graph on a vertical DATE axis: the
   finished machine at the top on its delivery date, every operation that
   feeds it below, and the earliest manufacturing activity at the bottom.

   THE TIME AXIS IS BANDED, NOT LINEAR-CONTINUOUS
   ----------------------------------------------
   A straight pixels-per-day scale is unusable on this plan: a ten-step
   routing routinely has seven steps stamped at the same minute, so they
   would land on top of each other, while a six-week wait for material
   would open kilometres of blank page.

   So each DISTINCT timestamp gets one band. Bands are ordered latest at
   the top, and the gap between two bands is proportional to the real time
   between them but clamped, so a long wait still reads as a long wait
   without dominating the page. Inside a band, operations that depend on
   each other stack into sub-rows - they genuinely share a timestamp, so
   the band's date label stays true for every row in it - and operations
   that do NOT depend on each other sit side by side, which is exactly how
   parallel production should read.

   Every position therefore comes from a real planned date. Nothing is
   spread out for looks.

   LAYOUT IS TWO-PASS
   ------------------
   Cards vary in height with their content, so pass one renders them and
   measures, and pass two sizes each row to its tallest card. That is why
   nothing can overlap regardless of how much text a part description has.
   ------------------------------------------------------------------ */

(function () {
  "use strict";

  var root = document.querySelector(".sg-bg");
  if (!root) return;

  // -- geometry ---------------------------------------------------------
  var CARD_W = 208; // must match --sg-bg-card
  var H_GAP = 26; // horizontal breathing room between siblings
  var ROW_PAD = 22; // vertical space under a card before the next sub-row
  var BAND_MIN = 38; // shortest gap between two bands
  var BAND_MAX = 170; // longest, however big the real wait is
  var PX_PER_DAY = 30;
  var PAD = 46;

  // Low enough that "fit" genuinely fits a graph several thousand pixels
  // tall. Below ~0.4 the cards stop trying to show text and become a
  // structure map - see .z-tiny in the stylesheet.
  var ZOOM_MIN = 0.12,
    ZOOM_MAX = 1.8;

  // -- elements ---------------------------------------------------------
  var canvas = document.getElementById("sg-bg-canvas");
  var world = document.getElementById("sg-bg-world");
  var wires = document.getElementById("sg-bg-wires");
  var railIn = document.getElementById("sg-bg-rail-in");
  var msg = document.getElementById("sg-bg-msg");
  var panel = document.getElementById("sg-bg-panel");
  var panelBd = document.getElementById("sg-bg-panel-bd");
  var panelTitle = document.getElementById("sg-bg-panel-title");
  var panelKind = document.getElementById("sg-bg-panel-kind");
  var zoomLabel = document.getElementById("sg-bg-zoom");
  var findBox = document.getElementById("sg-bg-q");
  var hits = document.getElementById("sg-bg-hits");

  var sizer = document.createElement("div");
  sizer.style.position = "relative";
  sizer.style.pointerEvents = "none";
  canvas.appendChild(sizer);
  world.style.position = "absolute";
  world.style.top = "0";
  world.style.left = "0";

  // -- state ------------------------------------------------------------
  var MODE = root.dataset.mode || "order";
  // The ship graph opens in STRUCTURE view - the shipment as the hero
  // node, then assembly levels beneath it, which is how a planner thinks
  // about a build. The honest date axis is one click away.
  var VIEW = MODE === "ship" ? "structure" : "time";
  var cpChain = [];       // the delay chain, root-outward, for the walkthrough
  var didEnter = false;   // entrance animation plays once
  var DATA = null;
  var byRef = {}; // reference -> node
  var runById = {}; // MO reference -> run
  var stepRun = {}; // step reference -> MO reference
  var collapsed = {}; // MO reference -> true when folded to one card
  var L = null; // the current layout
  var els = {}; // node id -> element
  var wireEl = {}; // "up|down" edge key -> its <path>, rebuilt by drawWires()
  var litEls = []; // cards currently opted out of dimming (is-keep)
  var hotOverlays = []; // chain-highlight wires (SVG class or overlay path)
  var cpOverlays = []; // critical-chain wires, persist until the next render
  var machineEls = {}; // machine -> its card elements, reset on each render
  var flaggedEls = []; // cards currently flagged by a blocker-card hover
  var wcan = null; // bitmap layer for the static wires of big graphs
  var zoom = 1;
  var selected = null;
  var detailCache = {};

  // -- canvas renderer (big graphs only) --------------------------------
  // On graphs over HOVER_MAX runs the whole picture can be drawn to ONE
  // canvas sized to the viewport, instead of thousands of DOM cards. Pan
  // and zoom become a view transform; only the nodes on screen are drawn.
  // Opt-in, DOM stays the default - see renderMode().
  var GFX = null;        // the <canvas>
  var gctx = null;       // its 2d context
  var gfxOn = false;     // is the CURRENT render drawn to canvas?
  var gView = { ox: 0, oy: 0, scale: 1 }; // world->screen: sx = wx*scale+ox
  var gDirty = false, gRaf = 0;
  var gHover = null;     // hovered node id in canvas mode
  var gIndex = null;     // { bands:[{top,bot,ids}], hotspots:[{x,y,w,h,kind,id}] }
  var gLit = null;       // chain-highlight {keep,wire} or null
  var gCrit = null;      // critical chain { nodes:{}, edges:{} }
  var gFlag = null;      // flagged machine name or null
  var gFocus = null;     // "follow the delay" focused id
  var gColors = null;    // theme colours, re-probed each render
  var gAnim = false;     // keep the rAF loop alive for cp crawl / pulse
  var DPR = Math.min(2, window.devicePixelRatio || 1);

  // Above this many cards, hover-dimming is off: restyling thousands of
  // nodes per mouse crossing is what made the big shipment graphs lag,
  // and at that density the dim carries no information anyway. Click
  // still highlights.
  var HOVER_MAX = 900;

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

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
    return DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()];
  }

  function timeLabel(t) {
    var d = new Date(t);
    var h = d.getHours(), m = d.getMinutes();
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  /* The step code a routing step carries in its name, minus the routing
     prefix and the sequence number: RT-M55...-40-HOBB -> HOBB. */
  function opCode(n) {
    var op = n.operation || "";
    if (!op) return "";
    var run = n.run && runById[n.run] ? runById[n.run].operation : "";
    var tail = run && op.indexOf(run + "-") === 0 ? op.slice(run.length + 1) : op;
    var bits = tail.split("-");
    if (bits.length > 1 && /^\d+$/.test(bits[0])) return bits.slice(1).join("-");
    return tail;
  }

  // ------------------------------------------------ the effective graph
  //
  // A collapsed production run folds all of its steps into one card, so
  // every edge that touched a step has to be re-pointed at the run. eff()
  // is that mapping, and it is the only place collapse state is read.

  function eff(ref) {
    var run = stepRun[ref];
    if (run) return collapsed[run] ? run : ref;
    if (runById[ref]) {
      if (collapsed[ref]) return ref;
      // Expanded: the manufacturing order itself is drawn as a frame, not
      // a card. Its component supply is consumed at the start of the
      // routing, so anything feeding the order feeds its first step.
      var steps = runById[ref].steps;
      return steps.length ? steps[0] : ref;
    }
    return ref;
  }

  /* What a given effective id should render as. */
  function view(id) {
    if (id === "__order") {
      var o = DATA.order;
      return {
        id: id, kind: "order", tone: o.tone,
        eyebrow: "finished machine · " + o.name,
        title: o.item || o.name,
        sub: o.description,
        date: ms(o.delivery) || ms(o.due),
        meta: [
          ["qty", o.quantity],
          ["due", o.due ? dayLabel(ms(o.due)) : "—"],
          ["delivery", o.delivery ? dayLabel(ms(o.delivery)) : "—"]
        ],
        tags: [
          [o.status, ""],
          o.delay_days > 0.05 ? [o.delay_days + " d late", "is-late"] : ["on time", "is-ok"],
          o.customer ? [o.customer, ""] : null
        ]
      };
    }

    var r = runById[id];
    if (r && collapsed[id]) {
      return {
        id: id, kind: "run", tone: r.tone,
        eyebrow: "production run · " + r.id,
        title: r.item || r.id,
        sub: r.description,
        date: ms(r.end) || ms(r.start),
        run: r,
        meta: [
          ["qty", r.qty],
          ["from", r.start ? dayLabel(ms(r.start)) : "—"],
          ["to", r.end ? dayLabel(ms(r.end)) : "—"]
        ],
        tags: [
          [r.steps.length + " step" + (r.steps.length === 1 ? "" : "s"), "is-key"],
          r.shared ? ["shared ×" + r.orders, "is-share"] : null,
          r.delay_days > 0.05 ? [r.delay_days + " d late", "is-late"] : null
        ],
        collapsedRun: true,
        count: r.steps.length
      };
    }

    var n = byRef[id];
    if (!n) return null;

    if (n.kind === "delivery") {
      return {
        id: id, kind: "delivery", tone: n.tone,
        eyebrow: "delivery · " + n.ref,
        title: "Ship " + (n.item || ""),
        sub: n.description,
        date: ms(n.end) || ms(n.start),
        meta: [["qty", n.qty], ["on", n.end ? dayLabel(ms(n.end)) : "—"]],
        tags: [[n.status, ""]]
      };
    }

    if (n.kind === "run") {
      // A production run as its own card - the shipment graph's node.
      return {
        id: id, kind: "run", tone: n.tone,
        eyebrow: "production run · " + n.ref,
        title: n.item || n.ref, sub: n.description,
        date: ms(n.end) || ms(n.start),
        meta: [
          ["qty", n.qty],
          ["on", n.machines && n.machines.length
                 ? (n.machines.length > 2
                    ? n.machines.slice(0, 2).join(", ") + " +" +
                      (n.machines.length - 2)
                    : n.machines.join(", "))
                 : ""],
          ["to", n.end ? dayLabel(ms(n.end)) : "—"]
        ],
        tags: [
          n.steps_n ? [n.steps_n + " step" + (n.steps_n === 1 ? "" : "s"), ""] : null,
          n.shared ? ["shared ×" + n.npeg, "is-share"] : null,
          n.delay_days > 0.05 ? [n.delay_days + " d late", "is-late"] : null
        ]
      };
    }

    var code = opCode(n);
    return {
      id: id, kind: n.kind, tone: n.tone,
      eyebrow: (n.seq !== null && n.seq !== undefined ? "op " + n.seq : n.type) +
        (n.item ? " · " + n.item : ""),
      title: code || n.operation || n.ref,
      sub: n.operation_description || n.description,
      date: ms(n.end) || ms(n.start),
      seq: n.seq,
      meta: [
        ["machine", n.machines.length ? n.machines.join(", ") : "—"],
        ["qty", n.qty],
        [n.hours ? "hours" : "", n.hours ? n.hours : ""]
      ],
      tags: [
        n.start ? [timeLabel(ms(n.start)), ""] : null,
        n.shared ? ["shared ×" + n.npeg, "is-share"] : null,
        n.delay_days > 0.05 ? [n.delay_days + " d late", "is-late"] : null
      ]
    };
  }

  // ------------------------------------------------------------- layout

  function layout() {
    // 1. which ids are on screen, and the edges between them
    var idset = {};
    idset["__order"] = true;
    DATA.nodes.forEach(function (n) { idset[eff(n.id)] = true; });

    var seen = {};
    var E = [];
    function edge(u, d) {
      if (u === d || !idset[u] || !idset[d]) return;
      var k = u + "|" + d;
      if (seen[k]) return;
      seen[k] = true;
      E.push([u, d]);
    }
    DATA.edges.forEach(function (e) { edge(eff(e[0]), eff(e[1])); });
    DATA.seeds.forEach(function (s) { edge(eff(s), "__order"); });

    var ids = Object.keys(idset);
    var V = {};
    ids.forEach(function (id) {
      var v = view(id);
      if (v) V[id] = v;
    });
    ids = ids.filter(function (id) { return V[id]; });

    var succ = {}, pred = {};
    ids.forEach(function (id) { succ[id] = []; pred[id] = []; });
    E.forEach(function (e) {
      if (!succ[e[0]] || !pred[e[1]]) return;
      succ[e[0]].push(e[1]);
      pred[e[1]].push(e[0]);
    });

    // 2. bands. TIME view: one band per distinct timestamp, latest at
    // the top - the honest axis. STRUCTURE view: one band per assembly
    // level - the shipment, what feeds it directly, what feeds that, and
    // so on down to the first cut. Same layout machinery either way.
    var times = [];
    var band = {};
    if (VIEW === "structure") {
      // Level = longest chain from this node up to the shipment, so a
      // node always sits below everything it feeds.
      var lvl = { "__order": 0 };
      var pending = ids.filter(function (id) { return id !== "__order"; });
      var guard = ids.length + 5;
      while (pending.length && guard-- > 0) {
        var next = [];
        pending.forEach(function (id) {
          var ss = succ[id];
          if (!ss.length) { lvl[id] = null; return; }   // orphan: later
          var m = -1, ok = true;
          ss.forEach(function (t) {
            if (lvl[t] === undefined) ok = false;
            else if (lvl[t] !== null && lvl[t] > m) m = lvl[t];
          });
          if (ok) lvl[id] = m + 1; else next.push(id);
        });
        if (next.length === pending.length) {           // cycle: cut it
          lvl[next[0]] = 1;
          next.shift();
        }
        pending = next;
      }
      var maxL = 0;
      ids.forEach(function (id) {
        if (lvl[id] !== null && lvl[id] !== undefined && lvl[id] > maxL) maxL = lvl[id];
      });
      ids.forEach(function (id) {
        var v = lvl[id];
        band[id] = (v === null || v === undefined) ? maxL : v;
      });
    } else {
      var stamps = {};
      ids.forEach(function (id) {
        var t = V[id].date;
        if (t !== null && t !== undefined) stamps[t] = true;
      });
      times = Object.keys(stamps).map(Number).sort(function (a, b) { return b - a; });
      if (!times.length) times = [0];
      var bandOf = {};
      times.forEach(function (t, i) { bandOf[t] = i; });
      ids.forEach(function (id) {
        var t = V[id].date;
        band[id] = t === null || t === undefined ? times.length - 1 : bandOf[t];
      });
    }

    // An upstream node must never sit in a band ABOVE its successor - that
    // would draw an edge running backwards in time. Equality is fine and
    // is the normal case: several steps of one routing genuinely carry the
    // same timestamp (their routing duration is zero), and the sub-rows
    // below separate them without lying about the date.
    var order = topo(ids, succ, pred);
    for (var i = 0; i < order.length; i++) {
      var id = order[i];
      succ[id].forEach(function (s) {
        if (band[id] < band[s]) band[id] = band[s];
      });
    }

    // 3. sub-rows. TIME view: nodes that depend on each other but share
    //    a timestamp stack. STRUCTURE view: each level wraps into a
    //    shelf of rows, late work first, so 1,500 runs on one level stay
    //    on screen instead of forming a 370,000px row.
    var sub = {};
    var shelfPos = null;
    if (VIEW === "structure") {
      shelfPos = {};
      var byBand = {};
      ids.forEach(function (id) {
        (byBand[band[id]] = byBand[band[id]] || []).push(id);
      });
      var toneRank = { crit: 0, late: 1, ok: 2 };
      Object.keys(byBand).forEach(function (b) {
        var list = byBand[b];
        list.sort(function (a, bb) {
          var ta = toneRank[V[a].tone] !== undefined ? toneRank[V[a].tone] : 2;
          var tb = toneRank[V[bb].tone] !== undefined ? toneRank[V[bb].tone] : 2;
          if (ta !== tb) return ta - tb;
          return String(V[a].title).localeCompare(String(V[bb].title));
        });
        var cols = Math.max(4, Math.ceil(Math.sqrt(list.length * 2.4)));
        list.forEach(function (id, i) {
          sub[id] = Math.floor(i / cols);
          shelfPos[id] = i % cols;
        });
      });
    } else {
      order.forEach(function (id) {
        var best = 0;
        succ[id].forEach(function (s) {
          if (band[s] === band[id]) best = Math.max(best, sub[s] + 1);
        });
        sub[id] = best;
      });
    }

    // 4. x - leaf packing down a primary-parent tree, so a run's steps
    //    stack in one column and independent branches spread sideways
    var parent = {}, kids = {};
    ids.forEach(function (id) { kids[id] = []; });
    ids.forEach(function (id) {
      if (id === "__order") return;
      var best = null;
      succ[id].forEach(function (s) {
        if (best === null || band[s] < band[best] ||
            (band[s] === band[best] && sub[s] < sub[best])) best = s;
      });
      if (best === null) best = "__order";
      parent[id] = best;
      kids[best].push(id);
    });

    if (shelfPos) {
      var px2 = {};
      ids.forEach(function (id) {
        px2[id] = PAD + shelfPos[id] * (CARD_W + H_GAP);
      });
      // centre the hero over the widest shelf
      var wMax = 0;
      ids.forEach(function (id) {
        if (px2[id] + CARD_W > wMax) wMax = px2[id] + CARD_W;
      });
      px2["__order"] = Math.max(PAD, (wMax + PAD - CARD_W * 1.6) / 2);
      return {
        ids: ids, V: V, E: E, succ: succ, pred: pred,
        band: band, sub: sub, px: px2, rows: {},
        times: times, kids: {}, parent: {}
      };
    }

    var slot = 0, xs = {}, guard = {};
    (function place(id) {
      if (guard[id]) return xs[id] || 0;
      guard[id] = true;
      var ch = kids[id] || [];
      if (!ch.length) { xs[id] = slot++; return xs[id]; }
      ch.sort(function (a, b) {
        if (band[a] !== band[b]) return band[a] - band[b];
        var sa = V[a].seq, sb = V[b].seq;
        if (sa !== undefined && sb !== undefined && sa !== null && sb !== null && sa !== sb) {
          return sb - sa;
        }
        return String(a).localeCompare(String(b));
      });
      var got = ch.map(place);
      xs[id] = (Math.min.apply(null, got) + Math.max.apply(null, got)) / 2;
      return xs[id];
    })("__order");
    ids.forEach(function (id) { if (xs[id] === undefined) xs[id] = slot++; });

    var px = {};
    ids.forEach(function (id) { px[id] = xs[id] * (CARD_W + H_GAP); });

    // Rows must not overlap horizontally. Sweep each row left to right and
    // push anything that would collide.
    var rows = {};
    ids.forEach(function (id) {
      var k = band[id] + ":" + sub[id];
      (rows[k] = rows[k] || []).push(id);
    });
    Object.keys(rows).forEach(function (k) {
      var row = rows[k].sort(function (a, b) { return px[a] - px[b]; });
      for (var j = 1; j < row.length; j++) {
        var need = px[row[j - 1]] + CARD_W + H_GAP;
        if (px[row[j]] < need) px[row[j]] = need;
      }
    });

    var minX = Math.min.apply(null, ids.map(function (id) { return px[id]; }));
    ids.forEach(function (id) { px[id] = px[id] - minX + PAD; });

    return {
      ids: ids, V: V, E: E, succ: succ, pred: pred,
      band: band, sub: sub, px: px, rows: rows,
      times: times, kids: kids, parent: parent
    };
  }

  /* Topological order, downstream first. Falls back to band order if the
     data ever contains a cycle, so layout can never hang. */
  function topo(ids, succ, pred) {
    var indeg = {};
    ids.forEach(function (id) { indeg[id] = 0; });
    ids.forEach(function (id) {
      succ[id].forEach(function (s) { if (indeg[s] !== undefined) indeg[s]++; });
    });
    var q = ids.filter(function (id) { return !indeg[id]; });
    var out = [];
    while (q.length) {
      var id = q.shift();
      out.push(id);
      succ[id].forEach(function (s) {
        if (indeg[s] === undefined) return;
        if (--indeg[s] === 0) q.push(s);
      });
    }
    if (out.length !== ids.length) {
      var left = ids.filter(function (id) { return out.indexOf(id) < 0; });
      out = out.concat(left);
    }
    // Downstream first: reverse, because edges run upstream -> downstream.
    return out.reverse();
  }

  // ------------------------------------------------------------ drawing

  function cardHTML(v) {
    var h = '<div class="sg-bg-n-top">' +
      "<span>" + esc(v.eyebrow) + "</span>" +
      (v.seq !== undefined && v.seq !== null ? '<span class="sg-bg-n-seq">' + esc(v.seq) + "</span>" : "") +
      "</div>" +
      '<div class="sg-bg-n-title">' + esc(v.title) + "</div>";

    if (v.sub) h += '<div class="sg-bg-n-sub" title="' + esc(v.sub) + '">' + esc(v.sub) + "</div>";

    var meta = (v.meta || []).filter(function (m) { return m && m[0] && m[1] !== "" && m[1] !== null && m[1] !== undefined; });
    if (meta.length) {
      h += '<div class="sg-bg-n-meta">' + meta.map(function (m) {
        return "<span><i>" + esc(m[0]) + "</i> " + esc(m[1]) + "</span>";
      }).join("") + "</div>";
    }

    var tags = (v.tags || []).filter(Boolean).filter(function (t) { return t[0]; });
    if (tags.length) {
      h += '<div class="sg-bg-n-tags">' + tags.map(function (t) {
        return '<span class="sg-bg-pill ' + esc(t[1]) + '">' + esc(t[0]) + "</span>";
      }).join("") + "</div>";
    }

    if (v.collapsedRun) {
      h += '<span class="sg-bg-count">' + v.count + "</span>" +
        '<button class="sg-bg-tog" type="button" data-toggle="' + esc(v.id) +
        '" title="show the operations" aria-label="expand">+</button>';
    }
    return h;
  }

  // =====================================================================
  // CANVAS RENDERER (big graphs only)
  //
  // The same layout() output, drawn to one viewport-sized <canvas> under a
  // view transform, instead of thousands of absolutely-positioned DOM
  // cards. Opt-in; the DOM path above stays the default and the fallback.
  // =====================================================================

  function renderMode() {
    var p = null;
    try { p = new URLSearchParams(location.search).get("render"); } catch (e) {}
    if (p === "canvas" || p === "dom") {
      try { localStorage.setItem("sg-bg-render", p); } catch (e) {}
      return p;
    }
    var s = null;
    try { s = localStorage.getItem("sg-bg-render"); } catch (e) {}
    return s === "canvas" ? "canvas" : "dom";
  }

  /* Read a CSS custom property off the root element. frePPLe swaps the
     whole theme bundle with no event, so these are re-read every render. */
  function cssVar(name, fallback) {
    var v = getComputedStyle(root).getPropertyValue(name);
    v = (v || "").trim();
    return v || fallback;
  }
  function withAlpha(col, a) {
    // Accepts #rrggbb or rgb(...); returns an rgba() string.
    if (!col) return "rgba(128,138,145," + a + ")";
    if (col[0] === "#") {
      var h = col.slice(1);
      if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
      var n = parseInt(h, 16);
      return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
    }
    var m = col.match(/rgba?\(([^)]+)\)/);
    if (m) {
      var p = m[1].split(",");
      return "rgba(" + (+p[0]) + "," + (+p[1]) + "," + (+p[2]) + "," + a + ")";
    }
    return col;
  }

  function themeColors() {
    var sans = cssVar("--sg-sans", "system-ui, sans-serif");
    var mono = cssVar("--sg-mono", "ui-monospace, monospace");
    return {
      sans: sans, mono: mono,
      surface: cssVar("--sg-surface", "#fff"),
      surface2: cssVar("--sg-surface-2", "#edeaea"),
      rule: cssVar("--sg-rule", "#cbc5c3"),
      ink: cssVar("--sg-ink", "#201c1b"),
      ink2: cssVar("--sg-ink-2", "#6a6360"),
      accent: cssVar("--sg-accent", "#c0392b"),
      accentIn: cssVar("--sg-accent-in", "#e8a33d"),
      ok: cssVar("--sg-ok", "#2fa36a"),
      warn: cssVar("--sg-warn", "#d08a2c"),
      bad: cssVar("--sg-bad", "#d9534f"),
      // z-tiny solid blocks (mirror .z-tiny in the stylesheet)
      okBlk: "rgba(47,163,106,.6)",
      warnBlk: "rgba(208,138,44,.78)",
      critBlk: "rgba(217,83,79,.82)",
      dimBlk: "rgba(128,138,145,.55)"
    };
  }

  function gfxFont(px, bold, mono) {
    return (bold ? "700 " : "400 ") + px + "px " +
      (mono ? gColors.mono : gColors.sans);
  }

  function roundRect(x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    gctx.beginPath();
    gctx.moveTo(x + r, y);
    gctx.arcTo(x + w, y, x + w, y + h, r);
    gctx.arcTo(x + w, y + h, x, y + h, r);
    gctx.arcTo(x, y + h, x, y, r);
    gctx.arcTo(x, y, x + w, y, r);
    gctx.closePath();
  }

  function fitText(s, maxw) {
    s = String(s || "");
    if (gctx.measureText(s).width <= maxw) return s;
    while (s.length > 1 && gctx.measureText(s + "…").width > maxw) {
      s = s.slice(0, -1);
    }
    return s + "…";
  }

  function gfxEnsure() {
    if (GFX) return true;
    try {
      GFX = document.createElement("canvas");
      GFX.className = "sg-bg-gfx";
      GFX.setAttribute("role", "img");
      canvas.appendChild(GFX);
      gctx = GFX.getContext("2d");
      if (!gctx) throw new Error("no 2d context");
      // Pointer + wheel handlers live on `canvas` already; the gfx canvas
      // is transparent to them (pointer-events none) so hit-testing stays
      // in one place with the pan/zoom code.
      return true;
    } catch (e) {
      GFX = null; gctx = null;
      return false;
    }
  }

  function gfxResize() {
    if (!GFX) return;
    var w = canvas.clientWidth, h = canvas.clientHeight;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    GFX.width = Math.max(1, Math.round(w * DPR));
    GFX.height = Math.max(1, Math.round(h * DPR));
    GFX.style.width = w + "px";
    GFX.style.height = h + "px";
    scheduleDraw();
  }

  function visibleRect() {
    // world coords of the on-screen area, plus a small margin
    var pad = 40;
    return {
      x0: (-gView.ox) / gView.scale - pad,
      y0: (-gView.oy) / gView.scale - pad,
      x1: (canvas.clientWidth - gView.ox) / gView.scale + pad,
      y1: (canvas.clientHeight - gView.oy) / gView.scale + pad
    };
  }

  function scheduleDraw() {
    if (gDirty) return;
    gDirty = true;
    gRaf = requestAnimationFrame(drawFrame);
  }

  function lodFor(scale) { return scale < 0.4 ? 0 : scale < 0.62 ? 1 : 2; }

  function drawFrame() {
    gDirty = false; gRaf = 0;
    if (!gctx || !L) return;
    var c = gColors;
    gctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    gctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    gctx.setTransform(DPR * gView.scale, 0, 0, DPR * gView.scale,
      DPR * gView.ox, DPR * gView.oy);
    var vr = visibleRect();
    var lod = lodFor(gView.scale);
    gfxBands(vr);
    gfxWires(vr);
    gfxFrames(vr, lod);
    gfxNodes(vr, lod);
    gfxOverlays(vr, lod);
    // Keep animating while the critical chain / tour needs a moving dash
    // or pulse; otherwise let the loop idle.
    if (gAnim) scheduleDraw();
  }

  function gfxBands(vr) {
    if (!L.bandTop) return;
    var c = gColors;
    gctx.lineWidth = 1 / gView.scale;
    for (var b = 0; b < L.nbands; b++) {
      var y = L.bandTop[b];
      if (y === undefined) continue;
      if (y < vr.y0 - 20 || y > vr.y1 + 20) continue;
      gctx.strokeStyle = withAlpha(c.rule, b === 0 ? 0.9 : 0.4);
      gctx.beginPath();
      gctx.moveTo(vr.x0, y - 12);
      gctx.lineTo(vr.x1, y - 12);
      gctx.stroke();
    }
  }

  function gfxWires() {
    // The static wires are rastered once into wcan (paintWireCanvas); one
    // blit under the view transform, already themed. Dynamic chain/cp
    // wires are drawn live in gfxOverlays.
    if (wcan && wcan.width) {
      gctx.save();
      if (gLit) gctx.globalAlpha = 0.16;
      gctx.drawImage(wcan, 0, 0, L.W, L.H);
      gctx.restore();
    }
  }

  function gfxFrames(vr, lod) {
    if (lod === 0 || !gIndex || !gIndex.frames) return;
    var c = gColors;
    gIndex.frames.forEach(function (f) {
      if (f.x + f.w < vr.x0 || f.x > vr.x1 || f.y + f.h < vr.y0 || f.y > vr.y1) return;
      gctx.fillStyle = withAlpha(c.accentIn, f.shared ? 0.07 : 0.045);
      gctx.strokeStyle = withAlpha(f.shared ? c.accentIn : c.rule, 0.55);
      gctx.lineWidth = 1 / gView.scale;
      roundRect(f.x, f.y, f.w, f.h, 8);
      gctx.fill();
      gctx.stroke();
    });
  }

  function gfxNodes(vr, lod) {
    if (!gIndex) return;
    var c = gColors;
    gctx.textBaseline = "top";
    gctx.textAlign = "left";
    // iterate only bands intersecting the viewport
    for (var bi = 0; bi < gIndex.bands.length; bi++) {
      var band = gIndex.bands[bi];
      if (band.bot < vr.y0 || band.top > vr.y1) continue;
      for (var i = 0; i < band.ids.length; i++) {
        var id = band.ids[i];
        var x = L.px[id], y = L.top[id], w = L.w[id], h = L.h[id];
        if (x + w < vr.x0 || x > vr.x1) continue;
        gfxNode(L.V[id], id, x, y, w, h, lod);
      }
    }
  }

  function gfxNode(v, id, x, y, w, h, lod) {
    var c = gColors;
    var tone = v.tone || "ok";
    var dim = gLit && !gLit.keep[id];
    gctx.globalAlpha = dim ? 0.22 : 1;

    if (lod === 0) {
      // Zoomed-out structure map: cheap sharp blocks, no rounded paths.
      gctx.fillStyle = v.kind === "order" ? c.accent
        : tone === "crit" ? c.critBlk
        : tone === "late" ? c.warnBlk
        : (v.kind === "operation" || v.kind === "run") ? c.okBlk : c.dimBlk;
      gctx.fillRect(x, y, w, h);
      if ((v.run && v.run.shared) || (byRef[id] && byRef[id].shared)) {
        gctx.lineWidth = 3 / gView.scale;
        gctx.strokeStyle = c.accentIn;
        gctx.strokeRect(x, y, w, h);
      }
      gctx.globalAlpha = 1;
      return;
    }

    // card body
    gctx.fillStyle = v.kind === "order" ? withAlpha(c.accent, 0.12) : c.surface2;
    roundRect(x, y, w, h, 4);
    gctx.fill();
    gctx.lineWidth = 1 / gView.scale;
    gctx.strokeStyle = c.rule;
    gctx.stroke();
    // tone left stripe
    gctx.fillStyle = tone === "crit" ? c.bad : tone === "late" ? c.warn : c.ok;
    gctx.fillRect(x, y + 1, 3, h - 2);

    gctx.save();
    roundRect(x, y, w, h, 4);
    gctx.clip();
    var px = x + 9, py = y + 7, tw = w - 18;

    // eyebrow + seq
    gctx.font = gfxFont(9, true, true);
    gctx.fillStyle = c.ink2;
    var eb = fitText(v.eyebrow, v.seq != null ? tw - 16 : tw);
    gctx.fillText(eb, px, py);
    if (v.seq != null && v.seq !== undefined) {
      gctx.textAlign = "right";
      gctx.fillStyle = c.ink;
      gctx.fillText(String(v.seq), x + w - 9, py);
      gctx.textAlign = "left";
    }
    py += 14;

    // title - up to two lines
    gctx.font = gfxFont(12.5, true, true);
    gctx.fillStyle = c.ink;
    var words = String(v.title).split(/\s+/), line = "", lines = [], k;
    for (k = 0; k < words.length; k++) {
      var t = line ? line + " " + words[k] : words[k];
      if (gctx.measureText(t).width > tw && line) { lines.push(line); line = words[k]; }
      else line = t;
      if (lines.length >= 2) break;
    }
    if (line && lines.length < 2) lines.push(line);
    for (k = 0; k < lines.length; k++) {
      gctx.fillText(k === 1 ? fitText(lines[k], tw) : lines[k], px, py);
      py += 15;
    }

    if (lod === 2) {
      if (v.sub) {
        gctx.font = gfxFont(10.5, false, false);
        gctx.fillStyle = withAlpha(c.ink2, 0.85);
        gctx.fillText(fitText(v.sub, tw), px, py);
        py += 14;
      }
      // one compact meta line: "label value  label value"
      var meta = (v.meta || []).filter(function (m) {
        return m && m[0] && m[1] !== "" && m[1] !== null && m[1] !== undefined;
      });
      if (meta.length) {
        gctx.font = gfxFont(9.5, false, true);
        gctx.fillStyle = c.ink2;
        var ms = meta.map(function (m) { return m[0] + " " + m[1]; }).join("   ");
        gctx.fillText(fitText(ms, tw), px, py);
        py += 13;
      }
      // tags: late / shared as coloured text
      var tags = (v.tags || []).filter(Boolean).filter(function (t) { return t[0]; });
      if (tags.length) {
        gctx.font = gfxFont(9.5, true, true);
        var tx = px;
        for (k = 0; k < tags.length; k++) {
          var cls = tags[k][1];
          gctx.fillStyle = cls === "is-late" ? c.warn
            : cls === "is-share" ? c.accentIn
            : cls === "is-key" ? c.accent
            : cls === "is-ok" ? c.ok : c.ink2;
          var label = tags[k][0];
          var lw = gctx.measureText(label).width;
          if (tx + lw > x + w - 6) break;
          gctx.fillText(label, tx, py);
          tx += lw + 8;
        }
      }
    }
    gctx.restore();
    gctx.globalAlpha = 1;
  }

  function gfxOverlays(vr, lod) {
    var c = gColors;
    // chain-highlight hot wires + critical chain wires, drawn live
    function drawWire(u, d, col, width, dash) {
      var g = wireGeom(u, d);
      if (!g) return;
      gctx.strokeStyle = col;
      gctx.lineWidth = width / gView.scale;
      gctx.setLineDash(dash ? [6 / gView.scale, 5 / gView.scale] : []);
      gctx.beginPath();
      gctx.moveTo(g.pts[0], g.pts[1]);
      gctx.bezierCurveTo(g.pts[2], g.pts[3], g.pts[4], g.pts[5], g.pts[6], g.pts[7]);
      gctx.stroke();
      gctx.setLineDash([]);
    }
    if (gLit) {
      Object.keys(gLit.wire).forEach(function (kk) {
        var i = kk.indexOf("|");
        drawWire(kk.slice(0, i), kk.slice(i + 1), c.accent, 2.4, false);
      });
    }
    if (gCrit) {
      Object.keys(gCrit.edges).forEach(function (kk) {
        var i = kk.indexOf("|");
        drawWire(kk.slice(0, i), kk.slice(i + 1), c.accent, 2.6, true);
      });
    }
    // rings on selected / flagged / focused / critical nodes
    function ring(id, col, width, grow) {
      var x = L.px[id], y = L.top[id], w = L.w[id], h = L.h[id];
      if (x === undefined) return;
      gctx.strokeStyle = col;
      gctx.lineWidth = width / gView.scale;
      roundRect(x - grow, y - grow, w + grow * 2, h + grow * 2, 5);
      gctx.stroke();
    }
    if (gCrit) Object.keys(gCrit.nodes).forEach(function (id) { ring(id, withAlpha(c.accent, 0.8), 2, 1); });
    if (gFlag && gIndex && gIndex.machineIds[gFlag]) {
      gctx.globalAlpha = 1;
      // fade non-flagged by overpainting? simpler: ring the flagged ones
      gIndex.machineIds[gFlag].forEach(function (id) { ring(id, c.bad, 2.5, 2); });
    }
    if (selected) ring(selected, c.accent, 2.5, 1);
    if (gFocus) {
      var pulse = 2 + 2 * Math.abs(Math.sin(Date.now() / 300));
      ring(gFocus, c.accent, 2.5, pulse);
    }
    // Only the "follow the delay" pulse needs a live loop; everything else
    // is static, so the rAF idles the moment the tour ends.
    gAnim = !!gFocus;
  }

  // Spatial index + frames + machine lists, rebuilt each render.
  function buildIndex() {
    var bands = [];
    var byBand = {};
    L.ids.forEach(function (id) {
      var b = L.band[id];
      (byBand[b] = byBand[b] || []).push(id);
    });
    for (var b = 0; b < L.nbands; b++) {
      if (!byBand[b]) continue;
      var top = L.bandTop[b], bot = top + (L.bandH[b] || 0) + 40;
      bands.push({ top: top, bot: bot, ids: byBand[b] });
    }
    // frames for expanded runs
    var frames = [];
    (DATA.runs || []).forEach(function (r) {
      if (collapsed[r.id]) return;
      var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, any = false;
      r.steps.forEach(function (s) {
        if (L.px[s] === undefined) return;
        any = true;
        x0 = Math.min(x0, L.px[s]); x1 = Math.max(x1, L.px[s] + L.w[s]);
        y0 = Math.min(y0, L.top[s]); y1 = Math.max(y1, L.top[s] + L.h[s]);
      });
      if (!any) return;
      frames.push({ id: r.id, shared: r.shared, x: x0 - 11, y: y0 - 15, w: x1 - x0 + 22, h: y1 - y0 + 26 });
    });
    // machine -> ids
    var machineIds = {};
    L.ids.forEach(function (id) {
      var n = byRef[id];
      if (!n || !n.machines) return;
      n.machines.forEach(function (m) { (machineIds[m] = machineIds[m] || []).push(id); });
    });
    // collapse/expand hotspots
    var hotspots = [];
    frames.forEach(function (f) {
      // frame label / collapse button, top-left of the frame
      hotspots.push({ x: f.x, y: f.y - 2, w: 150, h: 20, kind: "frame", id: f.id });
    });
    gIndex = { bands: bands, frames: frames, machineIds: machineIds, hotspots: hotspots };
  }

  function hitTest(sx, sy) {
    if (!L) return null;
    var rect = canvas.getBoundingClientRect();
    var wx = (sx - rect.left - gView.ox) / gView.scale;
    var wy = (sy - rect.top - gView.oy) / gView.scale;
    // hotspots first
    for (var i = 0; i < gIndex.hotspots.length; i++) {
      var hp = gIndex.hotspots[i];
      if (wx >= hp.x && wx <= hp.x + hp.w && wy >= hp.y && wy <= hp.y + hp.h) {
        return { kind: hp.kind, id: hp.id };
      }
    }
    // node: find the band then scan it
    for (var bi = 0; bi < gIndex.bands.length; bi++) {
      var band = gIndex.bands[bi];
      if (wy < band.top - 20 || wy > band.bot) continue;
      for (var k = 0; k < band.ids.length; k++) {
        var id = band.ids[k];
        if (wx >= L.px[id] && wx <= L.px[id] + L.w[id] &&
            wy >= L.top[id] && wy <= L.top[id] + L.h[id]) {
          return { kind: "node", id: id };
        }
      }
    }
    return null;
  }

  // -- canvas viewport ops ---------------------------------------------
  function gfxAfterZoom() {
    zoom = gView.scale; // keep DOM-era readers (drawRail thinning) in sync
    if (zoomLabel) zoomLabel.textContent = Math.round(gView.scale * 100) + "%";
    gfxRail();
    scheduleDraw();
  }

  function gfxSetZoom(z, anchorX, anchorY) {
    var ax = anchorX === undefined ? canvas.clientWidth / 2 : anchorX;
    var ay = anchorY === undefined ? canvas.clientHeight / 2 : anchorY;
    var wx = (ax - gView.ox) / gView.scale, wy = (ay - gView.oy) / gView.scale;
    gView.scale = clamp(z, ZOOM_MIN, ZOOM_MAX);
    gView.ox = ax - wx * gView.scale;
    gView.oy = ay - wy * gView.scale;
    gfxAfterZoom();
  }

  function gfxFit() {
    var z = Math.min(canvas.clientWidth / (L.W + 20), canvas.clientHeight / (L.H + 20));
    gView.scale = clamp(z, ZOOM_MIN, ZOOM_MAX);
    gView.ox = Math.max(0, (canvas.clientWidth - L.W * gView.scale) / 2);
    gView.oy = 20;
    gfxAfterZoom();
  }

  function gfxReset() {
    gView.scale = 1;
    gView.ox = Math.max(0, (canvas.clientWidth - L.W) / 2);
    gView.oy = 20;
    gfxAfterZoom();
  }

  function gfxCentreOn(id) {
    if (L.top[id] === undefined) return;
    gView.ox = canvas.clientWidth / 2 - (L.px[id] + L.w[id] / 2) * gView.scale;
    gView.oy = canvas.clientHeight / 2 - (L.top[id] + L.h[id] / 2) * gView.scale;
    gfxAfterZoom();
  }

  /* Rail markers built once per zoom level at y = bandTop*scale (no pan
     offset); panning then only shifts the container via a transform, so a
     drag never rebuilds this DOM. gfxRailShift() does the per-frame move. */
  function gfxRailShift() {
    railIn.style.transform = "translateY(" + gView.oy + "px)";
  }

  function gfxRail() {
    if (!L.bandTop) return;
    gfxRailShift();
    var out = [], last = -1e9;
    for (var b = 0; b < L.nbands; b++) {
      if (L.bandTop[b] === undefined) continue;
      var sy = L.bandTop[b] * gView.scale;
      if (VIEW === "structure") {
        var lbl = b === 0 ? "SHIPMENT" : b === 1 ? "final assembly" : "level " + b;
        out.push('<div class="sg-bg-mark' + (b === 0 ? " is-edge" : "") +
          '" style="top:' + sy + 'px"><b>' + esc(lbl) + "</b><span>" +
          (b === 0 ? "what we deliver" : b === 1 ? "feeds the shipment" : "feeds level " + (b - 1)) +
          "</span></div>");
        continue;
      }
      var t = L.times[b];
      if (t === undefined) continue;
      if (sy - last < 34 && b !== 0 && b !== L.nbands - 1) continue;
      last = sy;
      var rows = (L.maxSub[b] || 0) + 1;
      out.push('<div class="sg-bg-mark' + (b === 0 || b === L.nbands - 1 ? " is-edge" : "") +
        '" style="top:' + sy + 'px"><b>' + esc(dayLabel(t)) + "</b><span>" +
        esc(timeLabel(t)) + " · " + new Date(t).getFullYear() +
        (rows > 1 ? " · " + rows + " steps" : "") + "</span></div>");
    }
    railIn.style.marginTop = "0px";
    railIn.innerHTML = out.join("");
  }

  /* The canvas side of a full render: layout is already measured into L.
     Strip the measurement DOM, set up the canvas, draw. */
  function gfxRender() {
    // Paint the static-wire bitmap and probe colours while the stylesheet
    // still resolves normally (before the world is hidden).
    if (!wcan) { wcan = document.createElement("canvas"); }
    paintWireCanvas();
    buildIndex();
    computeCriticalCanvas();
    // remove the cards we built only to measure
    Array.prototype.slice.call(world.querySelectorAll(".sg-bg-node,.sg-bg-frame,.sg-bg-band"))
      .forEach(function (e) { e.remove(); });
    els = {};
    wires.innerHTML = "";
    canvas.classList.add("is-gfx");
    if (!gfxEnsure()) { throw new Error("canvas unavailable"); }
    GFX.style.display = "block";
    GFX.setAttribute("aria-label",
      "Dependency graph, " + L.ids.length + " production runs, drawn to canvas. " +
      "Use the search box and the detail panel to inspect it.");
    gfxResize();
    if (msg) msg.hidden = true;
  }

  function computeCriticalCanvas() {
    var r = computeCritical();
    gCrit = r;
    cpChain = r.chain;
  }

  function render() {
    L = layout();

    // -- pass one: cards, so they can be measured -----------------------
    Array.prototype.slice.call(world.querySelectorAll(".sg-bg-node,.sg-bg-frame,.sg-bg-band"))
      .forEach(function (e) { e.remove(); });
    els = {};

    var frag = document.createDocumentFragment();
    L.ids.forEach(function (id) {
      var v = L.V[id];
      var el = document.createElement("div");
      el.className = "sg-bg-node k-" + v.kind + " t-" + (v.tone || "ok") +
        (v.run && v.run.shared ? " is-shared" : "") +
        (byRef[id] && byRef[id].shared ? " is-shared" : "");
      el.setAttribute("data-id", id);
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      el.innerHTML = cardHTML(v);
      el.style.left = L.px[id] + "px";
      el.style.top = "0px";
      frag.appendChild(el);
      els[id] = el;
    });
    world.appendChild(frag);

    // -- pass two: size every row to its tallest card -------------------
    // Widths are measured too, not assumed: the finished-machine card is
    // deliberately wider than the rest, and guessing CARD_W for it would
    // put the wire anchors off-centre and under-report the canvas width.
    // Measure at full detail, never in a zoomed-out simplification, so the
    // layout is identical at every zoom level and re-rendering while zoomed
    // out cannot shuffle the graph. applyZoom() puts the classes back.
    world.classList.remove("z-far", "z-tiny");
    var h = {}, w = {};
    L.ids.forEach(function (id) {
      h[id] = els[id].offsetHeight || 76;
      w[id] = els[id].offsetWidth || CARD_W;
    });

    var rowH = {};
    L.ids.forEach(function (id) {
      var k = L.band[id] + ":" + L.sub[id];
      rowH[k] = Math.max(rowH[k] || 0, h[id] + ROW_PAD);
    });

    var maxSub = {};
    L.ids.forEach(function (id) {
      maxSub[L.band[id]] = Math.max(maxSub[L.band[id]] || 0, L.sub[id]);
    });

    var bandTop = {}, bandH = {}, y = PAD;
    var nbands = 0;
    L.ids.forEach(function (id) { nbands = Math.max(nbands, L.band[id] + 1); });

    for (var b = 0; b < nbands; b++) {
      bandTop[b] = y;
      var total = 0;
      for (var s = 0; s <= (maxSub[b] || 0); s++) total += rowH[b + ":" + s] || 0;
      bandH[b] = total;
      y += total;
      // Gap to the next band: real wait in time view, constant breathing
      // room between assembly levels in structure view.
      if (b + 1 < nbands) {
        if (VIEW === "structure") {
          y += 86;
        } else {
          var t0 = L.times[b], t1 = L.times[b + 1];
          var days = t0 !== undefined && t1 !== undefined ? (t0 - t1) / 86400000 : 0;
          y += clamp(days * PX_PER_DAY, BAND_MIN, BAND_MAX);
        }
      }
    }

    var top = {};
    L.ids.forEach(function (id) {
      var t = bandTop[L.band[id]];
      for (var s = 0; s < L.sub[id]; s++) t += rowH[L.band[id] + ":" + s] || 0;
      top[id] = t;
      els[id].style.top = t + "px";
    });

    var W = PAD, H = y + PAD;
    L.ids.forEach(function (id) { W = Math.max(W, L.px[id] + w[id] + PAD); });

    L.top = top; L.h = h; L.w = w; L.bandTop = bandTop; L.bandH = bandH;
    L.W = W; L.H = H; L.nbands = nbands; L.maxSub = maxSub;

    world.style.width = W + "px";
    world.style.height = H + "px";
    // Big graphs drop the per-card hover styling - see .is-big in the CSS.
    world.classList.toggle("is-big", L.ids.length > HOVER_MAX);

    // Canvas renderer, for big graphs only and only when opted in. Falls
    // back to the DOM path on any failure so a demo never breaks.
    gfxOn = false;
    var group = document.getElementById("sg-bg-rendergroup");
    var big = L.ids.length > HOVER_MAX;
    if (group) group.hidden = !big;
    if (big && renderMode() === "canvas") {
      try {
        gColors = themeColors();
        gfxRender();
        gfxOn = true;
        var btn = root.querySelector('[data-act="renderer"]');
        if (btn) btn.classList.add("is-on");
        // first view: fit the width like the DOM boot did
        if (L.W > canvas.clientWidth) gfxFit(); else gfxReset();
        drawRailDispatch();
        return;
      } catch (e) {
        gfxOn = false;
        try { localStorage.setItem("sg-bg-render", "dom"); } catch (ee) {}
        canvas.classList.remove("is-gfx");
        if (GFX) GFX.style.display = "none";
      }
    }
    canvas.classList.remove("is-gfx");
    if (GFX) GFX.style.display = "none";
    var rbtn = root.querySelector('[data-act="renderer"]');
    if (rbtn) rbtn.classList.remove("is-on");

    drawBands();
    drawFrames();
    drawWires();
    drawRail();
    markCritical();
    applyZoom();
    if (msg) msg.hidden = true;

    // Entrance: the graph builds itself downwards, one level after the
    // next, once per page - never on re-layouts, which must be instant.
    if (!didEnter) {
      didEnter = true;
      L.ids.forEach(function (id) {
        els[id].style.setProperty("--sg-d",
          Math.min(L.band[id] * 90, 1400) + "ms");
      });
      world.classList.add("sg-enter");
      setTimeout(function () { world.classList.remove("sg-enter"); }, 2600);
    }
  }

  /* Rail: canvas-positioned when in gfx mode, DOM-positioned otherwise. */
  function drawRailDispatch() {
    if (gfxOn) gfxRail(); else { drawRail(); syncRail(); }
  }

  /* The horizontal rules that carry the date axis across the canvas. */
  function drawBands() {
    var frag = document.createDocumentFragment();
    for (var b = 0; b < L.nbands; b++) {
      if (L.bandTop[b] === undefined) continue;
      var d = document.createElement("div");
      d.className = "sg-bg-band" + (b === 0 ? " is-major" : "");
      d.style.top = (L.bandTop[b] - 12) + "px";
      d.style.width = L.W + "px";
      frag.appendChild(d);
    }
    world.insertBefore(frag, world.firstChild);
  }

  /* One translucent frame per expanded production run. This is what makes
     the graph read as a bill of materials rather than a flowchart: each
     frame is one part being made, the cards inside are how it is made. */
  function drawFrames() {
    var frag = document.createDocumentFragment();
    DATA.runs.forEach(function (r) {
      if (collapsed[r.id]) return;
      var members = r.steps.filter(function (s) { return els[s]; });
      if (members.length < 1) return;

      var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      members.forEach(function (s) {
        x0 = Math.min(x0, L.px[s]);
        x1 = Math.max(x1, L.px[s] + L.w[s]);
        y0 = Math.min(y0, L.top[s]);
        y1 = Math.max(y1, L.top[s] + L.h[s]);
      });

      var f = document.createElement("div");
      f.className = "sg-bg-frame" + (r.shared ? " is-shared" : "");
      f.style.left = (x0 - 11) + "px";
      f.style.top = (y0 - 15) + "px";
      f.style.width = (x1 - x0 + 22) + "px";
      f.style.height = (y1 - y0 + 26) + "px";
      f.innerHTML = '<span class="sg-bg-frame-lb" data-run="' + esc(r.id) + '">' +
        '<button class="sg-bg-btn" type="button" data-toggle="' + esc(r.id) +
          '" title="fold this run into one card" aria-label="collapse">&minus;</button>' +
        "<b>" + esc(r.item) + "</b> · " + esc(r.qty) +
        (r.shared ? ' <span class="sg-bg-pill is-share">shared ×' + r.orders + "</span>" : "") +
        "</span>";
      frag.appendChild(f);
    });
    world.insertBefore(frag, world.firstChild);
  }

  /* Geometry and styling of one wire, shared by the SVG and bitmap
     painters. The wire wears the upstream node's status, so trouble
     shows in the flow itself, not only on the cards. */
  function wireGeom(u, d) {
    // Depend on layout coords, not DOM: in canvas mode there are no cards.
    if (!L || L.px[u] === undefined || L.px[d] === undefined ||
        L.top[u] === undefined || L.top[d] === undefined) return null;
    var x1 = L.px[u] + L.w[u] / 2, y1 = L.top[u];
    var x2 = L.px[d] + L.w[d] / 2, y2 = L.top[d] + L.h[d];
    var dy = Math.max(18, (y1 - y2) * 0.45);
    return {
      pts: [x1, y1, x1, y1 - dy, x2, y2 + dy, x2, y2],
      d: "M" + x1 + "," + y1 + "C" + x1 + "," + (y1 - dy) + " " +
        x2 + "," + (y2 + dy) + " " + x2 + "," + y2,
      tone: (byRef[u] && byRef[u].tone) || "ok",
      shared: (byRef[u] && byRef[u].shared) || (runById[u] && runById[u].shared)
    };
  }

  /* The themed stroke of each wire class, read off a probe path so the
     bitmap uses exactly what the stylesheet says in the active theme. */
  function wireColors() {
    var out = {};
    ["ok", "late", "crit"].forEach(function (tone) {
      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("class", "sg-bg-wire w-" + tone);
      wires.appendChild(p);
      var cs = getComputedStyle(p);
      out[tone] = { color: cs.stroke, alpha: parseFloat(cs.opacity) || 1 };
      wires.removeChild(p);
    });
    return out;
  }

  /* Big graphs: raster the static wires once into a canvas bitmap. Tile
     re-raster then blits a cached texture instead of replaying 3,600
     anti-aliased beziers - which measured at ~1s per frame and was THE
     reason the shipment graphs froze on hover and scroll. */
  function paintWireCanvas() {
    var rs = Math.min(1, 4096 / Math.max(L.W, L.H));
    wcan.width = Math.max(1, Math.round(L.W * rs));
    wcan.height = Math.max(1, Math.round(L.H * rs));
    wcan.style.width = L.W + "px";
    wcan.style.height = L.H + "px";
    var g2 = wcan.getContext("2d");
    g2.setTransform(rs, 0, 0, rs, 0, 0);
    g2.lineWidth = 2;
    var colors = wireColors();
    L.E.forEach(function (e) {
      var g = wireGeom(e[0], e[1]);
      if (!g) return;
      var c = colors[g.tone] || colors.ok;
      g2.strokeStyle = c.color;
      g2.globalAlpha = c.alpha;
      g2.setLineDash(g.shared ? [5, 4] : []);
      g2.beginPath();
      g2.moveTo(g.pts[0], g.pts[1]);
      g2.bezierCurveTo(g.pts[2], g.pts[3], g.pts[4], g.pts[5], g.pts[6], g.pts[7]);
      g2.stroke();
    });
  }

  function drawWires() {
    wires.setAttribute("width", L.W);
    wires.setAttribute("height", L.H);
    wires.setAttribute("viewBox", "0 0 " + L.W + " " + L.H);
    wireEl = {};
    hotOverlays = [];
    cpOverlays = [];
    litEls = [];
    machineEls = {};
    flaggedEls = [];

    if (!wcan) {
      wcan = document.createElement("canvas");
      wcan.className = "sg-bg-wirecan";
      world.insertBefore(wcan, wires);
    }

    if (L.ids.length > HOVER_MAX) {
      // Static wires as one bitmap; the SVG holds only the handful of
      // dynamic chain wires added by highlight() / markCritical().
      wires.innerHTML = "";
      wcan.style.display = "block";
      paintWireCanvas();
      return;
    }

    wcan.style.display = "none";
    var parts = [];
    L.E.forEach(function (e) {
      var g = wireGeom(e[0], e[1]);
      if (!g) return;
      parts.push('<path class="sg-bg-wire w-' + g.tone +
        (g.shared ? " is-share" : "") +
        '" data-u="' + esc(e[0]) + '" data-d="' + esc(e[1]) + '" d="' +
        g.d + '"/>');
    });
    wires.innerHTML = parts.join("");

    // Index the paths by edge key so highlight() can light the few wires
    // of one chain without walking every path in the SVG.
    Array.prototype.forEach.call(wires.children, function (w) {
      wireEl[w.getAttribute("data-u") + "|" + w.getAttribute("data-d")] = w;
    });
  }

  /* Light one wire: toggle the class on its SVG path where one exists
     (small graphs), or add a temporary overlay path (big graphs, whose
     base wires live in the bitmap). */
  function litWire(u, d, cls, bucket) {
    var el = wireEl[u + "|" + d];
    if (el) {
      el.classList.add(cls);
      bucket.push({ el: el, cls: cls, overlay: false });
      return;
    }
    var g = wireGeom(u, d);
    if (!g) return;
    var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("class", "sg-bg-wire w-" + g.tone + " " + cls);
    p.setAttribute("d", g.d);
    wires.appendChild(p);
    bucket.push({ el: p, cls: cls, overlay: true });
  }

  function clearWires(bucket) {
    bucket.forEach(function (b) {
      if (b.overlay) { if (b.el.parentNode) b.el.parentNode.removeChild(b.el); }
      else b.el.classList.remove(b.cls);
    });
    bucket.length = 0;
  }

  /* Rail markers: dates in time view, assembly levels in structure view. */
  function drawRail() {
    var out = [];
    var last = -1e9;
    for (var b = 0; b < L.nbands; b++) {
      if (L.bandTop[b] === undefined) continue;
      var y = L.bandTop[b];
      if (VIEW === "structure") {
        var lbl = b === 0 ? "SHIPMENT"
          : b === 1 ? "final assembly"
          : "level " + b;
        out.push('<div class="sg-bg-mark' + (b === 0 ? " is-edge" : "") +
          '" style="top:' + y + 'px"><b>' + esc(lbl) +
          "</b><span>" + (b === 0 ? "what we deliver"
                          : b === 1 ? "feeds the shipment"
                          : "feeds level " + (b - 1)) + "</span></div>");
        continue;
      }
      var t = L.times[b];
      if (t === undefined) continue;
      if (y - last < 30 / Math.max(zoom, 0.2) && b !== 0 && b !== L.nbands - 1) continue;
      last = y;
      var rows = (L.maxSub[b] || 0) + 1;
      out.push('<div class="sg-bg-mark' + (b === 0 || b === L.nbands - 1 ? " is-edge" : "") +
        '" style="top:' + y + 'px"><b>' + esc(dayLabel(t)) + "</b><span>" +
        esc(timeLabel(t)) + " · " + new Date(t).getFullYear() +
        (rows > 1 ? " · " + rows + " steps" : "") + "</span></div>");
    }
    railIn.innerHTML = out.join("");
  }

  // ------------------------------------------------- the delay chain
  //
  // From the shipment, walk upstream always taking the predecessor that
  // FINISHES LATEST - at every hop that is the run the next one was
  // genuinely waiting on, so the walk traces the chain that sets the
  // ship date. Dates are the solver's own; nothing is estimated.
  /* The pure walk, shared by both renderers: returns the critical chain
     as sets plus the ordered chain for the "follow the delay" tour. */
  function computeCritical() {
    var nodes = {}, edges = {}, chain = [];
    if (!L) return { nodes: nodes, edges: edges, chain: chain };
    var cur = "__order", hops = 0;
    nodes[cur] = true;
    while (hops++ < 600) {
      var preds = L.pred[cur] || [];
      if (!preds.length) break;
      var best = null, bt = -1;
      preds.forEach(function (p) {
        var n = byRef[p];
        var t = n && n.end ? Date.parse(n.end) : 0;
        if (t > bt) { bt = t; best = p; }
      });
      if (!best) break;
      edges[best + "|" + cur] = true;
      nodes[best] = true;
      chain.push(best);
      cur = best;
    }
    return { nodes: nodes, edges: edges, chain: chain };
  }

  function markCritical() {
    Array.prototype.forEach.call(
      world.querySelectorAll(".sg-bg-node.is-cp"),
      function (el) { el.classList.remove("is-cp"); });
    Array.prototype.forEach.call(wires.children, function (w) {
      w.classList.remove("is-cp");
    });
    if (!L) return;
    var r = computeCritical();
    cpChain = r.chain;
    if (els["__order"]) els["__order"].classList.add("is-cp");
    Object.keys(r.edges).forEach(function (k) {
      var i = k.indexOf("|");
      litWire(k.slice(0, i), k.slice(i + 1), "is-cp", cpOverlays);
    });
    Object.keys(r.nodes).forEach(function (id) {
      if (els[id]) els[id].classList.add("is-cp");
    });
  }

  // -------------------------------------------------------- zoom & pan

  function applyZoom() {
    world.style.transform = "scale(" + zoom + ")";
    // The rail deliberately does not scale - see drawRail(). Redrawing
    // it here is what keeps the markers lined up with the bands, and it
    // re-thins them for the new zoom level at the same time.
    if (L) drawRail();
    sizer.style.width = L.W * zoom + "px";
    sizer.style.height = L.H * zoom + "px";
    // A chain-shaped graph is much narrower than the canvas. Centre it
    // rather than pinning a single column to the left edge.
    world.style.left = Math.max(0, (canvas.clientWidth - L.W * zoom) / 2) + "px";
    world.classList.toggle("z-far", zoom < 0.62);
    world.classList.toggle("z-tiny", zoom < 0.4);
    if (zoomLabel) zoomLabel.textContent = Math.round(zoom * 100) + "%";
    syncRail();
  }

  function syncRail() {
    railIn.style.marginTop = -canvas.scrollTop + "px";
  }

  function setZoom(z, anchorY, anchorX) {
    if (gfxOn) { gfxSetZoom(z, anchorX, anchorY); return; }
    var before = (canvas.scrollTop + (anchorY || canvas.clientHeight / 2)) / zoom;
    zoom = clamp(z, ZOOM_MIN, ZOOM_MAX);
    applyZoom();
    canvas.scrollTop = before * zoom - (anchorY || canvas.clientHeight / 2);
    syncRail();
  }

  function fit() {
    if (!L) return;
    if (gfxOn) { gfxFit(); return; }
    var z = Math.min(canvas.clientWidth / (L.W + 20), canvas.clientHeight / (L.H + 20));
    zoom = clamp(z, ZOOM_MIN, ZOOM_MAX);
    applyZoom();
    canvas.scrollTop = 0;
    canvas.scrollLeft = 0;
    syncRail();
  }

  function centreOn(id, smooth) {
    if (gfxOn) { gfxCentreOn(id); return; }
    if (!L || !L.px[id]) { if (L && L.top[id] === undefined) return; }
    if (L.top[id] === undefined) return;
    canvas.scrollTo({
      left: Math.max(0, (L.px[id] + L.w[id] / 2) * zoom - canvas.clientWidth / 2),
      top: Math.max(0, (L.top[id] + L.h[id] / 2) * zoom - canvas.clientHeight / 2),
      behavior: smooth === false ? "auto" : "smooth"
    });
  }

  // -------------------------------------------------- chain highlighting
  //
  // Requirement: make the dependency chain up to the finished machine
  // visually obvious. Everything on the path from a node to the order
  // lights up; everything else dims.

  function chainOf(id) {
    var keep = {}, wire = {};
    var stack = [id];
    keep[id] = true;
    while (stack.length) {
      var cur = stack.pop();
      (L.succ[cur] || []).forEach(function (s) {
        wire[cur + "|" + s] = true;
        if (!keep[s]) { keep[s] = true; stack.push(s); }
      });
    }
    // and one level of what feeds it, so the immediate inputs stay legible
    (L.pred[id] || []).forEach(function (p) {
      keep[p] = true;
      wire[p + "|" + id] = true;
    });
    return { keep: keep, wire: wire };
  }

  /* Dimming is one class on the WORLD; the kept chain opts out with
     is-keep / is-hot. Each call therefore touches O(chain) elements, not
     every card and wire on the canvas - which is the difference between
     a smooth hover and a 2,100-card restyle per mouse move. */
  function highlight(id) {
    if (!L) return;
    hoverId = id || null;
    if (gfxOn) {
      gLit = id ? chainOf(id) : null;
      scheduleDraw();
      return;
    }
    litEls.forEach(function (el) { el.classList.remove("is-keep"); });
    litEls = [];
    clearWires(hotOverlays);
    if (!id) {
      world.classList.remove("is-dimming");
      return;
    }
    var c = chainOf(id);
    Object.keys(c.keep).forEach(function (n) {
      var el = els[n];
      if (el) { el.classList.add("is-keep"); litEls.push(el); }
    });
    Object.keys(c.wire).forEach(function (k) {
      var i = k.indexOf("|");
      litWire(k.slice(0, i), k.slice(i + 1), "is-hot", hotOverlays);
    });
    world.classList.add("is-dimming");
  }

  // Hover goes through one rAF gate: repeated mouseover events for the
  // same card do nothing, and bursts collapse into one highlight per
  // frame.
  var hoverId = null, hoverRaf = 0, hoverWant = null;
  function hoverHighlight(id) {
    hoverWant = id;
    if (hoverRaf) return;
    hoverRaf = requestAnimationFrame(function () {
      hoverRaf = 0;
      if (!selected && hoverWant !== hoverId) highlight(hoverWant);
    });
  }

  // ------------------------------------------------------- detail panel

  function openPanel() { panel.classList.add("is-open"); }
  function closePanel() {
    panel.classList.remove("is-open");
    if (selected && els[selected]) els[selected].classList.remove("is-sel");
    selected = null;
    highlight(null);
  }

  function kv(pairs) {
    var body = pairs.filter(function (p) {
      return p && p[1] !== "" && p[1] !== null && p[1] !== undefined;
    }).map(function (p) {
      return "<dt>" + esc(p[0]) + "</dt><dd>" + p[1] + "</dd>";
    }).join("");
    return body ? '<dl class="sg-bg-kv">' + body + "</dl>" : "";
  }

  function sec(title, inner, note) {
    if (!inner) return "";
    return '<section class="sg-bg-sec"><h3>' + esc(title) + "</h3>" +
      (note ? '<p class="sg-bg-note">' + note + "</p>" : "") + inner + "</section>";
  }

  function select(id) {
    if (selected && els[selected]) els[selected].classList.remove("is-sel");
    selected = id;
    if (els[id]) els[id].classList.add("is-sel");
    highlight(id);
    openPanel();

    var v = L.V[id];
    panelKind.textContent = v.eyebrow;
    panelTitle.textContent = v.title;
    panelBd.innerHTML = '<p class="sg-bg-load">loading…</p>';

    if (id === "__order") return renderOrder();
    if (runById[id] && collapsed[id]) return fetchRun(id);
    var n = byRef[id];
    if (!n) return;
    if (n.kind === "run") return fetchRun(id);
    renderOpLocal(n);
    fetchOp(n.ref);
  }

  function renderOrder() {
    var o = DATA.order;
    var m = DATA.meta;
    panelBd.innerHTML =
      kv([
        ["order", esc(o.name)],
        ["part", esc(o.item)],
        ["description", esc(o.description)],
        ["customer", esc(o.customer)],
        ["quantity", esc(o.quantity)],
        ["due", esc(o.due_txt)],
        ["planned delivery", esc(o.delivery_txt)],
        ["lateness", o.delay_days > 0.05
          ? '<b class="sg-bg-pill is-late">' + o.delay_days + " days late</b>"
          : '<span class="sg-bg-pill is-ok">on time</span>'],
        ["status", esc(o.status)],
        ["priority", esc(o.priority)],
        ["location", esc(o.location)]
      ]) +
      sec("this graph", kv([
        ["operations", m.nodes],
        ["production runs", m.runs],
        ["dependencies", m.edges],
        ["levels", m.depth + 1],
        ["earliest", m.min_date ? esc(dayLabel(ms(m.min_date))) : "—"],
        ["latest", m.max_date ? esc(dayLabel(ms(m.max_date))) : "—"]
      ]), m.truncated
        ? "This graph hit the " + m.max_nodes + "-node ceiling and is partial."
        : "") +
      '<footer class="sg-bg-ft">' +
      '<a class="sg-bg-btn" href="/data/input/demand/?name=' + encodeURIComponent(o.name) +
        '">open the order</a>' +
      '<a class="sg-bg-btn" href="/build-graph/">other machines</a>' +
      "</footer>";
  }

  /* What we already hold about an operation, shown at once. The full
     record - routing siblings, components, the customer orders it feeds -
     is fetched from the plan board's existing endpoint and appended. */
  function renderOpLocal(n, extra) {
    panelBd.innerHTML =
      kv([
        ["reference", esc(n.ref)],
        ["step", n.seq === null || n.seq === undefined ? "" : esc(n.seq)],
        ["operation", esc(n.operation)],
        ["description", esc(n.operation_description)],
        ["part", esc(n.item)],
        ["part description", esc(n.description)],
        ["quantity", esc(n.qty)],
        ["for this order", n.peg_qty ? esc(n.peg_qty) : ""],
        ["machine", n.machines.length ? esc(n.machines.join(", ")) : "—"],
        ["setup", esc(n.setup)],
        ["planned start", esc(n.start_txt)],
        ["planned finish", esc(n.end_txt)],
        ["duration", n.hours ? n.hours + " h" : ""],
        ["status", esc(n.status)],
        ["lateness", n.delay_days > 0.05
          ? '<b class="sg-bg-pill is-late">' + n.delay_days + " days late</b>"
          : '<span class="sg-bg-pill is-ok">on time</span>'],
        ["production run", n.run ? esc(n.run) : ""]
      ]) +
      predSucc(n.ref) +
      (n.shared
        ? sec("shared", '<p class="sg-bg-note">This operation is pegged to <b>' +
            n.npeg + "</b> customer orders — it is not built for this machine alone. " +
            "Open its production run for the split.</p>" +
            (n.run ? '<button class="sg-bg-btn" type="button" data-run="' + esc(n.run) +
              '">production run ' + esc(n.run) + "</button>" : ""))
        : "") +
      (extra || '<p class="sg-bg-load">loading the full record…</p>');
  }

  /* Predecessors and successors, straight off the solver's own links. */
  function predSucc(id) {
    var P = (L.pred[id] || []), S = (L.succ[id] || []);
    function list(arr) {
      if (!arr.length) return "";
      return arr.map(function (r) {
        var v = L.V[r];
        return '<button class="sg-bg-btn" type="button" data-goto="' + esc(r) + '">' +
          esc(v ? v.title : r) + "</button>";
      }).join(" ");
    }
    var inner = "";
    if (S.length) inner += '<p class="sg-bg-note"><b>feeds</b><br>' + list(S) + "</p>";
    if (P.length) inner += '<p class="sg-bg-note"><b>needs first</b><br>' + list(P) + "</p>";
    if (!P.length) inner += '<p class="sg-bg-note">Nothing upstream in the plan — ' +
      "this is where the chain for this machine starts.</p>";
    return sec("dependencies", inner);
  }

  function fetchOp(ref) {
    var url = root.dataset.opUrl + encodeURIComponent(ref) + "/";
    get(url, "op:" + ref, function (d) {
      if (selected === null) return;
      var html = "";
      if (d.job && d.job.steps && d.job.steps.length) {
        html += sec("the whole routing",
          '<table class="sg-bg-tb"><thead><tr><th>step</th><th>machine</th>' +
          "<th>from</th><th>to</th><th class=num>qty</th></tr></thead><tbody>" +
          d.job.steps.map(function (s) {
            return "<tr" + (s.ref === ref ? ' class="is-this"' : "") + "><td>" +
              esc(s.operation || s.ref) +
              (s.description
                ? '<br><small style="opacity:.6">' + esc(s.description) + "</small>"
                : "") +
              "</td><td>" +
              (esc(s.machines.join(", ")) || "—") + "</td><td>" +
              esc(s.start) + "</td><td>" + esc(s.end) + "</td><td class=num>" +
              esc(s.quantity) + "</td></tr>";
          }).join("") + "</tbody></table>",
          "The highlighted row is the card you clicked.");
      }
      if (d.consumes && d.consumes.length) {
        html += sec("components consumed",
          '<table class="sg-bg-tb"><thead><tr><th>part</th><th>description</th>' +
          "<th class=num>qty</th><th>when</th></tr></thead><tbody>" +
          d.consumes.map(function (c) {
            return "<tr><td>" + esc(c.item) + "</td><td>" + esc(c.description) +
              "</td><td class=num>" + esc(c.quantity) + "</td><td>" + esc(c.date) +
              "</td></tr>";
          }).join("") + "</tbody></table>");
      }
      if (d.orders && d.orders.length) {
        html += sec("customer orders this feeds",
          '<table class="sg-bg-tb"><thead><tr><th>order</th><th>due</th>' +
          "<th>planned</th><th class=num>late</th></tr></thead><tbody>" +
          d.orders.map(function (o) {
            return "<tr" + (o.name === DATA.order.name ? ' class="is-this"' : "") +
              "><td>" + esc(o.name) + "</td><td>" + esc(o.due) + "</td><td>" +
              esc(o.delivery) + "</td><td class=num>" +
              (o.delay_days > 0.05 ? o.delay_days + " d" : "—") + "</td></tr>";
          }).join("") + "</tbody></table>");
      }
      html += '<footer class="sg-bg-ft">' +
        '<a class="sg-bg-btn" href="' + esc(d.links.mo) + '">open the job</a>' +
        (d.job && d.job.operation
          ? '<a class="sg-bg-btn" href="' + esc(d.links.routing) + '">routing</a>' : "") +
        "</footer>";

      var n = byRef[ref];
      if (n) renderOpLocal(n, html);
    }, function (e) {
      var n = byRef[ref];
      if (n) renderOpLocal(n, '<p class="sg-bg-note">Could not load the full record (' +
        esc(e) + ").</p>");
    });
  }

  /* A production run, and the split of it across customer orders. */
  function fetchRun(ref) {
    get(root.dataset.runUrl + encodeURIComponent(ref) + "/", "run:" + ref, function (d) {
      var total = parseFloat(d.quantity) || 0;
      var mine = 0;
      d.orders.forEach(function (o) {
        if (o.demand === DATA.order.name) mine = parseFloat(o.quantity) || 0;
      });
      var others = (parseFloat(d.allocated) || 0) - mine;
      var free = parseFloat(d.remaining) || 0;

      var bar = "";
      if (total > 0) {
        var pc = function (v) { return Math.max(0, (100 * v) / total) + "%"; };
        bar = '<div class="sg-bg-alloc">' +
          (mine > 0 ? '<i class="is-this" style="width:' + pc(mine) + '"></i>' : "") +
          (others > 0 ? '<i class="is-other" style="width:' + pc(others) + '"></i>' : "") +
          (free > 0 ? '<i class="is-free" style="width:' + pc(free) + '"></i>' : "") +
          "</div>" +
          '<p class="sg-bg-alloc-key">' +
          '<span><i class="is-this" style="background:var(--sg-accent)"></i>this order ' + mine + "</span>" +
          (others > 0 ? '<span><i style="background:var(--sg-accent-in)"></i>other orders ' +
            Math.round(others * 1000) / 1000 + "</span>" : "") +
          (free > 0 ? '<span><i style="background:var(--sg-rule)"></i>uncommitted ' +
            Math.round(free * 1000) / 1000 + "</span>" : "") +
          "</p>";
      }

      var alloc = "";
      if (d.orders.length) {
        alloc = bar +
          '<table class="sg-bg-tb"><thead><tr><th>order</th><th>customer</th>' +
          "<th class=num>qty</th><th class=num>share</th><th>due</th></tr></thead><tbody>" +
          d.orders.map(function (o) {
            var link = '<a href="' + esc(root.dataset.orderUrl) +
              encodeURIComponent(o.demand) + '/">' + esc(o.demand) + "</a>";
            return "<tr" + (o.demand === DATA.order.name ? ' class="is-this"' : "") +
              "><td>" + link + "</td><td>" + esc(o.customer || "—") +
              "</td><td class=num>" + esc(o.quantity) + "</td><td class=num>" +
              o.share + "%</td><td>" + esc(o.due) + "</td></tr>";
          }).join("") + "</tbody></table>";
      }

      var steps = "";
      if (d.steps.length) {
        steps = '<table class="sg-bg-tb"><thead><tr><th>seq</th><th>operation</th>' +
          "<th>machine</th><th>from</th><th>to</th></tr></thead><tbody>" +
          d.steps.map(function (s) {
            return '<tr><td>' + esc(s.seq === null ? "" : s.seq) + "</td><td>" +
              esc(s.operation) +
              (s.description
                ? '<br><small style="opacity:.6">' + esc(s.description) + "</small>"
                : "") +
              "</td><td>" +
              (esc((s.machines || []).join(", ")) || "—") + "</td><td>" +
              esc(s.start) + "</td><td>" + esc(s.end) + "</td></tr>";
          }).join("") + "</tbody></table>";
      }

      panelBd.innerHTML =
        kv([
          ["production run", esc(d.ref)],
          ["part", esc(d.item)],
          ["description", esc(d.description)],
          ["routing", esc(d.operation)],
          ["total produced", esc(d.quantity)],
          ["planned start", esc(d.start)],
          ["planned finish", esc(d.end)],
          ["status", esc(d.status)],
          ["location", esc(d.location)],
          ["lateness", d.delay_days > 0.05
            ? '<b class="sg-bg-pill is-late">' + d.delay_days + " days late</b>"
            : '<span class="sg-bg-pill is-ok">on time</span>']
        ]) +
        sec(d.shared ? "allocation — shared across " + d.order_count + " orders"
                     : "allocation", alloc,
          d.shared
            ? "This run is <b>common to several machines</b>. It is produced once and " +
              "split as below — the split is the planning engine's own pegging, not " +
              "an estimate."
            : "") +
        sec("operation sequence", steps) +
        predSucc(d.ref) +
        '<footer class="sg-bg-ft">' +
        '<a class="sg-bg-btn" href="' + esc(d.links.mo) + '">open the job</a>' +
        '<a class="sg-bg-btn" href="' + esc(d.links.routing) + '">routing</a>' +
        (runById[d.ref] && collapsed[d.ref]
          ? '<button class="sg-bg-btn" type="button" data-toggle="' + esc(d.ref) +
            '">show its operations</button>' : "") +
        "</footer>";
    }, function (e) {
      panelBd.innerHTML = '<p class="sg-bg-note">Could not load this run (' + esc(e) + ").</p>";
    });
  }

  function get(url, key, ok, bad) {
    if (detailCache[key]) return ok(detailCache[key]);
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) { detailCache[key] = d; ok(d); })
      .catch(function (e) { (bad || function () {})(e.message); });
  }

  // -------------------------------------------------------------- search

  function search(term) {
    term = (term || "").trim().toLowerCase();
    if (!term) { hits.hidden = true; hits.innerHTML = ""; return; }
    var found = [];
    L.ids.forEach(function (id) {
      if (found.length >= 40) return;
      var v = L.V[id];
      var n = byRef[id];
      var hay = [v.title, v.sub, id,
        n ? n.item : "", n ? n.operation : "", n ? (n.machines || []).join(" ") : "",
        n ? n.description : ""].join(" ").toLowerCase();
      if (hay.indexOf(term) >= 0) found.push(id);
    });
    if (!found.length) {
      hits.innerHTML = "<p>Nothing in this graph matches.</p>";
    } else {
      hits.innerHTML = found.map(function (id) {
        var v = L.V[id];
        return '<button type="button" data-goto="' + esc(id) + '">' + esc(v.title) +
          " <em>" + esc(v.eyebrow) + "</em></button>";
      }).join("");
    }
    hits.hidden = false;
  }

  // ------------------------------------------------------------- events

  world.addEventListener("click", function (ev) {
    var tog = ev.target.closest("[data-toggle]");
    if (tog) {
      ev.stopPropagation();
      var rid = tog.getAttribute("data-toggle");
      collapsed[rid] = !collapsed[rid];
      var keep = selected;
      render();
      if (keep && els[keep]) select(keep); else closePanel();
      return;
    }
    var lb = ev.target.closest("[data-run]");
    if (lb && !ev.target.closest("[data-toggle]")) {
      ev.stopPropagation();
      var r = lb.getAttribute("data-run");
      panelKind.textContent = "production run · " + r;
      panelTitle.textContent = (runById[r] && runById[r].item) || r;
      openPanel();
      panelBd.innerHTML = '<p class="sg-bg-load">loading…</p>';
      fetchRun(r);
      return;
    }
    var node = ev.target.closest(".sg-bg-node");
    if (node) select(node.getAttribute("data-id"));
  });

  world.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    var node = ev.target.closest(".sg-bg-node");
    if (node) { ev.preventDefault(); select(node.getAttribute("data-id")); }
  });

  world.addEventListener("mouseover", function (ev) {
    if (selected) return;
    if (L && L.ids.length > HOVER_MAX) return;
    var node = ev.target.closest(".sg-bg-node");
    if (node) hoverHighlight(node.getAttribute("data-id"));
  });
  world.addEventListener("mouseout", function (ev) {
    if (selected) return;
    if (L && L.ids.length > HOVER_MAX) return;
    if (!ev.relatedTarget || !ev.relatedTarget.closest(".sg-bg-node")) {
      hoverHighlight(null);
    }
  });

  panelBd.addEventListener("click", function (ev) {
    var g = ev.target.closest("[data-goto]");
    if (g) { var id = g.getAttribute("data-goto"); select(id); centreOn(id); return; }
    var r = ev.target.closest("[data-run]");
    if (r) fetchRun(r.getAttribute("data-run"));
    var t = ev.target.closest("[data-toggle]");
    if (t) {
      var rid = t.getAttribute("data-toggle");
      collapsed[rid] = !collapsed[rid];
      render();
      closePanel();
    }
  });

  document.getElementById("sg-bg-panel-x").addEventListener("click", closePanel);

  /* The toolbar is live before the graph has arrived, and a click on 'fit'
     in that window used to no-op and then be overwritten by the opening
     zoom. Disable the controls until there is something to act on. */
  var acts = Array.prototype.slice.call(root.querySelectorAll("[data-act]"));
  acts.forEach(function (b) { b.disabled = true; });
  function armToolbar() { acts.forEach(function (b) { b.disabled = false; }); }

  // A node still exists after a re-render (DOM card or canvas node id).
  function hasNode(id) { return id != null && (gfxOn ? L.V[id] : els[id]); }

  root.querySelector(".sg-bg-top").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-act]");
    if (!b || !L) return;
    var act = b.getAttribute("data-act");
    if (act === "zoom-in") setZoom((gfxOn ? gView.scale : zoom) * 1.25);
    else if (act === "zoom-out") setZoom((gfxOn ? gView.scale : zoom) / 1.25);
    else if (act === "fit") fit();
    else if (act === "reset") {
      if (gfxOn) gfxReset();
      else { zoom = 1; applyZoom(); canvas.scrollTop = 0; canvas.scrollLeft = 0; syncRail(); }
    }
    else if (act === "top") {
      if (gfxOn) { gView.oy = 20; gfxAfterZoom(); }
      else canvas.scrollTo({ top: 0, behavior: "smooth" });
    }
    else if (act === "bottom") {
      if (gfxOn) { gView.oy = Math.min(20, canvas.clientHeight - L.H * gView.scale - 20); gfxAfterZoom(); }
      else canvas.scrollTo({ top: canvas.scrollHeight, behavior: "smooth" });
    }
    else if (act === "renderer") {
      var now = renderMode();
      try { localStorage.setItem("sg-bg-render", now === "canvas" ? "dom" : "canvas"); } catch (e) {}
      // drop any ?render= override so the stored choice wins
      try {
        var u = new URL(location.href); u.searchParams.delete("render");
        history.replaceState(null, "", u);
      } catch (e2) {}
      var keepR = selected;
      didEnter = true; // no entrance replay on a mode switch
      render();
      if (hasNode(keepR)) select(keepR); else closePanel();
    }
    else if (act === "expand" || act === "collapse") {
      DATA.runs.forEach(function (r) { collapsed[r.id] = act === "collapse"; });
      var keepC = selected;
      render();
      if (hasNode(keepC)) select(keepC); else closePanel();
    }
    else if (act === "follow") {
      followDelay();
    }
  });

  // Walk the delay chain, shipment downwards, pausing on each run - the
  // story of why this shipment is late, told by the graph itself.
  var followTimer = null;
  function followDelay() {
    if (followTimer) { clearInterval(followTimer); followTimer = null; }
    if (!cpChain.length) return;
    var stops = ["__order"].concat(cpChain);
    var i = 0;
    function step() {
      Array.prototype.forEach.call(
        world.querySelectorAll(".is-cp-focus"),
        function (el) { el.classList.remove("is-cp-focus"); });
      if (i >= stops.length) {
        clearInterval(followTimer); followTimer = null;
        gFocus = null; if (gfxOn) scheduleDraw();
        return;
      }
      var id = stops[i++];
      if (gfxOn) { gFocus = id; centreOn(id); scheduleDraw(); }
      else if (els[id]) {
        els[id].classList.add("is-cp-focus");
        centreOn(id);
      }
    }
    // 1.3s was too quick to follow: the smooth scroll alone eats most of
    // it, so a stop was gone before the eye had settled on it. 3.4s gives
    // roughly a second of travel and two full pulses standing still.
    step();
    followTimer = setInterval(step, 3400);
  }
  // A click anywhere on the canvas ends the tour - the planner has
  // taken over.
  canvas.addEventListener("pointerdown", function () {
    if (followTimer) { clearInterval(followTimer); followTimer = null; }
  });

  if (findBox) {
    findBox.addEventListener("input", function () { search(findBox.value); });
    findBox.addEventListener("focus", function () { if (findBox.value) search(findBox.value); });
  }
  hits.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-goto]");
    if (!b) return;
    var id = b.getAttribute("data-goto");
    hits.hidden = true;
    select(id);
    centreOn(id);
    if (gfxOn) { scheduleDraw(); }
    else if (els[id]) {
      els[id].classList.add("is-hit");
      setTimeout(function () { els[id] && els[id].classList.remove("is-hit"); }, 1600);
    }
  });
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest(".sg-bg-find")) hits.hidden = true;
  });

  // Close the machine switcher when the click lands anywhere else.
  var switcher = root.querySelector(".sg-bg-switch");
  if (switcher) {
    document.addEventListener("click", function (ev) {
      if (!ev.target.closest(".sg-bg-switch")) switcher.open = false;
    });
  }

  // Blocker cards light up the runs they hold. A flag class, not the
  // dim-everything chain highlight, so the delay chain stays visible.
  var blockers = document.getElementById("sg-bg-blockers");
  if (blockers) {
    // machine -> its card elements, computed once per machine (and reset
    // by drawWires on each render). Flagging then touches only those
    // cards plus one class on the world, instead of restyling every card
    // on the canvas per rail hover - which froze the big shipment graphs.
    function flagMachine(machine, on) {
      if (!L) return;
      if (gfxOn) { gFlag = on ? machine : null; scheduleDraw(); return; }
      flaggedEls.forEach(function (el) { el.classList.remove("is-flag"); });
      flaggedEls = [];
      if (!on) {
        world.classList.remove("is-flagging");
        return;
      }
      if (!machineEls[machine]) {
        var hit = [];
        L.ids.forEach(function (id) {
          var n = byRef[id];
          if (n && n.machines && n.machines.indexOf(machine) >= 0 && els[id]) {
            hit.push(els[id]);
          }
        });
        machineEls[machine] = hit;
      }
      machineEls[machine].forEach(function (el) {
        el.classList.add("is-flag");
        flaggedEls.push(el);
      });
      world.classList.add("is-flagging");
    }
    Array.prototype.forEach.call(
      blockers.querySelectorAll(".sg-bg-blk"), function (card) {
        var m = card.dataset.machine;
        card.addEventListener("mouseenter", function () { flagMachine(m, true); });
        card.addEventListener("focus", function () { flagMachine(m, true); });
        card.addEventListener("mouseleave", function () { flagMachine(m, false); });
        card.addEventListener("blur", function () { flagMachine(m, false); });
      });
  }

  canvas.addEventListener("scroll", syncRail, { passive: true });

  // Wheel zoom is deliberately gentle. A mouse wheel fires several
  // events per physical notch and a trackpad fires dozens per gesture,
  // so a per-event factor that feels right for one click of the wheel
  // is violent for the other. Scale the step by how big the delta
  // actually is, then cap it - a fast flick zooms further than a slow
  // one, but never by more than a few percent per event.
  var ZOOM_RATE = 0.0016;   // per unit of deltaY
  var ZOOM_STEP_MAX = 0.06; // never more than 6% in one event

  canvas.addEventListener("wheel", function (ev) {
    if (!ev.ctrlKey && !ev.metaKey) return;
    ev.preventDefault();
    var rect = canvas.getBoundingClientRect();
    // deltaMode 1 = lines, 2 = pages. Normalise both to pixel-ish units.
    var d = ev.deltaY * (ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 100 : 1);
    var step = clamp(Math.abs(d) * ZOOM_RATE, 0, ZOOM_STEP_MAX);
    var base = gfxOn ? gView.scale : zoom;
    setZoom(base * (d < 0 ? 1 + step : 1 / (1 + step)),
      ev.clientY - rect.top, ev.clientX - rect.left);
  }, { passive: false });

  // Drag to pan - from anywhere on the canvas, cards included: on the
  // big graphs cards cover nearly the whole screen, so a pan that only
  // starts on empty ground is a pan nobody can start. Panning engages
  // after a few pixels of movement; a press-and-release without movement
  // is still a click, and a click that follows a pan is swallowed.
  var drag = null;
  var dragged = false; // a pan happened: swallow the click that follows it
  canvas.addEventListener("pointerdown", function (ev) {
    if (ev.button !== 0) return;
    if (ev.target.closest(".sg-bg-panel, button, a, input, select, textarea")) return;
    drag = {
      x: ev.clientX, y: ev.clientY,
      l: gfxOn ? gView.ox : canvas.scrollLeft,
      t: gfxOn ? gView.oy : canvas.scrollTop,
      id: ev.pointerId, moved: false
    };
  });
  // Pan writes are coalesced to one per animation frame. A real mouse or
  // trackpad fires pointermove several times per frame; writing scrollLeft
  // on each one - every write also firing the scroll -> syncRail handler -
  // is layout work multiplied by event rate, which is what makes a fast
  // drag feel heavy even when the frame budget is otherwise fine. We just
  // remember the latest position and apply it once, in a rAF.
  var panRaf = 0;
  function panFrame() {
    panRaf = 0;
    if (!drag || !drag.moved) return;
    if (gfxOn) {
      gView.ox = drag.l + drag.dx;   // drag follows the content
      gView.oy = drag.t + drag.dy;
      gfxRailShift();                // cheap: just a transform, no DOM rebuild
      scheduleDraw();
    } else {
      canvas.scrollLeft = drag.l - drag.dx;
      canvas.scrollTop = drag.t - drag.dy;
    }
  }
  // Canvas-mode hover: hit-test under the pointer, throttled to one rAF.
  var hvRaf = 0, hvX = 0, hvY = 0;
  function hvFrame() {
    hvRaf = 0;
    if (!gfxOn || selected || drag) return;
    var hit = hitTest(hvX, hvY);
    var id = hit && hit.kind === "node" ? hit.id : null;
    if (id !== gHover) { gHover = id; highlight(id); }
    canvas.style.cursor = hit ? "pointer" : "";
  }
  canvas.addEventListener("pointermove", function (ev) {
    if (!drag) {
      if (gfxOn) {
        hvX = ev.clientX; hvY = ev.clientY;
        if (!hvRaf) hvRaf = requestAnimationFrame(hvFrame);
      }
      return;
    }
    var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
    if (!drag.moved) {
      if (Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
      drag.moved = true;
      canvas.classList.add("is-drag");
      try { canvas.setPointerCapture(drag.id); } catch (e) { /* gone */ }
    }
    drag.dx = dx;
    drag.dy = dy;
    if (!panRaf) panRaf = requestAnimationFrame(panFrame);
  });
  ["pointerup", "pointercancel"].forEach(function (e) {
    canvas.addEventListener(e, function () {
      dragged = !!(drag && drag.moved);
      drag = null;
      canvas.classList.remove("is-drag");
    });
  });
  // Capture phase, so a click produced by releasing a pan never reaches
  // the card underneath it.
  canvas.addEventListener("click", function (ev) {
    if (dragged) {
      dragged = false;
      ev.stopPropagation();
      ev.preventDefault();
      return;
    }
    if (!gfxOn) return;
    if (ev.target.closest(".sg-bg-panel, button, a, input")) return;
    var hit = hitTest(ev.clientX, ev.clientY);
    if (!hit) return;
    if (hit.kind === "frame") {
      // click the run frame label -> fold/unfold that run
      collapsed[hit.id] = !collapsed[hit.id];
      var keepF = selected;
      render();
      if (hasNode(keepF)) select(keepF); else closePanel();
      return;
    }
    if (hit.kind === "node") {
      if (runById[hit.id] && collapsed[hit.id]) {
        // a collapsed run: a plain click opens it in the panel; the planner
        // expands via the panel button, matching the DOM behaviour.
        select(hit.id);
      } else {
        select(hit.id);
      }
    }
  }, true);

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { closePanel(); hits.hidden = true; }
  });

  // Keep the canvas backing store matched to the viewport.
  window.addEventListener("resize", function () {
    if (gfxOn) gfxResize();
  });

  // --------------------------------------------------------------- boot

  fetch(root.dataset.url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" }
  })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (d) {
      DATA = d;
      d.nodes.forEach(function (n) { byRef[n.id] = n; });
      d.runs.forEach(function (r) {
        runById[r.id] = r;
        r.steps.forEach(function (s) { stepRun[s] = r.id; });
      });

      if (!d.nodes.length) {
        msg.innerHTML = "<p><b>Nothing is planned for this order.</b></p>" +
          "<p class=\"sg-bg-note\">No manufacturing order references " +
          esc(d.order.name) + ". That normally means the part has no routing, " +
          "so the planner cannot schedule it. Check the part's routing, then re-run the plan.</p>" +
          '<p><a class="sg-bg-btn" href="/data/input/operation/?item=' +
          encodeURIComponent(d.order.item) + '">routings for ' + esc(d.order.item) + "</a> " +
          '<a class="sg-bg-btn" href="/build-graph/">pick another machine</a></p>';
        return;
      }

      // Start with the runs open while the graph stays legible, and folded
      // once it does not. That way a small order shows its whole operation
      // sequence at once and a large bill of materials still opens fast.
      var opCount = d.nodes.filter(function (n) { return n.kind === "operation"; }).length;
      var foldAll = opCount > 70;
      d.runs.forEach(function (r) { collapsed[r.id] = foldAll; });

      render();
      armToolbar();
      // Canvas mode set its own initial view inside render(); only the DOM
      // path needs the open-at-full-size-then-pull-back-if-wide logic.
      if (!gfxOn) {
        // Open at full size, at the top. The whole point of the screen is to
        // start on the delivery date and scroll down through time, so fitting
        // a tall graph into the viewport on load - which shrinks a twelve-step
        // routing to 25% and unreadable - would defeat it. Only pull back when
        // the graph is genuinely too WIDE to find your way around.
        if (L.W > canvas.clientWidth) {
          setZoom(Math.max(0.55, canvas.clientWidth / (L.W + 40)));
        }
        canvas.scrollTop = 0;
        // The first thing a planner must see is the shipment itself; it
        // sits centred over a shelf thousands of pixels wide, so the
        // camera goes to it - after the zoom above, which rewrites scroll.
        if (MODE === "ship") {
          centreOn("__order", false);
          canvas.scrollTop = 0;
        }
        syncRail();
      }
      // Web font may finish loading after first paint; redraw so canvas text
      // uses the real metrics.
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(function () { if (gfxOn) scheduleDraw(); });
      }
    })
    .catch(function (e) {
      msg.innerHTML = '<p class="sg-bg-note">Could not build the graph (' +
        esc(e.message) + ").</p>";
    });
})();
