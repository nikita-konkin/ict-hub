(function () {
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

  function updateRecentRunStatus(jobId, isSuccess, finishedAtIso) {
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

    const finishedNode = row.querySelector(".recent-finished-at");
    if (finishedNode && finishedAtIso) {
      const value = finishedAtIso;
      finishedNode.setAttribute("data-local-datetime", value);
      finishedNode.textContent = value;
      formatLocalDateTimes(row);
    }
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
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function ensureRecentRunRow(jobId, startedAtIso, dataRange) {
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
    row.setAttribute("data-recent-job-range", dataRange || "");
    row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted);";
    const safeRange = dataRange ? escapeHtml(dataRange) : "—";
    row.innerHTML =
      '<span style="font-family:var(--font-mono);font-size:11px;color:var(--text-dim);">#' + jobId + '</span>' +
      '<div style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;">' +
        '<div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;">' +
          '<span style="white-space:nowrap;color:var(--text-dim);">Start: <span class="recent-started-at" data-local-datetime="' + (startedAtIso || "") + '">' + timeText + '</span></span>' +
          '<span style="white-space:nowrap;color:var(--text-dim);">Stop: <span class="recent-finished-at">—</span></span>' +
        '</div>' +
        '<div class="td-mono" style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + safeRange + '">' +
          'Range: ' + safeRange +
        '</div>' +
      '</div>' +
      '<span class="badge badge-running" data-recent-job-status>running</span>' +
      '<span data-recent-job-progress style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);">—</span>' +
      '<a href="/jobs/' + jobId + '/open" class="btn btn-ghost" style="padding:4px 10px;font-size:10px;">Open</a>';
    body.insertAdjacentElement("afterbegin", row);
    formatLocalDateTimes(row);
  }

  const recentRunTrackers = new Map();

  function stopRecentRunTracker(jobId) {
    const existing = recentRunTrackers.get(jobId);
    if (!existing) return;
    try { existing.close(); } catch (_) {}
    recentRunTrackers.delete(jobId);
  }

  function startRecentRunTracker(jobId, afterEventId) {
    if (!jobId || recentRunTrackers.has(jobId)) return;
    if (document.querySelector('#job-panel-' + jobId)) return;

    let source;
    let lastEventId = afterEventId || "";
    try {
      source = new EventSource(buildSseUrl('/jobs/' + jobId + '/stream', lastEventId), {
        withCredentials: true,
      });
    } catch (_) {
      return;
    }
    recentRunTrackers.set(jobId, source);

    source.addEventListener('progress', function (event) {
      if (event.lastEventId) {
        lastEventId = event.lastEventId;
      }
      const payload = parseSseXml(event.data);
      const pct = readXmlInt(payload, 'value');
      if (pct !== null) {
        const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
        if (row && lastEventId) row.setAttribute('data-recent-last-event-id', lastEventId);
        updateRecentRunProgress(jobId, pct);
      }
    });

    source.addEventListener('done', function (event) {
      if (event.lastEventId) {
        lastEventId = event.lastEventId;
      }
      const payload = parseSseXml(event.data);
      const isSuccess = readXmlText(payload, 'status') === 'success';
      const finishedAtIso = readXmlText(payload, 'finished_at') || '';
      updateRecentRunStatus(jobId, isSuccess, finishedAtIso);
      stopRecentRunTracker(jobId);
    });

    source.addEventListener('job_error', function (event) {
      if (event.lastEventId) {
        lastEventId = event.lastEventId;
        const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
        if (row) row.setAttribute('data-recent-last-event-id', lastEventId);
      }
    });

    source.onerror = function () {
      // Reconnect tracker for running jobs after transient SSE errors.
      const row = document.querySelector('[data-recent-job-id="' + jobId + '"]');
      const isRunning = row && row.getAttribute('data-recent-job-running') === '1';
      const resumeFrom = row && row.getAttribute('data-recent-last-event-id')
        ? row.getAttribute('data-recent-last-event-id')
        : lastEventId;
      stopRecentRunTracker(jobId);
      if (isRunning && !document.querySelector('#job-panel-' + jobId)) {
        setTimeout(function () { startRecentRunTracker(jobId, resumeFrom); }, 800);
      }
    };
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
      const darkLabel = toggle.dataset.labelDark || 'Dark';
      const lightLabel = toggle.dataset.labelLight || 'Light';
      const switchTo = nextTheme === 'light' ? darkLabel : lightLabel;
      toggle.textContent = switchTo;
      toggle.setAttribute('aria-label', 'Switch to ' + switchTo.toLowerCase());
    }
  }

  function initializeTheme() {
    // The blocking inline script in <head> already applied data-theme
    // before first paint (see base.html) — this just wires up the toggle
    // and syncs the button label to match what's already on screen.
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    setTheme(current);
    toggle.addEventListener('click', function () {
      const now = document.documentElement.getAttribute('data-theme') || 'dark';
      setTheme(now === 'dark' ? 'light' : 'dark');
    });
  }

  function initializeLanguage() {
    const langSelect = document.getElementById('lang-toggle');
    if (!langSelect) return;

    const currentLang = langSelect.value;
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

  function parseSseXml(payload) {
    if (typeof payload !== "string" || !payload.trim()) return null;
    const doc = new DOMParser().parseFromString(payload, "application/xml");
    if (!doc || doc.querySelector("parsererror")) return null;
    return doc.documentElement;
  }

  function readXmlText(node, tagName) {
    if (!node) return "";
    const child = node.querySelector(tagName);
    return child && typeof child.textContent === "string" ? child.textContent : "";
  }

  function readXmlInt(node, tagName) {
    const value = parseInt(readXmlText(node, tagName), 10);
    return Number.isNaN(value) ? null : value;
  }

  function buildStatusBadgeMarkup(status, exitCode) {
    if (status === "success") {
      return '<span class="badge badge-success">Success</span>';
    }
    if (status === "failed") {
      const suffix = typeof exitCode === "number" ? " (" + exitCode + ")" : "";
      return '<span class="badge badge-danger">Failed' + suffix + '</span>';
    }
    return '<span class="badge badge-danger">Error</span>';
  }

  function buildSseUrl(baseUrl, afterEventId) {
    const url = new URL(baseUrl, window.location.origin);
    if (afterEventId) {
      url.searchParams.set("after_event_id", String(afterEventId));
    }
    return url.pathname + url.search;
  }

  function initializeJobPanel(panel) {
    if (!panel || panel.dataset.sseInit === "1") return;
    const isRunning = panel.dataset.jobRunning === "1";
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
    ensureRecentRunRow(jobId, panel.dataset.startedAt, panel.dataset.jobRange);
    // Panel stream is the source of truth while open.
    stopRecentRunTracker(jobId);

    let hasRealLogs = false;
    let pendingLogs = [];
    let flushScheduled = false;
    const maxLogLines = (function () {
      if (!logLines) return 2000;
      const raw = (logLines.dataset && logLines.dataset.maxLines) ? String(logLines.dataset.maxLines) : "";
      const parsed = parseInt(raw, 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : 2000;
    })();
    const maxPendingLogs = Math.max(maxLogLines * 2, 1000);

    function enforceLogLimit(shouldStickToBottom) {
      if (!logLines) return;

      const existingCount = logLines.childNodes.length;
      const incomingCount = pendingLogs.length;
      const excess = (existingCount + incomingCount) - maxLogLines;
      if (excess <= 0) return;

      const prevScrollTop = logLines.scrollTop;
      const prevScrollHeight = logLines.scrollHeight;

      const removeFromExisting = Math.min(existingCount, excess);
      for (let i = 0; i < removeFromExisting; i++) {
        if (!logLines.firstChild) break;
        logLines.removeChild(logLines.firstChild);
      }

      const remainingExcess = excess - removeFromExisting;
      if (remainingExcess > 0) {
        pendingLogs = pendingLogs.slice(remainingExcess);
      }

      if (!shouldStickToBottom && removeFromExisting > 0) {
        const afterRemovalScrollHeight = logLines.scrollHeight;
        const removedHeight = prevScrollHeight - afterRemovalScrollHeight;
        logLines.scrollTop = Math.max(0, prevScrollTop - removedHeight);
      }
    }

    function flushLogBuffer() {
      flushScheduled = false;
      if (!logLines || !pendingLogs.length) return;

      if (!hasRealLogs) {
        logLines.textContent = "";
        hasRealLogs = true;
      }

      const shouldStickToBottom =
        (logLines.scrollTop + logLines.clientHeight) >= (logLines.scrollHeight - 24);
      enforceLogLimit(shouldStickToBottom);
      const fragment = document.createDocumentFragment();

      pendingLogs.forEach(function (entry) {
        const line = document.createElement("span");
        line.className = entry.level === "error" ? "log-line log-line-error" : "log-line";
        line.textContent = entry.message;
        fragment.appendChild(line);
      });

      pendingLogs = [];
      logLines.appendChild(fragment);
      if (shouldStickToBottom) {
        logLines.scrollTop = logLines.scrollHeight;
      }
    }

    function queueLog(message, level) {
      pendingLogs.push({
        message: typeof message === "string" ? message : String(message || ""),
        level: level === "error" ? "error" : "info",
      });
      if (pendingLogs.length > maxPendingLogs) {
        pendingLogs = pendingLogs.slice(pendingLogs.length - maxPendingLogs);
      }
      if (!flushScheduled) {
        flushScheduled = true;
        window.requestAnimationFrame(flushLogBuffer);
      }
    }

    let source;
    let reconnectTimer = null;
    let lastEventId = panel.dataset.lastEventId || "";
    let lastStreamErrorAt = 0;

    function clearReconnectTimer() {
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    }

    function rememberEventId(event) {
      if (event && event.lastEventId) {
        lastEventId = event.lastEventId;
        panel.dataset.lastEventId = lastEventId;
      }
    }

    function openSource(afterEventId) {
      clearReconnectTimer();
      try {
        const url = buildSseUrl(streamUrl, afterEventId);
        try {
          source = new EventSource(url, { withCredentials: true });
        } catch (_) {
          source = new EventSource(url);
        }
        panel._sseSource = source;
      } catch (err) {
        queueLog("Failed to open log stream.", "error");
        return;
      }

      source.addEventListener("log", function (event) {
        rememberEventId(event);
        const payload = parseSseXml(event.data);
        if (!payload) {
          markStreamActive("Connected to container. Waiting for output...");
          queueLog(event.data || "<empty log payload>", "info");
          return;
        }
        const message = readXmlText(payload, "message");
        if (!message) {
          markStreamActive("Connected to container. Waiting for output...");
          queueLog(event.data, "info");
          return;
        }
        queueLog(message, readXmlText(payload, "level"));
      });

      function markStreamActive(message) {
        if (!hasRealLogs && logLines) {
          logLines.textContent = "";
          hasRealLogs = true;
          if (message) {
            queueLog(message, "info");
          }
        }
      }

      source.addEventListener("progress", function (event) {
        rememberEventId(event);
        markStreamActive("Connected to container. Waiting for output...");
        const payload = parseSseXml(event.data);
        const pct = readXmlInt(payload, "value");
        if (pct === null) {
          queueLog(event.data || "<invalid progress payload>", "error");
          return;
        }
        if (progressLabel) progressLabel.textContent = pct + "%";
        if (progressFill) {
          progressFill.innerHTML = '<div class="progress-fill" style="width:' + pct + '%"></div>';
        }
        updateRecentRunProgress(jobId, pct);
      });

      source.addEventListener("job_error", function (event) {
        rememberEventId(event);
        markStreamActive();
        const payload = parseSseXml(event.data);
        const msg = payload ? readXmlText(payload, "message") : event.data;
        queueLog(msg || "Job error", "error");
      });

      source.addEventListener("done", function (event) {
        rememberEventId(event);
        panel.dataset.jobRunning = "0";
        markStreamActive("Job finished without captured log output.");
        const payload = parseSseXml(event.data);
        const status = payload ? readXmlText(payload, "status") : "failed";
        const exitCode = payload ? readXmlInt(payload, "exit_code") : null;
        const finishedAtIso = payload ? readXmlText(payload, "finished_at") : "";
        const isSuccess = status === "success";

        if (statusEl) statusEl.innerHTML = buildStatusBadgeMarkup(status, exitCode);
        if (isSuccess && progressFill) {
          progressFill.innerHTML = '<div class="progress-fill" style="width:100%"></div>';
        }
        if (isSuccess && progressLabel) progressLabel.textContent = "100%";

        updateRecentRunStatus(jobId, isSuccess, finishedAtIso);
        flushLogBuffer();
        clearReconnectTimer();
        source.close();

        try {
          const url = new URL(window.location.href);
          if (url.searchParams.has("job_id")) {
            url.searchParams.delete("job_id");
            window.history.replaceState({}, "", url.toString());
          }
        } catch (_) {}
      });

      source.onopen = function () {
        if (isRunning) {
          markStreamActive("Connected to container. Waiting for output...");
        } else {
          markStreamActive("Loading log history...");
        }
      };

      source.onerror = function () {
        if (!panel.isConnected) return;

        const isStillRunning = panel.dataset.jobRunning === "1";
        if (!isStillRunning) {
          queueLog("Failed to load log history.", "error");
          flushLogBuffer();
          try { source.close(); } catch (_) {}
          return;
        }

        const now = Date.now();
        if ((now - lastStreamErrorAt) > 3000) {
          queueLog("Log stream interrupted. Waiting for reconnect...", "error");
          flushLogBuffer();
          lastStreamErrorAt = now;
        }

        if (source.readyState === EventSource.CLOSED) {
          clearReconnectTimer();
          reconnectTimer = window.setTimeout(function () {
            openSource(lastEventId);
          }, 800);
        }
      };
    }

    openSource(lastEventId);
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

  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    formatLocalDateTimes(evt.target || document);
    initializeAllJobPanels(evt.target || document);
    initializeRecentRunTrackers(evt.target || document);
  });
})();
