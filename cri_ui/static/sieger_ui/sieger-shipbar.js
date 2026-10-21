/* ------------------------------------------------------------------
   Sieger APS - the shipment switcher on frePPLe's own screens.

   The core grids (schedule, purchase orders, why late, exceptions,
   production orders) have no shipment field of their own - the DSQ tag
   is stamped onto plan rows after every replan (sieger_ui/stamp.py),
   and this bar turns the tag into the grid's own native URL filter.

   Nothing is hard-coded: the shipment list is fetched live, so a new
   DSQ appears here the moment it carries production orders.
   ------------------------------------------------------------------ */
(function () {
  "use strict";

  var RULES = [
    { re: /\/data\/input\/manufacturingorder\/?$/, key: "name__contains" },
    { re: /\/data\/input\/purchaseorder\/?$/, key: "name__contains" },
    { re: /\/data\/input\/demand\/?$/, key: "category__contains" },
    { re: /\/constraint\/?$/, key: "description__contains", marked: true },
    { re: /\/problem\/?$/, toShipment: true },
  ];

  var path = location.pathname;
  var rule = null;
  for (var i = 0; i < RULES.length; i++) {
    if (RULES[i].re.test(path)) { rule = RULES[i]; break; }
  }
  if (!rule) return;

  var parts = path.split("/").filter(Boolean);
  var prefix = "";
  if (parts.length && /^scenario\d*$/.test(parts[0])) prefix = "/" + parts[0];

  // Build the bar FIRST, then mount it - an earlier draft mounted
  // before creating it and died silently on every core screen.
  var bar = document.createElement("div");
  bar.className = "sg-shipbar";
  bar.innerHTML =
    '<label class="sg-shipbar-l"><span>shipment</span> ' +
    '<select class="sg-shipbar-sel"><option value="">all shipments</option></select>' +
    "</label>" +
    (rule.toShipment
      ? '<span class="sg-shipbar-note">exceptions are plant-wide - ' +
        "picking a shipment opens its own analysis</span>"
      : "");
  var sel = bar.querySelector("select");

  // frePPLe's admin pages keep their content in #content-main; our own
  // screens use #content. Fall back to the body so the bar always has
  // a home.
  var mount =
    document.getElementById("content-main") ||
    document.getElementById("content") ||
    document.body;
  mount.insertBefore(bar, mount.firstChild);

  function currentTag() {
    var m = location.search.match(
      new RegExp("[?&]" + rule.key + "=([^&]+)")
    );
    if (!m) return "";
    var v = decodeURIComponent(m[1]);
    return rule.marked ? v.replace(/^\[DSQ\s*/, "").trim() : v;
  }

  function stripParam(qs) {
    return qs
      .replace(new RegExp("[?&]" + rule.key + "=[^&]*"), "")
      .replace(/^[?&]/, "?")
      .replace(/\?$/, "");
  }

  fetch(prefix + "/build-graph/map/ships/", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      (d.ships || []).forEach(function (s) {
        var o = document.createElement("option");
        o.value = s.tag;
        o.textContent = (s.name || s.tag) + " (" + s.tag + ")";
        sel.appendChild(o);
      });
      var cur = currentTag();
      if (cur) sel.value = cur;
    })
    .catch(function () { /* the bar simply stays empty */ });

  sel.addEventListener("change", function () {
    if (!sel.value) {
      location.search = stripParam(location.search);
      return;
    }
    if (rule.toShipment) {
      location.href = prefix + "/shipment/" +
        encodeURIComponent(sel.value) + "/";
      return;
    }
    var val = rule.marked ? "[DSQ " + sel.value : sel.value;
    var base = stripParam(location.search);
    location.search = (base ? base + "&" : "?") +
      rule.key + "=" + encodeURIComponent(val);
  });
})();
