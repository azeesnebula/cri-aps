/* Saved-plan workspace: local navigation/filtering only. No network mutations. */
(function () {
  "use strict";
  var root = document.getElementById("shipment-workspace");
  if (!root) return;
  var panels = Array.from(root.querySelectorAll("[data-work-panel]"));
  var tablist = root.querySelector("[data-work-tabs]");
  if (!tablist) return;
  var tabs = Array.from(tablist.querySelectorAll("a"));
  var choices = Array.from(root.querySelectorAll("[data-blocker-choice]"));
  var blockerPanels = Array.from(root.querySelectorAll("[data-blocker-panel]"));
  var filter = root.querySelector("#order-filter");
  var search = root.querySelector("#order-search");
  var blockerSearch = root.querySelector("#blocker-search");
  var activeBlocker = choices.length ? choices[0].dataset.blockerChoice : null;
  var orderPager;
  var shipmentForm = root.querySelector("#production-shipment-filter");
  if (shipmentForm) {
    shipmentForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var url = new URL(shipmentForm.action, window.location.origin);
      var shipment = shipmentForm.querySelector("select").value;
      if (shipment) url.searchParams.set("dsq", shipment);
      // Keep the current section/filter, but a blocker ID belongs to the old
      // shipment and must not select an unrelated blocker after switching.
      url.hash = window.location.hash.startsWith("#blocker-") ? "#blockers" : window.location.hash;
      window.location.assign(url.href);
    });
    shipmentForm.querySelector("select").addEventListener("change", function () {
      shipmentForm.requestSubmit();
    });
  }

  // One paging implementation for order rows, conflict records and blocker choices.
  function pager(items, mount, empty, size) {
    var page = 0, predicate = function () { return true; };
    var texts = items.map(function (item) { return (item.textContent + " " + (item.dataset.searchExtra || "")).toLowerCase(); });
    var bar = document.createElement("div");
    bar.className = "sg-work-pager";
    var status = document.createElement("span");
    status.setAttribute("aria-live", "polite");
    status.setAttribute("aria-atomic", "true");
    var buttons = document.createElement("div");
    function button(label) {
      var el = document.createElement("button");
      el.type = "button";
      el.textContent = label;
      buttons.appendChild(el);
      return el;
    }
    var previous = button("Previous"), next = button("Next");
    bar.append(status, buttons);
    mount.appendChild(bar);
    function draw() {
      var matching = items.filter(function (item, index) { return predicate(item, texts[index]); });
      page = Math.min(page, Math.max(0, Math.ceil(matching.length / size) - 1));
      var visible = new Set(matching.slice(page * size, (page + 1) * size));
      items.forEach(function (item) { item.hidden = !visible.has(item); });
      status.textContent = matching.length
        ? (page * size + 1) + "–" + Math.min((page + 1) * size, matching.length) + " of " + matching.length
        : "0 results";
      previous.disabled = page === 0;
      next.disabled = (page + 1) * size >= matching.length;
      buttons.hidden = matching.length <= size;
      if (empty) empty.hidden = matching.length > 0;
    }
    previous.addEventListener("click", function () { page--; draw(); });
    next.addEventListener("click", function () { page++; draw(); });
    draw();
    return {
      filter: function (fn) { predicate = fn; page = 0; draw(); },
      reveal: function (item) {
        var matching = items.filter(function (entry, i) { return predicate(entry, texts[i]); });
        var index = matching.indexOf(item);
        if (index >= 0) { page = Math.floor(index / size); draw(); }
      }
    };
  }

  root.querySelectorAll("[data-page-table]").forEach(function (table) {
    var size = Number(table.dataset.pageSize) || 8;
    if (table.dataset.mobilePageSize && window.matchMedia("(max-width: 700px)").matches) size = Number(table.dataset.mobilePageSize);
    if (table.id === "shipment-order-table" && window.matchMedia("(max-width: 700px)").matches) size = 4;
    var controller = pager(Array.from(table.querySelectorAll("[data-page-row]")),
      table.querySelector("[data-page-controls]"), table.querySelector("[data-page-empty]"),
      size);
    if (table.id === "shipment-order-table") orderPager = controller;
    if (table.hasAttribute("data-search-table")) {
      var input = table.querySelector("[data-table-search]");
      var select = table.querySelector("[data-table-filter]");
      function applyFilter() {
        var term = input ? input.value.trim().toLowerCase() : "";
        controller.filter(function (row, text) {
          return (!term || text.includes(term)) && (!select || !select.value || row.dataset.kind === select.value);
        });
      }
      if (input) input.addEventListener("input", applyFilter);
      if (select) select.addEventListener("change", applyFilter);
    }
  });
  var blockerPager = choices.length ? pager(choices, root.querySelector("[data-blocker-pager]"),
    root.querySelector("[data-blocker-empty]"), window.matchMedia("(max-width: 700px)").matches ? 3 : 5) : null;

  function filterOrders() {
    var term = search.value.trim().toLowerCase();
    orderPager.filter(function (row, text) {
      var state = row.dataset.state, incomplete = row.dataset.incomplete === "true";
      var match = filter.value === "all" ||
        (filter.value === "attention" && (state !== "on time" || incomplete)) ||
        (filter.value === "incomplete" && incomplete) ||
        (filter.value === "ontime" && state === "on time") || filter.value === state;
      return match && (!term || text.includes(term));
    });
  }
  function selectBlocker(id, reveal) {
    if (!blockerPanels.some(function (panel) { return panel.id === id; })) return;
    root.querySelector("[data-blocker-detail-empty]").hidden = true;
    activeBlocker = id;
    blockerPanels.forEach(function (panel) { panel.hidden = panel.id !== id; });
    choices.forEach(function (choice) {
      choice.setAttribute("aria-current", choice.dataset.blockerChoice === id ? "true" : "false");
      choice.setAttribute("aria-controls", choice.dataset.blockerChoice);
    });
    if (reveal && blockerPager) {
      blockerSearch.value = "";
      blockerPager.filter(function () { return true; });
      blockerPager.reveal(choices.find(function (choice) { return choice.dataset.blockerChoice === id; }));
    }
  }
  function showTab(id) {
    panels.forEach(function (panel) { panel.hidden = panel.id !== id; });
    tabs.forEach(function (tab) {
      var selected = tab.getAttribute("href") === "#" + id;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
  }
  function route(hash, focus) {
    var id;
    try { id = decodeURIComponent((hash || "#overview").slice(1)); }
    catch (e) { id = "overview"; }
    var tab = id;
    if (id.startsWith("blocker-")) {
      tab = "blockers";
      selectBlocker(id, true);
    } else if (id.startsWith("orders-")) {
      tab = "orders";
      var value = id.slice(7);
      if (filter.value !== value && Array.from(filter.options).some(function (option) { return option.value === value; })) {
        filter.value = value;
        search.value = "";
        filterOrders();
      }
    } else if (id === "plan-trace") tab = "plan";
    if (!panels.some(function (panel) { return panel.id === tab; })) tab = "overview";
    showTab(tab);
    if (focus) {
      if (id.startsWith("blocker-") && document.getElementById(id + "-title")) {
        document.getElementById(id + "-title").focus({ preventScroll: true });
        if (window.matchMedia("(max-width: 700px)").matches) document.getElementById(id).scrollIntoView({ block: "start" });
      } else if (id === "plan-trace") {
        var trace = document.getElementById(id);
        trace.tabIndex = -1;
        trace.focus({ preventScroll: true });
        trace.scrollIntoView({ block: "start" });
      } else {
        document.getElementById("tab-" + tab).focus({ preventScroll: true });
        if (tablist.getBoundingClientRect().top < 0) tablist.scrollIntoView({ block: "start" });
      }
    }
  }
  function navigate(hash, focus) {
    if (window.location.hash !== hash) window.history.pushState(null, "", hash);
    route(hash, focus);
  }

  tablist.setAttribute("role", "tablist");
  tabs.forEach(function (tab) {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", tab.getAttribute("href").slice(1));
    tab.addEventListener("keydown", function (event) {
      var index = tabs.indexOf(tab), next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else if (event.key === " ") next = index;
      else return;
      event.preventDefault();
      navigate(tabs[next].getAttribute("href"), true);
    });
  });
  panels.forEach(function (panel) { panel.setAttribute("role", "tabpanel"); panel.tabIndex = 0; });
  root.addEventListener("click", function (event) {
    var link = event.target.closest('a[href^="#"]');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    navigate(link.getAttribute("href"), true);
  });
  filter.addEventListener("change", function () {
    search.value = "";
    filterOrders();
    navigate("#orders-" + filter.value, false);
  });
  search.addEventListener("input", filterOrders);
  if (blockerSearch) blockerSearch.addEventListener("input", function () {
    var term = blockerSearch.value.trim().toLowerCase();
    blockerPager.filter(function (choice, text) { return text.includes(term); });
    var matches = choices.filter(function (choice) { return choice.textContent.toLowerCase().includes(term); });
    if (matches.length) {
      var selected = matches.find(function (choice) { return choice.dataset.blockerChoice === activeBlocker; }) || matches[0];
      selectBlocker(selected.dataset.blockerChoice, false);
    } else {
      blockerPanels.forEach(function (panel) { panel.hidden = true; });
      choices.forEach(function (choice) { choice.setAttribute("aria-current", "false"); });
      root.querySelector("[data-blocker-detail-empty]").hidden = false;
    }
  });
  window.addEventListener("popstate", function () { route(window.location.hash, false); });
  window.addEventListener("hashchange", function () { route(window.location.hash, true); });
  root.querySelectorAll("[data-mobile-collapse]").forEach(function (detail) {
    var compact = window.matchMedia("(max-width: 700px)");
    detail.open = !compact.matches;
    compact.addEventListener("change", function () { detail.open = !compact.matches; });
  });
  if (!root.querySelector('[data-state="late"], [data-state="unplanned"], [data-incomplete="true"]')) filter.value = "all";
  filterOrders();
  selectBlocker(activeBlocker, false);
  root.classList.add("is-enhanced");
  route(window.location.hash, false);
})();
