/* Cafeteria PWA - application logic */
(function () {
  "use strict";

  var TOKEN_KEY = "cafetaria_token";

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  var state = {
    token: localStorage.getItem(TOKEN_KEY) || null,
    username: null,
    reservations: [],
    credit: null,
    status: null,
    busyDates: {},
  };

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  function $(id) { return document.getElementById(id); }

  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  var toastTimer = null;
  function toast(message, kind) {
    var el = $("toast");
    el.textContent = message;
    el.className = "toast" + (kind ? " " + kind : "");
    show(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { hide(el); }, 3200);
  }

  function api(path, options) {
    options = options || {};
    var headers = { "Content-Type": "application/json" };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    return fetch("/api" + path, Object.assign({}, options, {
      headers: Object.assign(headers, options.headers || {}),
    })).then(function (res) {
      if (res.status === 401 && path !== "/login") {
        logoutLocal();
        throw new Error("Session expired");
      }
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function formatDateParts(dateStr) {
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var dows = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var d = new Date(dateStr + "T12:00:00");
    return {
      dow: dows[d.getDay()],
      dom: String(d.getDate()),
      mon: months[d.getMonth()],
      full: d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }),
      date: d,
    };
  }

  function todayMidnight() {
    var n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate());
  }

  // ------------------------------------------------------------------
  // Views
  // ------------------------------------------------------------------
  function showLogin() {
    show($("view-login"));
    hide($("view-main"));
  }

  function showMain() {
    hide($("view-login"));
    show($("view-main"));
  }

  function logoutLocal() {
    state.token = null;
    state.username = null;
    localStorage.removeItem(TOKEN_KEY);
    document.cookie = "cafetaria_token=; Max-Age=0; path=/";
    showLogin();
  }

  // ------------------------------------------------------------------
  // Auth
  // ------------------------------------------------------------------
  $("login-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var btn = $("login-submit");
    var errEl = $("login-error");
    hide(errEl);
    btn.disabled = true;
    btn.textContent = "Signing in…";

    api("/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      }),
    }).then(function (res) {
      if (!res.ok) throw new Error(res.data.detail || "Sign in failed");
      state.token = res.data.token;
      state.username = res.data.username;
      localStorage.setItem(TOKEN_KEY, state.token);
      $("login-password").value = "";
      showMain();
      refreshData();
    }).catch(function (err) {
      errEl.textContent = err.message || "Sign in failed";
      show(errEl);
    }).finally(function () {
      btn.disabled = false;
      btn.textContent = "Sign in";
    });
  });

  $("btn-logout").addEventListener("click", function () {
    api("/logout", { method: "POST" }).catch(function () {});
    toast("Signed out", "success");
    setTimeout(logoutLocal, 150);
  });

  // ------------------------------------------------------------------
  // Data loading
  // ------------------------------------------------------------------
  function refreshData(forceSync) {
    setStatusLine("Refreshing…");

    var load = Promise.all([
      api("/reservations"),
      api("/credit"),
      api("/status"),
    ]);

    if (forceSync) {
      load = api("/sync", { method: "POST" }).then(function () { return load; })
        .catch(function () { return load; });
    }

    return load.then(function (results) {
      if (results[0].ok) state.reservations = results[0].data || [];
      if (results[1].ok) state.credit = results[1].data.credit;
      if (results[2].ok) state.status = results[2].data;
      render();
    }).catch(function (err) {
      if (navigator.onLine === false) {
        render(); // render whatever we have
        setStatusLine("Offline");
      } else {
        setStatusLine("Update failed");
        toast(err.message || "Could not load data", "error");
      }
    });
  }

  function setStatusLine(text) {
    $("status-line").textContent = text;
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------
  function render() {
    renderCredit();
    renderWarning();
    renderNextReservation();
    renderReservations();

    var last = state.status && state.status.last_update
      ? new Date(state.status.last_update)
      : null;
    setStatusLine(last
      ? "Updated " + last.toLocaleDateString() + " " + last.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "Not synchronized yet");
  }

  function renderCredit() {
    var el = $("credit-value");
    el.textContent = state.credit ? state.credit.replace(/\s+/g, " ").trim() : "Unknown";
  }

  function renderWarning() {
    var banner = $("warning-banner");
    var warning = state.status && state.status.low_credit_warning;
    if (warning) {
      banner.textContent = "⚠ " + warning;
      show(banner);
    } else {
      hide(banner);
    }
  }

  function renderNextReservation() {
    var card = $("next-reservation");
    var next = state.reservations.find(function (r) { return r.reserved; });
    if (next) {
      $("next-reservation-date").textContent =
        formatDateParts(next.date).full;
      show(card);
    } else {
      hide(card);
    }
  }

  function renderReservations() {
    var list = $("reservation-list");
    list.innerHTML = "";
    var today = todayMidnight();

    if (!state.reservations.length) {
      show($("list-empty"));
      return;
    }
    hide($("list-empty"));

    state.reservations.forEach(function (r) {
      var parts = formatDateParts(r.date);
      var isPast = parts.date < today;

      var li = document.createElement("li");
      li.className = "reservation-item " +
        (r.reserved ? "reserved" : "available") +
        (isPast ? " past" : "");
      li.setAttribute("data-date", r.date);

      li.innerHTML =
        '<div class="day-box">' +
        '<span class="dow">' + parts.dow + '</span>' +
        '<span class="dom">' + parts.dom + '</span>' +
        '<span class="mon">' + parts.mon + '</span>' +
        "</div>" +
        '<div class="reservation-info">' +
        '<span class="reservation-date"></span>' +
        '<span class="reservation-state ' + (r.reserved ? "reserved-state" : "") + '">' +
        (isPast ? (r.reserved ? "Reserved (past)" : "Not reserved") :
          r.reserved ? "Meal reserved" : "Not reserved") +
        "</span></div>";

      li.querySelector(".reservation-date").textContent = parts.full;

      if (!isPast) {
        var btn = document.createElement("button");
        btn.className = "btn " + (r.reserved ? "btn-cancel" : "btn-reserve");
        btn.textContent = r.reserved ? "Cancel" : "Reserve";
        btn.addEventListener("click", function () { onToggleReservation(r, btn); });
        li.appendChild(btn);
      }

      list.appendChild(li);
    });
  }

  // ------------------------------------------------------------------
  // Reservation actions
  // ------------------------------------------------------------------
  function onToggleReservation(reservation, btn) {
    var dateStr = reservation.date;
    if (state.busyDates[dateStr]) return;
    state.busyDates[dateStr] = true;

    var wasCancel = reservation.reserved;
    btn.disabled = true;
    btn.innerHTML = '<span class="spin">⟳</span>';

    api("/reservations/" + dateStr, { method: wasCancel ? "DELETE" : "POST" })
      .then(function (res) {
        var msg = (res.data && res.data.message) || "Done";
        toast(msg, res.ok ? "success" : "error");
        return refreshData(true);
      })
      .catch(function (err) {
        toast(err.message || "Action failed", "error");
        btn.disabled = false;
        btn.textContent = wasCancel ? "Cancel" : "Reserve";
        delete state.busyDates[dateStr];
      });
  }

  // ------------------------------------------------------------------
  // Refresh / connectivity
  // ------------------------------------------------------------------
  $("btn-refresh").addEventListener("click", function () {
    var btn = $("btn-refresh");
    btn.classList.add("spin");
    refreshData(true).finally(function () {
      btn.classList.remove("spin");
    });
  });

  window.addEventListener("online", function () {
    hide($("offline-banner"));
    if (state.token) refreshData();
  });

  window.addEventListener("offline", function () {
    show($("offline-banner"));
  });

  // ------------------------------------------------------------------
  // Install prompt (Android / Chrome)
  // ------------------------------------------------------------------
  var deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", function (ev) {
    ev.preventDefault();
    deferredPrompt = ev;
    if (!localStorage.getItem("cafetaria_install_dismissed")) {
      show($("install-banner"));
    }
  });

  $("btn-install").addEventListener("click", function () {
    hide($("install-banner"));
    if (deferredPrompt) {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
    }
  });

  $("btn-install-dismiss").addEventListener("click", function () {
    hide($("install-banner"));
    localStorage.setItem("cafetaria_install_dismissed", "1");
  });

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------
  (function boot() {
    if (!state.token) {
      showLogin();
      return;
    }
    api("/me").then(function (res) {
      if (!res.ok) throw new Error("unauthenticated");
      state.username = res.data.username;
      showMain();
      refreshData();
    }).catch(function () {
      logoutLocal();
    });
  })();
})();
