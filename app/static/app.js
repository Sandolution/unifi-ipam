// UniFi IPAM: small page behaviours. No framework.
(function () {
  "use strict";

  // ---------------------------------------------------------------- IPv4 helpers
  function toInt(s) {
    var p = String(s).trim().split(".");
    if (p.length !== 4) return null;
    var n = 0;
    for (var i = 0; i < 4; i++) {
      if (!/^\d{1,3}$/.test(p[i])) return null;
      var o = Number(p[i]);
      if (o > 255) return null;
      n = n * 256 + o;
    }
    return n;
  }
  function toIp(n) {
    return [Math.floor(n / 16777216) % 256, Math.floor(n / 65536) % 256, Math.floor(n / 256) % 256, n % 256].join(".");
  }

  // ---------------------------------------------------------------- range form: start / end / size
  // Fill any two and the third follows. Start is the anchor you normally keep:
  //   editing Size  moves End (or Start, when there is no Start yet)
  //   editing End   recalculates Size (or Start, when there is no Start yet)
  //   editing Start moves End when Size was set after End, else recalculates Size
  function wireCalc(row) {
    var prefix = row.getAttribute("data-prefix") || "";
    var f = {
      start: row.querySelector('input[name="start_ip"]'),
      end: row.querySelector('input[name="end_ip"]'),
      size: row.querySelector('input[name="size"]')
    };
    if (!f.start || !f.end || !f.size) return;

    function addr(input) {
      var v = input.value.trim();
      if (!v) return null;
      if (prefix && v.indexOf(".") === -1) v = prefix + v;
      return toInt(v);
    }
    function show(n) {
      var ip = toIp(n);
      return prefix && ip.indexOf(prefix) === 0 ? ip.slice(prefix.length) : ip;
    }
    function size() {
      var v = f.size.value.trim();
      return /^\d+$/.test(v) && Number(v) > 0 ? Number(v) : null;
    }
    // Host bounds of the subnet; nothing is ever calculated outside them.
    var first = toInt(row.getAttribute("data-first") || "") , last = toInt(row.getAttribute("data-last") || "");
    if (first === null) first = 0;
    if (last === null) last = 4294967295;
    var bounds = "Must stay inside " + show(first) + "\u2013" + show(last);
    function inside(n) { return n >= first && n <= last; }

    var tick = 0;
    var touched = { start: 0, end: 0, size: 0 };  // when the user last typed in each field
    if (addr(f.start) !== null) touched.start = ++tick;
    if (addr(f.end) !== null) touched.end = ++tick;

    function put(target, value, why) {
      f[target].value = value;
      f[target].classList.add(value === "" ? "invalid" : "derived");
      if (value === "") f[target].title = why || "";
    }
    function sizeFrom(s, e) { put("size", e >= s ? String(e - s + 1) : "", "End is before Start"); }
    function endFrom(s, z) { put("end", inside(s + z - 1) ? show(s + z - 1) : "", bounds); }
    function startFrom(e, z) { put("start", inside(e - z + 1) ? show(e - z + 1) : "", bounds); }

    function derive(k) {
      ["start", "end", "size"].forEach(function (x) { f[x].classList.remove("derived", "invalid"); f[x].title = ""; });
      var s = addr(f.start), e = addr(f.end), z = size();
      // a typed address outside the subnet is flagged and never used to calculate
      if (s !== null && !inside(s)) { f.start.classList.add("invalid"); f.start.title = bounds; s = null; }
      if (e !== null && !inside(e)) { f.end.classList.add("invalid"); f.end.title = bounds; e = null; }
      if (k === "size") {
        if (s !== null && z !== null) endFrom(s, z);
        else if (e !== null && z !== null) startFrom(e, z);
      } else if (k === "end") {
        if (s !== null && e !== null) sizeFrom(s, e);
        else if (e !== null && z !== null) startFrom(e, z);
      } else {
        if (s !== null && z !== null && touched.size > touched.end) endFrom(s, z);
        else if (s !== null && e !== null) sizeFrom(s, e);
        else if (s !== null && z !== null) endFrom(s, z);
      }
    }

    ["start", "end", "size"].forEach(function (k) {
      f[k].addEventListener("input", function () {
        touched[k] = f[k].value.trim() ? ++tick : 0;
        derive(k);
      });
    });
    if (addr(f.start) !== null && addr(f.end) !== null && !f.size.value.trim()) derive("end");
  }

  // ---------------------------------------------------------------- copy a free address from the zoomed strip
  var toast;
  function say(text) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "toast";
      toast.setAttribute("role", "status");
      document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.classList.add("show");
    clearTimeout(say.t);
    if (document.documentElement.classList.contains("busy")) return;  // stays until the page leaves
    say.t = setTimeout(function () { toast.classList.remove("show"); }, 1600);
  }
  function wireCopy() {
    document.addEventListener("click", function (ev) {
      var cell = ev.target.closest(".zstrip [data-ip], .copyable[data-ip]");
      if (!cell) return;
      var ip = cell.getAttribute("data-ip");
      var done = function () { say("Copied " + ip); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(ip).then(done, function () { say(ip); });
      } else {
        say(ip);
      }
    });
  }

  // ---------------------------------------------------------------- selectable columns
  // Hidden columns are a class on the table, remembered per browser.
  function wireColumns(picker) {
    var table = document.getElementById(picker.getAttribute("data-cols-for"));
    if (!table) return;
    var key = "ipam.cols." + table.id;
    var hidden = [];
    try { hidden = JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { hidden = []; }
    var boxes = picker.querySelectorAll('input[type="checkbox"]');
    function apply() {
      Array.prototype.forEach.call(boxes, function (b) {
        table.classList.toggle("hide-" + b.value, !b.checked);
      });
    }
    Array.prototype.forEach.call(boxes, function (b) {
      b.checked = hidden.indexOf(b.value) === -1;
      b.addEventListener("change", function () {
        hidden = Array.prototype.filter.call(boxes, function (x) { return !x.checked; }).map(function (x) { return x.value; });
        try { localStorage.setItem(key, JSON.stringify(hidden)); } catch (e) { /* private window */ }
        apply();
      });
    });
    apply();
    document.addEventListener("click", function (ev) {
      if (picker.open && !picker.contains(ev.target)) picker.open = false;
    });
    picker.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { picker.open = false; picker.querySelector("summary").focus(); }
    });
  }

  // ---------------------------------------------------------------- busy state for slow actions
  // After a confirmed submit the page waits for UniFi; show that it is working.
  function wireBusy() {
    document.addEventListener("submit", function (ev) {
      if (ev.defaultPrevented) return;  // confirm() was cancelled
      var form = ev.target;
      var button = ev.submitter || form.querySelector("button");
      var label = (button && button.getAttribute("data-busy")) || form.getAttribute("data-busy");
      if (!label) return;
      if (form.getAttribute("data-sent")) { ev.preventDefault(); return; }  // no double submit
      form.setAttribute("data-sent", "1");
      if (button) {
        button.classList.add("is-busy");
        button.setAttribute("aria-disabled", "true");
        button.textContent = label;
      }
      var row = form.closest("tr");
      if (row) row.classList.add("is-busy");
      document.documentElement.classList.add("busy");
      say(label.replace("…", "") + (form.action.indexOf("/forget") !== -1 ? " in UniFi…" : "…"));
    });
    // Back/forward cache: never restore a page stuck in the busy state.
    window.addEventListener("pageshow", function (ev) {
      if (ev.persisted) document.documentElement.classList.remove("busy");
    });
  }

  // ---------------------------------------------------------------- banners
  // Confirmations fade after a few seconds; errors and warnings stay until closed.
  // The query parameter that produced the banner is removed, so a reload does not repeat it.
  var ONE_SHOT = ["saved", "forgot", "warn", "err"];
  function cleanUrl(search) {
    var kept = search.replace(/^\?/, "").split("&").filter(function (pair) {
      return pair && ONE_SHOT.indexOf(decodeURIComponent(pair.split("=")[0])) === -1;
    });
    return kept.length ? "?" + kept.join("&") : "";
  }
  function dismiss(el) {
    el.classList.add("leaving");
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 260);
  }
  function wireBanner(el) {
    if (el.getAttribute("data-wired")) return;
    el.setAttribute("data-wired", "1");
    var x = document.createElement("button");
    x.type = "button";
    x.className = "dismiss";
    x.setAttribute("aria-label", "Dismiss");
    x.textContent = "\u00d7";
    x.addEventListener("click", function () { dismiss(el); });
    el.appendChild(x);
    if (el.classList.contains("ok")) {
      var t = setTimeout(function () { dismiss(el); }, 6000);
      el.addEventListener("mouseenter", function () { clearTimeout(t); });
    }
  }
  // Messages from a save that reloads the page are carried across the reload.
  var STASH = "ipam.toasts";
  function stashToasts() {
    var box = document.getElementById("toasts");
    if (!box) return;
    var list = Array.prototype.map.call(box.querySelectorAll(".banner:not(.leaving)"), function (b) {
      var c = b.cloneNode(true);
      var x = c.querySelector(".dismiss");
      if (x) x.parentNode.removeChild(x);
      return { cls: b.className, html: c.innerHTML };
    });
    try { sessionStorage.setItem(STASH, JSON.stringify(list)); } catch (e) { /* private window */ }
  }
  function restoreToasts() {
    var raw = null;
    try { raw = sessionStorage.getItem(STASH); sessionStorage.removeItem(STASH); } catch (e) { return; }
    var box = document.getElementById("toasts");
    if (!raw || !box) return;
    JSON.parse(raw).forEach(function (t) {
      var d = document.createElement("div");
      d.className = t.cls;
      d.innerHTML = t.html;  // markup this app rendered moments ago, already escaped
      box.appendChild(d);
    });
  }
  function wireBanners() {
    restoreToasts();
    Array.prototype.forEach.call(document.querySelectorAll(".banner"), wireBanner);
    var clean = cleanUrl(location.search);
    if (clean !== location.search && window.history && history.replaceState) {
      history.replaceState(null, "", location.pathname + clean + location.hash);
    }
    // messages added later (saving a device row) arrive in the same stack
    var box = document.getElementById && document.getElementById("toasts");
    if (box && window.MutationObserver) {
      new MutationObserver(function (list) {
        list.forEach(function (m) {
          Array.prototype.forEach.call(m.addedNodes, function (n) {
            if (n.classList && n.classList.contains("banner")) wireBanner(n);
          });
        });
      }).observe(box, { childList: true });
    }
  }


  // ---------------------------------------------------------------- whole range rows zoom
  // A click anywhere on a range row zooms in, or back out when it is already zoomed.
  // Links, buttons, inputs and the copyable next-free address keep their own behaviour.
  function wireRowZoom() {
    document.addEventListener("click", function (ev) {
      var row = ev.target.closest("tr[data-href]");
      if (!row) return;
      if (ev.target.closest("a, button, input, select, textarea, label, form, .copyable")) return;
      var sel = window.getSelection ? String(window.getSelection()) : "";
      if (sel) return;  // the user is selecting text, not navigating
      location.href = row.getAttribute("data-href");
    });
  }

  // ---------------------------------------------------------------- device row editing keys
  function wireEditKeys() {
    document.addEventListener("keydown", function (ev) {
      var row = ev.target.closest && ev.target.closest("tr.editing");
      if (!row || ev.target.tagName !== "INPUT") return;
      var target = ev.key === "Enter" ? "[data-save]" : ev.key === "Escape" ? "[data-cancel]" : null;
      if (!target) return;
      ev.preventDefault();
      var btn = row.querySelector(target);
      if (btn) btn.click();
    });
  }

  // ---------------------------------------------------------------- collapsible help, remembered
  function wireHelp() {
    Array.prototype.forEach.call(document.querySelectorAll("details[data-remember]"), function (d) {
      var key = "ipam.open." + d.getAttribute("data-remember");
      try { if (localStorage.getItem(key) === "1") d.open = true; } catch (e) { /* private window */ }
      d.addEventListener("toggle", function () {
        try { localStorage.setItem(key, d.open ? "1" : "0"); } catch (e) { /* private window */ }
      });
    });
  }

  // ---------------------------------------------------------------- saving a device row
  // Changing or removing a fixed IP asks first; while saving, the page shows it is busy.
  function wireDeviceSave() {
    document.addEventListener("htmx:confirm", function (ev) {
      var btn = ev.detail && ev.detail.elt;
      if (!btn || !btn.matches || !btn.matches("[data-save]")) return;
      var input = btn.closest("tr").querySelector('input[name="fixed_ip"]');
      if (!input) return;
      var now = input.value.trim(), was = input.getAttribute("data-orig") || "";
      if (now === was) return;
      ev.preventDefault();
      var prefix = input.getAttribute("data-prefix") || "";
      var full = now.indexOf(".") === -1 ? prefix + now : now;
      var msg = now
        ? "Set the fixed IP in UniFi to " + full + "? The device moves there when it renews its lease or reconnects."
        : "Remove the fixed IP reservation in UniFi?";
      if (window.confirm(msg)) ev.detail.issueRequest(true);
    });
    // While a row is in edit mode the table keeps its column widths and the row keeps its
    // height, so nothing around the fields moves. Released when no row is being edited.
    var heights = {};
    function freeze(table) {
      if (!table || table.getAttribute("data-frozen")) return;
      var ths = table.querySelectorAll("thead th");
      var widths = Array.prototype.map.call(ths, function (th) { return th.getBoundingClientRect().width; });
      Array.prototype.forEach.call(ths, function (th, i) { th.style.width = widths[i] + "px"; });
      table.style.tableLayout = "fixed";
      table.setAttribute("data-frozen", "1");
    }
    function thaw(table) {
      if (!table || !table.getAttribute("data-frozen") || table.querySelector("tr.editing")) return;
      Array.prototype.forEach.call(table.querySelectorAll("thead th"), function (th) { th.style.width = ""; });
      table.style.tableLayout = "";
      table.removeAttribute("data-frozen");
    }
    document.addEventListener("htmx:beforeRequest", function (ev) {
      var elt = ev.detail && ev.detail.elt;
      if (elt && elt.matches && elt.matches('[hx-get*="/edit"]')) {
        var row = elt.closest("tr");
        heights[row.id] = row.getBoundingClientRect().height;
        freeze(elt.closest("table"));
      }
    });
    document.addEventListener("htmx:afterSettle", function () {
      Array.prototype.forEach.call(document.querySelectorAll("tr.editing"), function (row) {
        if (heights[row.id]) row.style.height = heights[row.id] + "px";
      });
      thaw(document.getElementById("devices"));
    });
    // A successful save reloads the page, so grouping, counts, the strip and the checks
    // badge all reflect the change. A failed save stays in edit mode with its message.
    var savingRow = null;
    document.addEventListener("htmx:afterSettle", function () {
      if (!savingRow) return;
      var row = document.getElementById(savingRow);
      savingRow = null;
      if (row && !row.classList.contains("editing")) {
        stashToasts();
        document.documentElement.classList.add("busy");
        location.reload();
      }
    });
    document.addEventListener("htmx:beforeRequest", function (ev) {
      if (ev.detail && ev.detail.elt && ev.detail.elt.matches && ev.detail.elt.matches("[data-save]")) {
        savingRow = ev.detail.elt.closest("tr").id;
        document.documentElement.classList.add("busy");
        ev.detail.elt.textContent = "Saving\u2026";
      }
    });
    document.addEventListener("htmx:afterRequest", function () {
      document.documentElement.classList.remove("busy");
    });
  }

  // ---------------------------------------------------------------- refresh on return
  // Coming back to a tab that was hidden for a while reloads it with current data, unless
  // something is in progress that a reload would throw away.
  var MIN_AWAY_MS = 30000;
  function inProgress() {
    if (document.body.hasAttribute("data-no-renew")) return true;  // form post with errors
    if (document.documentElement.classList.contains("busy")) return true;
    if (document.querySelector("tr.editing, details.cols[open]")) return true;
    if (window.getSelection && String(window.getSelection())) return true;
    return Array.prototype.some.call(document.querySelectorAll("input, textarea"), function (el) {
      if (el.type === "hidden" || el.type === "radio" || el.type === "checkbox") return false;
      return el.value !== el.defaultValue;  // typed but not saved
    });
  }
  function renew() {
    if (!inProgress()) location.reload();
  }
  // A visible page checks in with the app about once a minute. With no check-ins the
  // background sync pauses; the first check-in after that syncs and answers resumed: true.
  var CHECK_IN_MS = 60000;
  function checkIn() {
    if (typeof fetch !== "function") return Promise.resolve({});
    return fetch("/presence", { method: "POST", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; });
  }
  function wireResume() {
    var hiddenAt = null;
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") { hiddenAt = Date.now(); return; }
      var away = hiddenAt === null ? 0 : Date.now() - hiddenAt;
      hiddenAt = null;
      return checkIn().then(function (res) {
        if (away >= MIN_AWAY_MS || res.resumed) renew();  // fresh data after a pause
      });
    });
    window.addEventListener("pageshow", function (ev) {
      if (ev.persisted) checkIn().then(renew);  // restored from the back/forward cache
    });
    if (document.visibilityState !== "hidden") {
      checkIn().then(function (res) { if (res.resumed) renew(); });  // opened after a pause
    }
    setInterval(function () {
      if (document.visibilityState === "visible") checkIn();
    }, CHECK_IN_MS);
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireBusy();
    wireResume();
    wireDeviceSave();
    wireHelp();
    wireEditKeys();
    wireRowZoom();
    wireBanners();
    Array.prototype.forEach.call(document.querySelectorAll("tr[data-calc]"), wireCalc);
    Array.prototype.forEach.call(document.querySelectorAll("details[data-cols-for]"), wireColumns);
    wireCopy();
  });
})();
