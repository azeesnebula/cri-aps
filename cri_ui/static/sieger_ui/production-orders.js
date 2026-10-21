/* Filtering is a normal GET request; the server fetches only the current page. */
(function () {
  "use strict";
  var form = document.getElementById("production-order-filters");
  if (!form) return;
  form.querySelectorAll("select").forEach(function (select) {
    select.addEventListener("change", function () { form.requestSubmit(); });
  });
  form.addEventListener("submit", function () {
    form.setAttribute("aria-busy", "true");
    var button = form.querySelector('button[type="submit"]');
    button.textContent = "Loading…";
    button.disabled = true;
  });
  window.addEventListener("pageshow", function () {
    form.removeAttribute("aria-busy");
    var button = form.querySelector('button[type="submit"]');
    button.textContent = "Apply filters";
    button.disabled = false;
  });
})();
