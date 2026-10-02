(function () {
  const I18N = JSON.parse(document.getElementById("ionmaps-i18n").textContent);
  const T = (key) => I18N[key] ?? key;
  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const form = document.getElementById("tecmap-form");
  if (!form) return;

  const modeEl = document.getElementById("tecmap-mode");
  const stationsEl = document.getElementById("tecmap-stations");
  const dateEl = document.getElementById("tecmap-date");
  const endDateEl = document.getElementById("tecmap-end-date");
  const yearEl = document.getElementById("tecmap-year");
  const doyEl = document.getElementById("tecmap-doy");
  const startTimeEl = document.getElementById("tecmap-start-time");
  const endTimeEl = document.getElementById("tecmap-end-time");
  const timestampEl = document.getElementById("tecmap-timestamp");
  const minElEl = document.getElementById("tecmap-min-el");
  const frameMinEl = document.getElementById("tecmap-frame-min");
  const gridResEl = document.getElementById("tecmap-grid-res");
  const sigmaEl = document.getElementById("tecmap-sigma");
  const samplingEl = document.getElementById("tecmap-sampling");
  const heightEl = document.getElementById("tecmap-height");
  const basemapEl = document.getElementById("tecmap-basemap");
  const fieldEl = document.getElementById("tecmap-field");
  const signalBandEl = document.getElementById("tecmap-signal-band");
  const interpEl = document.getElementById("tecmap-interp");
  const basemapAlphaEl = document.getElementById("tecmap-basemap-alpha");
  const fieldAlphaEl = document.getElementById("tecmap-field-alpha");
  const formatEl = document.getElementById("tecmap-format");
  const qualityEl = document.getElementById("tecmap-quality");
  const upsampleEl = document.getElementById("tecmap-upsample");
  const dpiEl = document.getElementById("tecmap-dpi");
  const vtecSmoothEl = document.getElementById("tecmap-vtec-smooth");
  const normalizeStationsEl = document.getElementById("tecmap-normalize-stations");
  const showAccuracyEl = document.getElementById("tecmap-show-accuracy");
  const showParamsEl = document.getElementById("tecmap-show-params");
  const modelEl = document.getElementById("tecmap-model");
  const stationHintEl = document.getElementById("tecmap-station-hint");
  const availYearEl = document.getElementById("tecmap-avail-year");
  const availDoyEl = document.getElementById("tecmap-avail-doy");
  const availRefreshEl = document.getElementById("tecmap-avail-refresh");
  const availDateEl = document.getElementById("tecmap-avail-date");
  const availStationsEl = document.getElementById("tecmap-avail-stations");

  const statusEl = document.getElementById("tecmap-status");
  const outputEl = document.getElementById("tecmap-output");
  const plotEl = document.getElementById("tecmap-plot");
  const gifWrapEl = document.getElementById("tecmap-gif-wrap");
  const gifImgEl = document.getElementById("tecmap-gif");
  const videoEl = document.getElementById("tecmap-video");
  const framePngBtn = document.getElementById("tecmap-frame-png");
  const validateBtn = document.getElementById("tecmap-validate");
  const validateResultEl = document.getElementById("tecmap-validate-result");
  const seriesBtn = document.getElementById("tecmap-series");
  const gifDownloadEl = document.getElementById("tecmap-gif-download");
  const lastUrlEl = document.getElementById("tecmap-last-url");

  let lastGifObjectUrl = "";
  let tecIndexOptions = null;

  function setStationHint(text) {
    if (!stationHintEl) return;
    const msg = clean(text);
    if (!msg) {
      stationHintEl.style.display = "none";
      stationHintEl.textContent = "";
      return;
    }
    stationHintEl.style.display = "";
    stationHintEl.textContent = msg;
  }

  function fillSelect(el, options, selected) {
    if (!el) return;
    const values = Array.isArray(options) ? options.map((v) => String(v)) : [];
    el.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = values.length ? T("ionmaps_js_select") : T("ionmaps_js_no_data");
    el.appendChild(empty);
    values.forEach((value) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      el.appendChild(opt);
    });
    if (selected && values.includes(String(selected))) el.value = String(selected);
  }

  function yDoyToIsoDate(year, doy) {
    const y = Number(year);
    const d = Number(doy);
    if (!Number.isFinite(y) || !Number.isFinite(d) || d < 1 || d > 366) return "";
    const dt = new Date(Date.UTC(y, 0, 1));
    dt.setUTCDate(dt.getUTCDate() + (d - 1));
    const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(dt.getUTCDate()).padStart(2, "0");
    return String(y) + "-" + mm + "-" + dd;
  }

  function isoDateToYDoy(isoDate) {
    const raw = clean(isoDate);
    if (!raw) return null;
    const parts = raw.split("-");
    if (parts.length !== 3) return null;
    const y = Number(parts[0]);
    const m = Number(parts[1]);
    const d = Number(parts[2]);
    if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
    const dt = new Date(Date.UTC(y, m - 1, d));
    if (Number.isNaN(dt.getTime())) return null;
    const start = new Date(Date.UTC(y, 0, 1));
    const doy = Math.floor((dt.getTime() - start.getTime()) / 86400000) + 1;
    return { year: String(y), doy: String(doy) };
  }

  function setAvailDateLabel() {
    if (!availDateEl) return;
    const year = clean(availYearEl?.value);
    const doy = clean(availDoyEl?.value);
    if (!year || !doy) {
      availDateEl.textContent = "";
      return;
    }
    const iso = yDoyToIsoDate(year, doy);
    availDateEl.textContent = iso ? (T("ionmaps_js_selected_day") + " " + iso + " (UTC)") : "";
  }

  // Proximity groups per day, fetched from /tec-map/station-positions.
  // key "year-doy" -> groups array | null (fetch failed / flat fallback);
  // "pending" while a request is in flight.
  const stationGroupsCache = {};

  function fetchStationGroups(year, doy) {
    const key = year + "-" + doy;
    if (stationGroupsCache[key] !== undefined) return;
    stationGroupsCache[key] = "pending";
    fetch("/tec-map/station-positions?year=" + encodeURIComponent(year) + "&doy=" + Number(doy), { credentials: "same-origin" })
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        const groups = payload && Array.isArray(payload.groups) && payload.groups.length ? payload.groups : null;
        stationGroupsCache[key] = groups;
        if (clean(availYearEl?.value) === year && clean(availDoyEl?.value) === doy) updateAvailStations();
      })
      .catch(() => {
        stationGroupsCache[key] = null;
      });
  }

  function updateAvailStations() {
    if (!availStationsEl) return;
    availStationsEl.innerHTML = "";
    const year = clean(availYearEl?.value);
    const doy = clean(availDoyEl?.value);
    const stations = (tecIndexOptions?.stationsByYearDoy?.[year]?.[doy] || []).map((s) => String(s).toLowerCase());
    const selected = new Set(parseStations(stationsEl?.value || ""));

    if (!stations.length) {
      const empty = document.createElement("div");
      empty.style.fontSize = "12px";
      empty.style.color = "var(--text-muted)";
      empty.textContent = year && doy ? T("ionmaps_js_no_stations") : T("ionmaps_js_pick_year_doy");
      availStationsEl.appendChild(empty);
      return;
    }

    function makeChip(station) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-secondary";
      btn.style.padding = "4px 10px";
      btn.style.fontSize = "12px";
      btn.style.lineHeight = "1.2";
      btn.textContent = station.toUpperCase();
      const isOn = selected.has(station);
      btn.style.opacity = isOn ? "1" : "0.7";
      btn.addEventListener("click", function () {
        const set = new Set(parseStations(stationsEl ? stationsEl.value : ""));
        if (set.has(station)) set.delete(station);
        else set.add(station);
        if (stationsEl) stationsEl.value = Array.from(set).sort().join(", ");
        updateAvailStations();
      });
      return btn;
    }

    fetchStationGroups(year, doy);
    const groups = stationGroupsCache[year + "-" + doy];

    if (!Array.isArray(groups)) {
      // Flat fallback (grouping unavailable or still loading).
      stations.sort().forEach((station) => availStationsEl.appendChild(makeChip(station)));
      return;
    }

    const stationSet = new Set(stations);
    const grouped = new Set();

    function renderGroup(labelText, members, center) {
      const wrap = document.createElement("div");
      wrap.style.cssText =
        "flex-basis:100%; display:flex; flex-wrap:wrap; gap:6px; align-items:center; " +
        "padding:6px 8px; border:1px solid var(--border); border-radius:var(--radius);";
      const head = document.createElement("button");
      head.type = "button";
      head.className = "btn btn-ghost";
      head.style.cssText = "padding:3px 8px; font-size:11px; line-height:1.2; color:var(--text-muted);";
      const allOn = members.every((s) => selected.has(s));
      head.textContent = (allOn ? "− " : "+ ") + labelText +
        (center ? " · " + center.lat + "N " + center.lon + "E" : "");
      head.title = allOn ? T("ionmaps_js_group_remove") : T("ionmaps_js_group_add");
      head.addEventListener("click", function () {
        const set = new Set(parseStations(stationsEl ? stationsEl.value : ""));
        if (allOn) members.forEach((s) => set.delete(s));
        else members.forEach((s) => set.add(s));
        if (stationsEl) stationsEl.value = Array.from(set).sort().join(", ");
        updateAvailStations();
      });
      wrap.appendChild(head);
      members.forEach((s) => wrap.appendChild(makeChip(s)));
      availStationsEl.appendChild(wrap);
    }

    groups.forEach(function (group) {
      const members = (group.stations || []).filter((s) => stationSet.has(s));
      if (!members.length) return;
      members.forEach((s) => grouped.add(s));
      renderGroup(String(group.anchor || members[0]).toUpperCase() + " " + T("ionmaps_js_region") + " (" + members.length + ")", members, group.center);
    });

    const rest = stations.filter((s) => !grouped.has(s)).sort();
    if (rest.length) renderGroup(T("ionmaps_js_other") + " (" + rest.length + ")", rest, null);
  }

  function updateAvailDoys() {
    const year = clean(availYearEl?.value);
    const doys = Array.isArray(tecIndexOptions?.doysByYear?.[year]) ? tecIndexOptions.doysByYear[year] : [];
    fillSelect(availDoyEl, doys, clean(doyEl?.value));
    if (availDoyEl && !clean(availDoyEl.value) && Array.isArray(doys) && doys.length) {
      availDoyEl.value = String(doys[0]);
    }
    setAvailDateLabel();
    updateAvailStations();
  }

  function applyAvailSelectionToInputs() {
    const year = clean(availYearEl?.value);
    const doy = clean(availDoyEl?.value);
    if (year && yearEl) yearEl.value = year;
    if (doy && doyEl) doyEl.value = doy;
    if (dateEl) dateEl.value = "";
  }

  function syncTecMapDateToYearDoy() {
    const dateValue = clean(dateEl?.value);
    if (!dateValue) return;
    const yd = isoDateToYDoy(dateValue);
    if (!yd) return;
    if (yearEl) yearEl.value = yd.year;
    if (doyEl) doyEl.value = yd.doy;
  }

  function selectHasValue(el, value) {
    if (!el) return false;
    return Array.from(el.options || []).some((opt) => clean(opt.value) === clean(value));
  }

  function syncManualInputsToAvailableSelectors() {
    const year = clean(yearEl?.value);
    const doy = clean(doyEl?.value);
    if (!availYearEl || !availDoyEl) return;

    if (year && selectHasValue(availYearEl, year) && clean(availYearEl.value) !== year) {
      availYearEl.value = year;
      updateAvailDoys();
    }
    if (doy && selectHasValue(availDoyEl, doy)) {
      availDoyEl.value = doy;
    }
    setAvailDateLabel();
    updateAvailStations();
  }

  async function loadTecIndexOptions(force) {
    if (!force && window.__ictHubTecIndexOptions && typeof window.__ictHubTecIndexOptions === "object") {
      tecIndexOptions = window.__ictHubTecIndexOptions;
      setStationHint(T("ionmaps_js_index_ready"));
      return true;
    }

    try {
      setStationHint(T("ionmaps_js_index_loading"));
      const res = await fetch("/analysis/index-options", { credentials: "same-origin" });
      if (!res.ok) {
        setStationHint(T("ionmaps_js_index_unavailable") + " (" + res.status + ").");
        return false;
      }
      const payload = await res.json();
      tecIndexOptions = payload?.tec && typeof payload.tec === "object" ? payload.tec : null;
      window.__ictHubTecIndexOptions = tecIndexOptions;
      setStationHint(tecIndexOptions ? T("ionmaps_js_index_ready") : T("ionmaps_js_index_empty"));
      return Boolean(tecIndexOptions);
    } catch (_) {
      setStationHint(T("ionmaps_js_index_failed"));
      return false;
    }
  }

  function setStatus(kind, text) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    if (kind === "error") statusEl.style.color = "var(--danger-text)";
    else if (kind === "success") statusEl.style.color = "var(--success-text)";
    else if (kind === "warning") statusEl.style.color = "var(--warning-text)";
    else statusEl.style.color = "var(--text-muted)";
  }

  function clean(text) {
    return String(text || "").trim();
  }

  function parseStations(text) {
    const raw = clean(text);
    if (!raw) return [];
    const tokens = raw.split(/[\s,;]+/g).map((v) => clean(v).toLowerCase()).filter(Boolean);
    return Array.from(new Set(tokens));
  }

  function updateModeVisibility() {
    const mode = modeEl ? modeEl.value : "snapshot";
    document.querySelectorAll("[data-tecmap-mode]").forEach((el) => {
      const wanted = el.getAttribute("data-tecmap-mode");
      const show = wanted === mode;
      el.style.display = show ? "" : "none";
    });

    if (plotEl && window.Plotly) window.Plotly.purge(plotEl);
    if (gifImgEl && lastGifObjectUrl) {
      URL.revokeObjectURL(lastGifObjectUrl);
      lastGifObjectUrl = "";
      gifImgEl.removeAttribute("src");
    }

    if (gifWrapEl) gifWrapEl.style.display = "none";
    if (plotEl) plotEl.style.display = "none";
    if (outputEl) outputEl.style.display = "none";
    if (gifDownloadEl) gifDownloadEl.style.display = "none";
    setStatus("info", "");
  }

  function addNumberParam(params, key, el) {
    if (!params || !el) return;
    const value = Number(el.value);
    if (Number.isFinite(value)) params.set(key, String(value));
  }

  function setLastUrl(url) {
    if (!lastUrlEl) return;
    if (!url) {
      lastUrlEl.style.display = "none";
      lastUrlEl.removeAttribute("href");
      return;
    }
    lastUrlEl.style.display = "";
    lastUrlEl.href = url;
  }

  // " (status): message" for a failed response: the server's JSON `detail`,
  // or for an HTML page (a proxy's or gateway's error page) its title, never
  // the whole page.
  async function responseError(res) {
    const contentType = (res.headers.get("Content-Type") || "").toLowerCase();
    const body = await res.text();
    let message = "";
    if (contentType.indexOf("json") >= 0) {
      try {
        const detail = JSON.parse(body).detail;
        if (typeof detail === "string") {
          message = detail;
        } else if (Array.isArray(detail)) {
          message = detail.map(function (item) { return item && item.msg ? item.msg : JSON.stringify(item); }).join("; ");
        }
      } catch (_) {
        message = body;
      }
    } else if (contentType.indexOf("html") >= 0 || /^\s*</.test(body)) {
      const title = new DOMParser().parseFromString(body, "text/html").title.trim();
      message = [502, 503, 504].indexOf(res.status) >= 0
        ? T("ionmaps_js_gateway_error") + (title ? " (" + title + ")" : "")
        : title;
    } else {
      message = body.trim();
    }
    if (message.length > 400) message = message.slice(0, 400) + "…";
    return " (" + res.status + "): " + (message || res.statusText);
  }

  async function runSnapshot(url) {
    setStatus("info", T("ionmaps_js_fetching_snapshot"));
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) {
      throw new Error(T("ionmaps_js_snapshot_failed") + (await responseError(res)));
    }

    const payload = await res.json();
    if (!plotEl || !window.Plotly) {
      throw new Error(T("ionmaps_js_no_plotly"));
    }

    const traces = Array.isArray(payload?.data) ? payload.data : [];
    const layout = payload?.layout && typeof payload.layout === "object" ? payload.layout : {};
    await window.Plotly.react(plotEl, traces, layout, {
      responsive: true,
      displaylogo: false,
      scrollZoom: true,
      modeBarButtonsToRemove: ["select2d", "lasso2d"],
    });
    if (plotEl) plotEl.style.display = "block";
    if (outputEl) outputEl.style.display = "block";
    setStatus("success", T("ionmaps_js_snapshot_done"));
  }

  function jobProgressText(job) {
    if (job.status === "queued") {
      return job.queue_position > 0
        ? T("ionmaps_js_job_queued_behind").replace("{n}", job.queue_position)
        : T("ionmaps_js_job_queued");
    }
    const label = {
      loading: T("ionmaps_js_job_loading"),
      gridding: T("ionmaps_js_job_gridding"),
      drawing: T("ionmaps_js_job_drawing"),
      encoding: T("ionmaps_js_job_encoding"),
    }[job.stage];
    if (!label || (!job.total && job.stage !== "encoding")) return T("ionmaps_js_rendering");
    return label.replace("{done}", job.done).replace("{total}", job.total);
  }

  // The animation renders as a background job: start it, poll its progress,
  // then download it. Each request is short, so no browser or proxy timeout
  // can cut off a render that takes many minutes.
  async function runGif(url) {
    setStatus("info", T("ionmaps_js_rendering"));
    const started = await fetch(url.replace("/tec-map/gif?", "/tec-map/gif/jobs?"), {
      method: "POST",
      credentials: "same-origin",
    });
    if (!started.ok) {
      throw new Error(T("ionmaps_js_animation_failed") + (await responseError(started)));
    }
    let job = await started.json();
    let failedPolls = 0;
    while (job.status !== "done") {
      if (job.status === "failed") {
        throw new Error(T("ionmaps_js_animation_failed") + ": " + (job.error || ""));
      }
      setStatus("info", jobProgressText(job));
      await new Promise(function (resolve) { setTimeout(resolve, 1500); });
      let res = null;
      try {
        res = await fetch(job.status_url, { credentials: "same-origin" });
      } catch (_) {
        // network blip; retried below
      }
      if (res && res.ok) {
        job = await res.json();
        failedPolls = 0;
        continue;
      }
      // A blip (a restart, a proxy hiccup) shouldn't lose a long render.
      const transient = !res || res.status >= 500;
      if (transient && ++failedPolls < 10) continue;
      if (!res) throw new Error(T("ionmaps_js_animation_failed") + ": " + T("ionmaps_js_gateway_error"));
      throw new Error(T("ionmaps_js_animation_failed") + (await responseError(res)));
    }

    const res = await fetch(job.result_url, { credentials: "same-origin" });
    if (!res.ok) {
      throw new Error(T("ionmaps_js_animation_failed") + (await responseError(res)));
    }

    const contentType = (res.headers.get("Content-Type") || "").toLowerCase();
    const isVideo = contentType.indexOf("video/") === 0;
    const ext = contentType.indexOf("webm") >= 0 ? "webm" : (isVideo ? "mp4" : "gif");

    const blob = await res.blob();
    if (lastGifObjectUrl) URL.revokeObjectURL(lastGifObjectUrl);
    lastGifObjectUrl = URL.createObjectURL(blob);

    if (gifImgEl) {
      gifImgEl.style.display = isVideo ? "none" : "block";
      gifImgEl.src = isVideo ? "" : lastGifObjectUrl;
    }
    if (videoEl) {
      videoEl.style.display = isVideo ? "block" : "none";
      videoEl.src = isVideo ? lastGifObjectUrl : "";
      if (isVideo) videoEl.play().catch(function () {});
    }
    if (gifWrapEl) gifWrapEl.style.display = "block";
    if (gifDownloadEl) {
      gifDownloadEl.href = lastGifObjectUrl;
      gifDownloadEl.setAttribute("download", "tec_map." + ext);
      gifDownloadEl.textContent = T("ionmaps_js_download") + " " + ext.toUpperCase();
      gifDownloadEl.style.display = "";
    }
    if (outputEl) outputEl.style.display = "block";
    setStatus("success", ext.toUpperCase() + " " + T("ionmaps_js_ready"));
  }

  function buildRequestUrl() {
    const mode = modeEl ? modeEl.value : "snapshot";
    const stations = parseStations(stationsEl ? stationsEl.value : "");
    if (!stations.length) throw new Error(T("ionmaps_js_err_stations"));

    const params = new URLSearchParams();
    const yearValue = clean(yearEl ? yearEl.value : "");
    const doyValue = clean(doyEl ? doyEl.value : "");
    const dateValue = clean(dateEl ? dateEl.value : "");
    const endDateValue = clean(endDateEl ? endDateEl.value : "");
    const canonicalDate = (yearValue && doyValue) ? yDoyToIsoDate(yearValue, doyValue) : "";

    if (yearValue && doyValue) {
      params.set("year", yearValue);
      params.set("doy", doyValue);
    } else if (!dateValue) {
      throw new Error(T("ionmaps_js_err_date"));
    }

    const effectiveDate = canonicalDate || dateValue;
    if (effectiveDate) {
      params.set("date", effectiveDate);
      if (dateEl && clean(dateEl.value) !== effectiveDate) {
        dateEl.value = effectiveDate;
      }
    }
    if (endDateValue) params.set("end_date", endDateValue);

    for (const station of stations) params.append("stations", station);

    addNumberParam(params, "min_elevation_deg", minElEl);
    addNumberParam(params, "sampling_interval_seconds", samplingEl);
    addNumberParam(params, "frame_minutes", frameMinEl);
    addNumberParam(params, "ionosphere_height_km", heightEl);
    addNumberParam(params, "grid_resolution_deg", gridResEl);
    addNumberParam(params, "smoothing_sigma", sigmaEl);

    const interpValue = clean(interpEl ? interpEl.value : "") || "linear";
    if (interpValue === "lpi2") {
      params.set("interpolation", "lpi");
      params.set("lpi_degree", "2");
    } else if (interpValue !== "linear") {
      params.set("interpolation", interpValue);
    }

    const fieldValue = clean(fieldEl ? fieldEl.value : "") || "vtec";
    if (fieldValue && fieldValue !== "vtec") params.set("field", fieldValue);
    if (fieldValue === "gdd" || fieldValue === "b_k") {
      const bandValue = clean(signalBandEl ? signalBandEl.value : "") || "gps_l1";
      if (bandValue !== "gps_l1") params.set("signal_band", bandValue);
    }

    const smoothEpochs = Number(vtecSmoothEl ? vtecSmoothEl.value : "");
    if (Number.isFinite(smoothEpochs) && smoothEpochs > 0) {
      params.set("vtec_smooth_epochs", String(Math.floor(smoothEpochs)));
    }

    const normalizeValue = clean(normalizeStationsEl ? normalizeStationsEl.value : "") || "off";
    if (normalizeValue && normalizeValue !== "off") params.set("normalize_stations", normalizeValue);

    const showAccuracyValue = clean(showAccuracyEl ? showAccuracyEl.value : "") || "off";
    if (showAccuracyValue === "on") params.set("show_accuracy", "true");

    const showParamsValue = clean(showParamsEl ? showParamsEl.value : "") || "on";
    if (showParamsValue === "on") params.set("show_params", "true");

    const modelValue = clean(modelEl ? modelEl.value : "") || "off";
    if (modelValue !== "off") params.set("model", modelValue);

    if (mode === "range") {
      const startText = clean(startTimeEl ? startTimeEl.value : "");
      const endText = clean(endTimeEl ? endTimeEl.value : "");
      if (!startText || !endText) throw new Error(T("ionmaps_js_err_range"));
      params.set("start_time", startText);
      params.set("end_time", endText);
      if (basemapEl) params.set("basemap", clean(basemapEl.value) || "false");

      const basemapAlphaValue = Number(basemapAlphaEl ? basemapAlphaEl.value : "");
      if (Number.isFinite(basemapAlphaValue) && basemapAlphaValue >= 0 && basemapAlphaValue <= 1 && basemapAlphaValue !== 0.28) {
        params.set("basemap_alpha", String(basemapAlphaValue));
      }
      const fieldAlphaValue = Number(fieldAlphaEl && fieldAlphaEl.value !== "" ? fieldAlphaEl.value : NaN);
      if (Number.isFinite(fieldAlphaValue) && fieldAlphaValue >= 0 && fieldAlphaValue <= 1) {
        params.set("field_alpha", String(fieldAlphaValue));
      }

      const fmtValue = clean(formatEl ? formatEl.value : "") || "gif";
      if (fmtValue !== "gif") params.set("format", fmtValue);
      const qualityValue = clean(qualityEl ? qualityEl.value : "") || "standard";
      if (qualityValue !== "standard") params.set("quality", qualityValue);
      const upsampleValue = clean(upsampleEl ? upsampleEl.value : "") || "2";
      if (upsampleValue !== "2") params.set("upsample", upsampleValue);
      const dpiValue = Number(dpiEl ? dpiEl.value : "");
      if (Number.isFinite(dpiValue) && dpiValue >= 50) params.set("frame_dpi", String(Math.floor(dpiValue)));
      return "/tec-map/gif?" + params.toString();
    }

    const tsText = clean(timestampEl ? timestampEl.value : "");
    if (!tsText) throw new Error(T("ionmaps_js_err_timestamp"));
    params.set("timestamp", tsText);
    return "/tec-map/snapshot?" + params.toString();
  }

  function buildFrameUrl() {
    const url = buildRequestUrl();
    if (!url.startsWith("/tec-map/snapshot?")) {
      throw new Error(T("ionmaps_js_err_snapshot_mode"));
    }
    const params = new URLSearchParams(url.slice("/tec-map/snapshot?".length));
    params.set("dpi", "300");
    const upsampleValue = clean(upsampleEl ? upsampleEl.value : "") || "2";
    if (upsampleValue !== "2") params.set("upsample", upsampleValue);
    return "/tec-map/frame?" + params.toString();
  }

  if (framePngBtn) {
    framePngBtn.addEventListener("click", async function () {
      let url;
      try {
        url = buildFrameUrl();
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
        return;
      }
      setLastUrl(url);
      setStatus("info", T("ionmaps_js_rendering_png"));
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) {
          throw new Error(T("ionmaps_js_frame_failed") + (await responseError(res)));
        }
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = "tec_map_frame.png";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 30000);
        setStatus("success", T("ionmaps_js_png_done"));
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
      }
    });
  }

  function buildValidateUrl() {
    const url = buildRequestUrl();
    let params;
    if (url.startsWith("/tec-map/gif?")) {
      params = new URLSearchParams(url.slice("/tec-map/gif?".length));
    } else {
      // Snapshot mode: validate the single frame bin containing the timestamp.
      params = new URLSearchParams(url.slice("/tec-map/snapshot?".length));
      const ts = params.get("timestamp") || "";
      params.delete("timestamp");
      params.set("start_time", ts);
      params.set("end_time", ts);
    }
    // Rendering-only parameters are meaningless for cross-validation.
    for (const key of ["basemap", "basemap_alpha", "field_alpha", "format", "quality",
                       "upsample", "frame_dpi", "field", "signal_band", "show_accuracy", "show_params",
                       "interpolation", "model", "f107"]) {
      params.delete(key);
    }
    params.set("interpolation", "all");
    return "/tec-map/validate?" + params.toString();
  }

  function buildSeriesUrl() {
    const url = buildRequestUrl();
    let params;
    if (url.startsWith("/tec-map/gif?")) {
      params = new URLSearchParams(url.slice("/tec-map/gif?".length));
    } else {
      // Snapshot mode: export the single frame bin containing the timestamp.
      params = new URLSearchParams(url.slice("/tec-map/snapshot?".length));
      const ts = params.get("timestamp") || "";
      params.delete("timestamp");
      params.set("start_time", ts);
      params.set("end_time", ts);
    }
    // The series is taken at station IPPs before any spatial interpolation,
    // so gridding/rendering parameters do not apply. The export always
    // carries VTEC + GDD + B_k columns, so `field` is dropped and the
    // signal band comes straight from the form select.
    for (const key of ["basemap", "basemap_alpha", "field_alpha", "format", "quality",
                       "upsample", "frame_dpi", "show_accuracy", "show_params",
                       "interpolation", "lpi_degree", "grid_resolution_deg",
                       "smoothing_sigma", "color_min", "color_max", "field", "signal_band"]) {
      params.delete(key);
    }
    const seriesBand = clean(signalBandEl ? signalBandEl.value : "") || "gps_l1";
    if (seriesBand !== "gps_l1") params.set("signal_band", seriesBand);
    // Series supports model=iri (adds IRI columns); a difference map maps to the same export.
    if (params.get("model") === "difference") params.set("model", "iri");
    params.set("format", "csv");
    return "/tec-map/series?" + params.toString();
  }

  if (seriesBtn) {
    seriesBtn.addEventListener("click", async function () {
      let url;
      try {
        url = buildSeriesUrl();
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
        return;
      }
      setLastUrl(url);
      setStatus("info", T("ionmaps_js_exporting_series"));
      seriesBtn.disabled = true;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) {
          throw new Error(T("ionmaps_js_series_failed") + (await responseError(res)));
        }
        const disposition = res.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="([^"]+)"/);
        const filename = match ? match[1] : "tec_map_series.csv";
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 30000);
        setStatus("success", T("ionmaps_js_series_done") + " (" + filename + ").");
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
      } finally {
        seriesBtn.disabled = false;
      }
    });
  }

  function fmtMetric(value) {
    return (value === null || value === undefined || !Number.isFinite(Number(value)))
      ? "—" : Number(value).toFixed(2);
  }

  function renderValidationTable(payload) {
    if (!validateResultEl) return;
    const results = payload && payload.results ? payload.results : {};
    const methods = Object.keys(results);
    if (!methods.length) {
      validateResultEl.style.display = "";
      validateResultEl.innerHTML = "<div class=\"param-hint\">" + escapeHtml(T("ionmaps_js_no_validation")) + "</div>";
      return;
    }

    const stations = new Set();
    methods.forEach((m) => (results[m].per_station || []).forEach((row) => stations.add(row.station)));
    const stationList = Array.from(stations).sort();
    const byMethodStation = {};
    methods.forEach((m) => {
      byMethodStation[m] = {};
      (results[m].per_station || []).forEach((row) => { byMethodStation[m][row.station] = row; });
    });

    let html = "<h4 style=\"margin:0 0 6px;\">" + escapeHtml(T("ionmaps_loso_title")) + "</h4>";
    html += "<div class=\"param-hint\" style=\"margin-bottom:8px;\">" + escapeHtml(T("ionmaps_loso_hint")) + "</div>";
    html += "<div class=\"table-wrap\"><table style=\"font-size:12px;\"><thead><tr><th>" + escapeHtml(T("ionmaps_loso_station")) + "</th>";
    methods.forEach((m) => { html += "<th colspan=\"4\" style=\"text-align:center;\">" + m + "</th>"; });
    html += "</tr><tr><th></th>";
    methods.forEach(() => { html += "<th>N</th><th>Bias</th><th>MAE</th><th>RMSE</th>"; });
    html += "</tr></thead><tbody>";

    stationList.forEach((station) => {
      html += "<tr><td>" + station.toUpperCase() + "</td>";
      methods.forEach((m) => {
        const row = byMethodStation[m][station] || {};
        html += "<td>" + (row.n ?? "—") + "</td><td>" + fmtMetric(row.bias_tecu) + "</td><td>"
              + fmtMetric(row.mae_tecu) + "</td><td>" + fmtMetric(row.rmse_tecu) + "</td>";
      });
      html += "</tr>";
    });

    html += "<tr style=\"font-weight:600; border-top:2px solid var(--border);\"><td>" + escapeHtml(T("ionmaps_loso_overall")) + "</td>";
    methods.forEach((m) => {
      const o = results[m].overall || {};
      html += "<td>" + (o.n ?? "—") + "</td><td>" + fmtMetric(o.bias_tecu) + "</td><td>"
            + fmtMetric(o.mae_tecu) + "</td><td>" + fmtMetric(o.rmse_tecu) + "</td>";
    });
    html += "</tr></tbody></table></div>";

    const outOfCoverage = methods
      .map((m) => (results[m].overall || {}).n_out_of_coverage || 0)
      .reduce((a, b) => Math.max(a, b), 0);
    if (outOfCoverage > 0) {
      html += "<div class=\"param-hint\" style=\"margin-top:6px;\">" + outOfCoverage
            + " " + escapeHtml(T("ionmaps_loso_out_of_coverage")) + "</div>";
    }

    validateResultEl.style.display = "";
    validateResultEl.innerHTML = html;
  }

  if (validateBtn) {
    validateBtn.addEventListener("click", async function () {
      let url;
      try {
        url = buildValidateUrl();
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
        return;
      }
      setLastUrl(url);
      setStatus("info", T("ionmaps_js_running_loso"));
      validateBtn.disabled = true;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) {
          throw new Error(T("ionmaps_js_validation_failed") + (await responseError(res)));
        }
        const payload = await res.json();
        renderValidationTable(payload);
        setStatus("success", T("ionmaps_js_loso_done"));
      } catch (err) {
        setStatus("error", err instanceof Error ? err.message : String(err));
      } finally {
        validateBtn.disabled = false;
      }
    });
  }

  async function initAvailableDataUi() {
    if (!availYearEl || !availDoyEl) return;

    const ok = await loadTecIndexOptions(false);
    const years = Array.isArray(tecIndexOptions?.years) ? tecIndexOptions.years : [];
    const preferredYear = clean(yearEl?.value) || (years[0] ? String(years[0]) : "");
    fillSelect(availYearEl, years, preferredYear);
    updateAvailDoys();
    applyAvailSelectionToInputs();
    setAvailDateLabel();
    updateAvailStations();

    availYearEl.addEventListener("change", function () {
      applyAvailSelectionToInputs();
      updateAvailDoys();
      setAvailDateLabel();
      updateAvailStations();
    });
    availDoyEl.addEventListener("change", function () {
      applyAvailSelectionToInputs();
      setAvailDateLabel();
      updateAvailStations();
    });
    if (availRefreshEl) {
      availRefreshEl.addEventListener("click", async function () {
        await loadTecIndexOptions(true);
        const yrs = Array.isArray(tecIndexOptions?.years) ? tecIndexOptions.years : [];
        const pref = clean(availYearEl.value) || clean(yearEl?.value) || (yrs[0] ? String(yrs[0]) : "");
        fillSelect(availYearEl, yrs, pref);
        updateAvailDoys();
        applyAvailSelectionToInputs();
        setAvailDateLabel();
        updateAvailStations();
      });
    }
    if (stationsEl) stationsEl.addEventListener("input", updateAvailStations);

    if (!ok) {
      setStationHint(T("ionmaps_js_index_manual"));
    }
  }

  initAvailableDataUi();

  if (dateEl) {
    dateEl.addEventListener("change", function () {
      syncTecMapDateToYearDoy();
      syncManualInputsToAvailableSelectors();
    });
    dateEl.addEventListener("input", function () {
      syncTecMapDateToYearDoy();
      syncManualInputsToAvailableSelectors();
    });
  }
  if (yearEl) {
    yearEl.addEventListener("input", function () {
      if (dateEl) dateEl.value = "";
      syncManualInputsToAvailableSelectors();
    });
    yearEl.addEventListener("change", function () {
      if (dateEl) dateEl.value = "";
      syncManualInputsToAvailableSelectors();
    });
  }
  if (doyEl) {
    doyEl.addEventListener("input", function () {
      if (dateEl) dateEl.value = "";
      syncManualInputsToAvailableSelectors();
    });
    doyEl.addEventListener("change", function () {
      if (dateEl) dateEl.value = "";
      syncManualInputsToAvailableSelectors();
    });
  }

  if (modeEl) modeEl.addEventListener("change", updateModeVisibility);
  updateModeVisibility();

  form.addEventListener("submit", async (evt) => {
    evt.preventDefault();
    setStatus("info", "");

    let url;
    try {
      url = buildRequestUrl();
    } catch (err) {
      setStatus("error", err instanceof Error ? err.message : String(err));
      return;
    }

    setLastUrl(url);
    if (plotEl && window.Plotly) window.Plotly.purge(plotEl);
    if (plotEl) plotEl.style.display = "none";
    if (gifWrapEl) gifWrapEl.style.display = "none";
    if (gifDownloadEl) gifDownloadEl.style.display = "none";
    if (outputEl) outputEl.style.display = "none";

    try {
      if (url.startsWith("/tec-map/gif")) {
        await runGif(url);
      } else {
        await runSnapshot(url);
      }
    } catch (err) {
      setStatus("error", err instanceof Error ? err.message : String(err));
    }
  });
})();
