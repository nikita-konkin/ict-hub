/* Shared page behaviour: dates, job panels, theme, language, indexer status. */
(function () {
  // Values rendered by base.html (language, translated labels).
  const APP_CONFIG = JSON.parse(document.getElementById("app-config").textContent);

  // ── Date formatting ────────────────────────────────────────────────────
  function normaliseDateInput(rawValue) {
    if (!rawValue) return "";
    const value = String(rawValue).trim();
    if (!value) return "";
    if (/Z$|[+-]\d{2}:?\d{2}$/.test(value)) return value;
    return value + "Z";
  }

  function formatLocalDateTimes(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-local-datetime]").forEach(function (node) {
      const raw = node.getAttribute("data-local-datetime");
      const parsed = new Date(normaliseDateInput(raw));
      if (Number.isNaN(parsed.getTime())) return;
      node.textContent = parsed.toLocaleString(undefined, {
        day: "2-digit", month: "short",
        hour: "2-digit", minute: "2-digit", hour12: false,
      });
    });
  }

  // ── Panel lifecycle ────────────────────────────────────────────────────

  /**
   * Close this panel's EventSource and remove it from the DOM.
   * If #job-output has no remaining panels, restore the placeholder.
   */
  function removePanel(panel) {
    if (!panel) return;
    const jobId = panel.dataset && panel.dataset.jobId ? panel.dataset.jobId : "";
    try { if (panel._sseSource) panel._sseSource.close(); } catch (_) {}
    const outputRoot = panel.closest("#job-output");
    panel.remove();
    if (jobId) {
      const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
      const isRunning = row && row.getAttribute("data-recent-job-running") === "1";
      if (isRunning) {
        startRecentRunTracker(jobId);
      }
    }
    if (outputRoot && !outputRoot.querySelector('[id^="job-panel-"]')) {
      // Restore placeholder — unhide existing one or clone from template
      const existing = outputRoot.querySelector(".job-output-placeholder");
      if (existing) {
        existing.style.display = "";
      } else {
        const tpl = document.getElementById("job-output-placeholder-template");
        if (tpl && "content" in tpl) {
          outputRoot.appendChild(tpl.content.cloneNode(true));
        }
      }
    }
  }

  // ── Recent-runs helpers ────────────────────────────────────────────────

  function updateRecentRunStatus(jobId, isSuccess) {
    const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
    if (!row) return;
    row.setAttribute("data-recent-job-running", "0");
    const badge = row.querySelector("[data-recent-job-status]");
    if (badge) {
      badge.className = isSuccess ? "badge badge-success" : "badge badge-danger";
      badge.textContent = isSuccess ? "success" : "failed";
    }
    const pct = row.querySelector("[data-recent-job-progress]");
    if (pct) pct.setAttribute("hidden", "");
  }

  function updateRecentRunProgress(jobId, pct) {
    const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
    if (!row) return;
    row.setAttribute("data-recent-job-running", "1");
    const span = row.querySelector("[data-recent-job-progress]");
    if (span) {
      span.removeAttribute("hidden");
      span.textContent = pct + "%";
    }
  }

  /**
   * Add a live row to #recent-runs-body for a job that was just started
   * (it won't be in the server-rendered list yet).
   */
  function ensureRecentRunRow(jobId, startedAtIso) {
    const body = document.getElementById("recent-runs-body");
    if (!body) return;
    if (document.querySelector('[data-recent-job-id="' + jobId + '"]')) return;

    // Remove "no recent runs" empty message
    const empty = body.querySelector(".recent-runs-empty");
    if (empty) empty.remove();

    let timeText = "just now";
    const normalised = startedAtIso
      ? (/Z$|[+-]\d{2}:?\d{2}$/.test(startedAtIso) ? startedAtIso : startedAtIso + "Z")
      : "";
    if (normalised) {
      const d = new Date(normalised);
      if (!isNaN(d.getTime())) {
        timeText = d.toLocaleString(undefined, {
          day: "2-digit", month: "short",
          hour: "2-digit", minute: "2-digit", hour12: false,
        });
      }
    }

    const row = document.createElement("div");
    row.setAttribute("data-recent-job-id", jobId);
    row.setAttribute("data-recent-job-running", "1");
    row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted);";
    row.innerHTML =
      '<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-dim);">#' + jobId + '</span>' +
      '<span data-local-datetime="' + (startedAtIso || "") + '">' + timeText + '</span>' +
      '<span class="badge badge-running" data-recent-job-status>running</span>' +
      '<span data-recent-job-progress style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);">—</span>' +
      '<a href="' + window.location.pathname + '?job_id=' + jobId + '" class="btn btn-ghost" style="padding:4px 10px;font-size:10px;">Open</a>';
    body.insertAdjacentElement("afterbegin", row);
  }

  const recentRunTrackers = new Map();

  function stopRecentRunTracker(jobId) {
    const existing = recentRunTrackers.get(jobId);
    if (!existing) return;
    try { existing.close(); } catch (_) {}
    recentRunTrackers.delete(jobId);
  }

  function startRecentRunTracker(jobId) {
    if (!jobId || recentRunTrackers.has(jobId)) return;
    if (document.querySelector('#job-panel-' + jobId)) return;

    let source;
    try {
      source = new EventSource('/jobs/' + jobId + '/stream');
    } catch (_) {
      return;
    }
    recentRunTrackers.set(jobId, source);

    source.addEventListener('progress', function (event) {
      const pct = parseInt(event.data, 10);
      if (!Number.isNaN(pct)) {
        updateRecentRunProgress(jobId, pct);
      }
    });

    source.addEventListener('done', function (event) {
      const isSuccess = typeof event.data === 'string' && event.data.indexOf('badge-success') !== -1;
      updateRecentRunStatus(jobId, isSuccess);
      stopRecentRunTracker(jobId);
    });

    source.addEventListener('error', function () {
      // Reconnect tracker for running jobs after transient SSE errors.
      const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
      const isRunning = row && row.getAttribute('data-recent-job-running') === '1';
      stopRecentRunTracker(jobId);
      if (isRunning && !document.querySelector('#job-panel-' + jobId)) {
        setTimeout(function () { startRecentRunTracker(jobId); }, 800);
      }
    });
  }

  function initializeRecentRunTrackers(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-recent-job-id]').forEach(function (row) {
      const jobId = row.getAttribute('data-recent-job-id');
      const runningAttr = row.getAttribute('data-recent-job-running') === '1';
      const statusEl = row.querySelector('[data-recent-job-status]');
      const statusText = statusEl ? statusEl.textContent.trim().toLowerCase() : '';
      if (runningAttr || statusText === 'running') {
        startRecentRunTracker(jobId);
      }
    });
  }

  // ── Theme toggle ───────────────────────────────────────────────────────
  const THEME_STORAGE_KEY = 'converterhub-theme';
  const LANG_STORAGE_KEY = 'converterhub-lang';

  function setTheme(theme) {
    const root = document.documentElement;
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    root.setAttribute('data-theme', nextTheme);
    try { localStorage.setItem(THEME_STORAGE_KEY, nextTheme); } catch (_) {}

    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
      const darkLabel = APP_CONFIG.themeDarkLabel;
      const lightLabel = APP_CONFIG.themeLightLabel;
      const switchTo = nextTheme === 'light' ? darkLabel : lightLabel;
      toggle.textContent = switchTo;
      toggle.setAttribute('aria-label', 'Switch to ' + switchTo.toLowerCase());
    }
  }

  function initializeTheme() {
    let savedTheme = '';
    try { savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || ''; } catch (_) {}
    setTheme(savedTheme === 'light' ? 'light' : 'dark');

    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    toggle.addEventListener('click', function () {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      setTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  function initializeLanguage() {
    const langSelect = document.getElementById('lang-toggle');
    if (!langSelect) return;

    const currentLang = APP_CONFIG.lang;
    try {
      const stored = localStorage.getItem(LANG_STORAGE_KEY);
      if (stored && stored !== currentLang && (stored === 'en' || stored === 'ru')) {
        const url = new URL(window.location.href);
        url.searchParams.set('lang', stored);
        window.location.replace(url.toString());
        return;
      }
    } catch (_) {}

    langSelect.addEventListener('change', function () {
      const selected = langSelect.value === 'ru' ? 'ru' : 'en';
      try { localStorage.setItem(LANG_STORAGE_KEY, selected); } catch (_) {}
      const url = new URL(window.location.href);
      url.searchParams.set('lang', selected);
      window.location.assign(url.toString());
    });
  }

  // ── Core panel initialiser ─────────────────────────────────────────────

  function initializeJobPanel(panel) {
    if (!panel || panel.dataset.sseInit === "1") return;
    const isRunning = panel.dataset.jobRunning === "1";
    if (!isRunning) {
      panel.dataset.sseInit = "1";
      return;
    }
    const streamUrl = panel.dataset.streamUrl;
    if (!streamUrl) return;

    panel.dataset.sseInit = "1";

    // Each panel uses IDs scoped to its job so multiple panels never clash
    const jobId = panel.dataset.jobId || panel.id.replace("job-panel-", "");
    const statusEl      = panel.querySelector("#job-status-"        + jobId);
    const progressLabel = panel.querySelector("#progress-label-"    + jobId);
    const progressFill  = panel.querySelector("#progress-fill-wrap-"+ jobId);
    const logLines      = panel.querySelector("#log-lines-"         + jobId);

    // Hide the placeholder while this panel is active
    const outputRoot = panel.parentElement;
    if (outputRoot && outputRoot.id === "job-output") {
      outputRoot.querySelectorAll(".job-output-placeholder").forEach(function (el) {
        el.style.display = "none";
      });
    }

    // Register this job in the Recent runs list (for freshly submitted jobs)
    ensureRecentRunRow(jobId, panel.dataset.startedAt);
    // Panel stream is the source of truth while open.
    stopRecentRunTracker(jobId);

    function appendLog(html) {
      if (!logLines) return;
      logLines.insertAdjacentHTML("beforeend", html);
      logLines.scrollTop = logLines.scrollHeight;
    }

    let source;
    try {
      source = new EventSource(streamUrl);
      panel._sseSource = source;
    } catch (err) {
      appendLog('<span class="log-line log-line-error">Failed to open log stream.</span>');
      return;
    }

    source.addEventListener("log", function (event) {
      appendLog(event.data);
    });

    source.addEventListener("progress", function (event) {
      const pct = parseInt(event.data, 10);
      if (Number.isNaN(pct)) return;
      if (progressLabel) progressLabel.textContent = pct + "%";
      if (progressFill) {
        progressFill.innerHTML = '<div class="progress-fill" style="width:' + pct + '%"></div>';
      }
      // Mirror progress into the Recent runs row
      updateRecentRunProgress(jobId, pct);
    });

    source.addEventListener("error", function (event) {
      const msg = event && typeof event.data === "string" ? event.data : "Stream error";
      if (statusEl) statusEl.innerHTML = '<span class="badge badge-danger">Error</span>';
      appendLog('<span class="log-line log-line-error">' + msg + '</span>');
      updateRecentRunStatus(jobId, false);
      source.close();
    });

    source.addEventListener("done", function (event) {
      const isSuccess = typeof event.data === "string" && event.data.indexOf("badge-success") !== -1;

      if (statusEl) statusEl.innerHTML = event.data;
      if (isSuccess && progressFill) {
        progressFill.innerHTML = '<div class="progress-fill" style="width:100%"></div>';
      }
      if (isSuccess && progressLabel) progressLabel.textContent = "100%";

      updateRecentRunStatus(jobId, isSuccess);
      source.close();

      // Strip ?job_id= from the URL if present
      try {
        const url = new URL(window.location.href);
        if (url.searchParams.has("job_id")) {
          url.searchParams.delete("job_id");
          window.history.replaceState({}, "", url.toString());
        }
      } catch (_) {}
    });
  }

  function initializeAllJobPanels(root) {
    const scope = root || document;
    scope.querySelectorAll('[id^="job-panel-"][data-stream-url]').forEach(initializeJobPanel);
  }

  // ── Public API ─────────────────────────────────────────────────────────
  window.dismissJobPanel      = function (panel) { removePanel(panel); };
  window.initializeAllJobPanels = initializeAllJobPanels;

  // ── Hooks ──────────────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    formatLocalDateTimes(document);
    initializeAllJobPanels(document);
    initializeRecentRunTrackers(document);
    initializeTheme();
    initializeLanguage();

    // Initialize data indexer status
    initializeDataIndexerStatus();
  });

  // HTMX leaves 4xx/5xx responses unswapped by default, but the job endpoints
  // answer errors with an alert fragment meant to be shown in the target.
  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    const xhr = evt.detail.xhr;
    const contentType = xhr.getResponseHeader("Content-Type") || "";
    if (xhr.status >= 400 && contentType.startsWith("text/html")) {
      evt.detail.shouldSwap = true;
      evt.detail.isError = false;
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    formatLocalDateTimes(evt.target || document);
    initializeAllJobPanels(evt.target || document);
    initializeRecentRunTrackers(evt.target || document);
  });

  // ── Data Indexer Status ───────────────────────────────────────────────
  function initializeDataIndexerStatus() {
    const statusDot = document.getElementById('status-dot');
    const rinexCount = document.getElementById('rinex-count');
    const tecsuiteCount = document.getElementById('tecsuite-count');
    const parquetCount = document.getElementById('parquet-count');

    if (!statusDot || !rinexCount || !tecsuiteCount || !parquetCount) return;

    async function updateStatus() {
      try {
        const response = await fetch('/api/data-indexer/status');
        if (response.ok) {
          const data = await response.json();
          statusDot.classList.add('active');
          rinexCount.textContent = data.cache_info.rinex.entries;
          tecsuiteCount.textContent = data.cache_info.tecsuite.entries;
          parquetCount.textContent = data.cache_info.parquet.entries;
        } else {
          statusDot.classList.remove('active');
          rinexCount.textContent = '-';
          tecsuiteCount.textContent = '-';
          parquetCount.textContent = '-';
        }
      } catch (error) {
        statusDot.classList.remove('active');
        rinexCount.textContent = '-';
        tecsuiteCount.textContent = '-';
        parquetCount.textContent = '-';
      }
    }

    // Update status immediately and then every 30 seconds
    updateStatus();
    setInterval(updateStatus, 30000);
  }
})();
