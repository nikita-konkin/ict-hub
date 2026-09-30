(function () {
  var modal = document.getElementById("feedback-modal");
  var form = modal ? modal.querySelector("form[action='/feedback']") : null;
  var result = modal ? modal.querySelector("#feedback-result") : null;
  var toastRoot = document.getElementById("feedback-toast-root");
  if (!toastRoot) {
    toastRoot = document.createElement("div");
    toastRoot.id = "feedback-toast-root";
    toastRoot.className = "toast-root";
    document.body.appendChild(toastRoot);
  }

  function toast(text, kind) {
    var node = document.createElement("div");
    node.className = "toast " + (kind === "error" ? "toast-error" : "toast-ok");
    node.textContent = String(text || "");
    toastRoot.appendChild(node);
    setTimeout(function () {
      try { node.classList.add("hide"); } catch (_) {}
      setTimeout(function () { try { node.remove(); } catch (_) {} }, 250);
    }, 3500);
  }

  function openModal() {
    if (!modal) return;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    try {
      var textarea = modal.querySelector("textarea[name='message']");
      if (textarea) textarea.focus();
    } catch (_) {}
    try {
      var page = modal.querySelector("input[name='page_url']");
      if (page) page.value = window.location.href;
    } catch (_) {}
  }
  function closeModal() {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }
  window.__chOpenFeedback = openModal;
  window.__chCloseFeedback = closeModal;
  document.addEventListener("keydown", function (e) {
    if (!modal || !modal.classList.contains("open")) return;
    if (e.key === "Escape") closeModal();
  });

  if (form && window.fetch) {
    form.addEventListener("submit", function (e) {
      // Prefer an in-page popup notification even if HTMX isn't available.
      e.preventDefault();
      if (result) result.innerHTML = "";

      var submitBtn = form.querySelector("button[type='submit']");
      if (submitBtn) submitBtn.disabled = true;

      var data = new FormData(form);
      fetch("/feedback", {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
      })
        .then(function (r) {
          return r.json().then(function (payload) {
            return { ok: r.ok, payload: payload || {} };
          });
        })
        .then(function (res) {
          if (res.ok && res.payload && res.payload.ok) {
            toast(res.payload.message || "Sent.", "ok");
            try {
              var textarea = form.querySelector("textarea[name='message']");
              if (textarea) textarea.value = "";
            } catch (_) {}
            closeModal();
            return;
          }
          var err = (res.payload && (res.payload.error || res.payload.message)) || "Failed to send feedback.";
          toast(err, "error");
          if (result) {
            result.innerHTML = '<div class="alert alert-danger">' + String(err) + '</div>';
          }
        })
        .catch(function () {
          toast("Failed to send feedback.", "error");
          if (result) result.innerHTML = '<div class="alert alert-danger">Failed to send feedback.</div>';
        })
        .finally(function () {
          if (submitBtn) submitBtn.disabled = false;
        });
    });
  }
})();
