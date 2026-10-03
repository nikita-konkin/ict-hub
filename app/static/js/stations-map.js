(function () {
  const I18N = JSON.parse(document.getElementById("stations-map-i18n").textContent);
  const T = (key) => I18N[key] ?? key;
  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const rinexTree = JSON.parse(document.getElementById("stations-map-rinex-tree").textContent);

  function byDayValue(a, b) {
    const left = String(a || "");
    const right = String(b || "");
    if (left.includes("/") && right.includes("/")) {
      const [lm, ld] = left.split("/").map(Number);
      const [rm, rd] = right.split("/").map(Number);
      return (lm - rm) || (ld - rd) || left.localeCompare(right);
    }
    const leftNum = Number.parseInt(left, 10);
    const rightNum = Number.parseInt(right, 10);
    if (!Number.isNaN(leftNum) && !Number.isNaN(rightNum)) {
      return (leftNum - rightNum) || (left.length - right.length) || left.localeCompare(right);
    }
    return left.localeCompare(right);
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  const state = {
    tree: Array.isArray(rinexTree) ? rinexTree : [],
    payload: null,
  };

  const yearSelect = document.getElementById("station-year");
  const daySelect = document.getElementById("station-day");
  const loadButton = document.getElementById("stations-map-load");
  const refreshButton = document.getElementById("stations-map-refresh");
  const labelsCheckbox = document.getElementById("stations-map-labels");
  const backgroundSelect = document.getElementById("stations-map-background");
  const summaryEl = document.getElementById("stations-map-summary");
  const statusEl = document.getElementById("stations-map-status");
  const plotEl = document.getElementById("stations-map-plot");
  const tableWrapEl = document.getElementById("stations-map-table-wrap");

  function getYearItem(yearValue) {
    return state.tree.find((item) => String(item.year || "") === String(yearValue || ""));
  }

  function populateYearOptions() {
    if (!yearSelect) return;
    yearSelect.innerHTML = "";
    for (const item of state.tree) {
      const yearValue = String(item.year || "").trim();
      if (!yearValue) continue;
      const option = document.createElement("option");
      option.value = yearValue;
      option.textContent = yearValue;
      yearSelect.appendChild(option);
    }
  }

  function populateDayOptions(preserveValue) {
    if (!daySelect) return;
    const yearItem = getYearItem(yearSelect ? yearSelect.value : "");
    const days = Array.isArray(yearItem && yearItem.days) ? [...yearItem.days] : [];
    days.sort((left, right) => byDayValue(left.day, right.day));

    daySelect.innerHTML = "";
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = T("stations_map_all_days");
    daySelect.appendChild(allOption);

    for (const item of days) {
      const dayValue = String(item.day || "").trim();
      if (!dayValue) continue;
      const count = Number(item.stations || 0);
      const option = document.createElement("option");
      option.value = dayValue;
      option.textContent = `${dayValue} (${count} ${T("run_unit_stations")})`;
      daySelect.appendChild(option);
    }

    if (preserveValue && [...daySelect.options].some((option) => option.value === preserveValue)) {
      daySelect.value = preserveValue;
      return;
    }

    if (days.length) {
      daySelect.value = String(days[days.length - 1].day || "");
    } else {
      daySelect.value = "";
    }
  }

  function setStatus(kind, message) {
    if (!statusEl) return;
    statusEl.className = `station-map-status${kind === "error" ? " error" : ""}`;
    statusEl.textContent = message;
  }

  function renderSummary(payload) {
    if (!summaryEl) return;
    const stationCount = Number(payload.station_count || 0);
    const archiveCount = Number(payload.archive_count || 0);
    const dayValue = String(payload.day || "");
    const label = dayValue ? dayValue : T("stations_map_all_days");
    summaryEl.innerHTML = [
      `<div><strong>${escapeHtml(T("label_year_folder"))}:</strong> ${payload.year || "-"}</div>`,
      `<div><strong>${escapeHtml(T("label_day_folder"))}:</strong> ${label}</div>`,
      `<div><strong>${escapeHtml(T("stations_map_station_count"))}:</strong> ${stationCount}</div>`,
      `<div><strong>${escapeHtml(T("stations_map_archive_count"))}:</strong> ${archiveCount}</div>`,
    ].join("");
  }

  function stationLabel(item) {
    const city = String(item.city || "").trim();
    const region = String(item.region || "").trim();
    const country = String(item.country || "").trim();
    const geoParts = [city, region, country].filter(Boolean);
    if (geoParts.length) {
      return geoParts.join(", ");
    }
    const markerName = String(item.marker_name || "").trim();
    if (markerName) {
      return markerName;
    }
    return String(item.station_id || "").trim();
  }

  function stationCategory(item) {
    const archives = Number(item.archive_count || 0);
    if (archives >= 10) {
      return "dense";
    }
    if (archives >= 4) {
      return "regular";
    }
    return "light";
  }

  function toNumber(value) {
    const num = typeof value === "number" ? value : Number.parseFloat(String(value || ""));
    return Number.isFinite(num) ? num : NaN;
  }

  const BACKGROUND_STORAGE_KEY = "converterhub-stations-map-background";
  const LABEL_FONT = "Open Sans Regular";

  function useOsm() {
    return !backgroundSelect || backgroundSelect.value !== "outline";
  }

  // OpenStreetMap raster tiles for Plotly's MapLibre map. Station labels are
  // drawn from glyphs the hub serves (scripts/fetch_vendor_assets.py).
  function osmStyle() {
    const dark = document.documentElement.getAttribute("data-theme") !== "light";
    return {
      version: 8,
      glyphs: `${window.location.origin}/static/vendor/maplibre/glyphs/{fontstack}/{range}.pbf`,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          maxzoom: 19,
          attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        },
      },
      layers: [{
        id: "osm",
        type: "raster",
        source: "osm",
        // Dark theme: invert the tiles' brightness, then turn the hues back
        // so that water stays blue and parks green.
        paint: dark
          ? { "raster-brightness-min": 0.85, "raster-brightness-max": 0.08, "raster-hue-rotate": 180, "raster-saturation": -0.35 }
          : {},
      }],
    };
  }

  // Center and zoom that fit every station into a Web Mercator map of the given size.
  function fitView(points, width, height) {
    const mercatorY = (lat) => Math.log(Math.tan(Math.PI / 4 + (Math.max(-85, Math.min(85, lat)) * Math.PI) / 360));
    const lons = points.map((point) => point.lon);
    const ys = points.map((point) => mercatorY(point.lat));
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const midY = (minY + maxY) / 2;
    // MapLibre's world is 512 px wide at zoom 0.
    const zoomX = Math.log2((width * 360) / (512 * Math.max(maxLon - minLon, 1e-6)));
    const zoomY = Math.log2((height * 2 * Math.PI) / (512 * Math.max(maxY - minY, 1e-6)));
    return {
      center: { lon: (minLon + maxLon) / 2, lat: (Math.atan(Math.exp(midY)) * 360) / Math.PI - 90 },
      // A margin for markers and labels at the edges; a lone station gets a town-scale view.
      zoom: Math.max(0, Math.min(zoomX, zoomY, 10) - 0.3),
    };
  }

  async function renderPlot(payload) {
    if (!plotEl) return true;
    if (!window.Plotly) {
      throw new Error("Plotly is not available in the browser.");
    }
    const stationsRaw = Array.isArray(payload.stations) ? payload.stations : [];
    const points = stationsRaw
      .map((item) => ({
        item,
        lon: toNumber(item.longitude_deg),
        lat: toNumber(item.latitude_deg),
      }))
      .filter((point) => Number.isFinite(point.lon) && Number.isFinite(point.lat));

    if (!points.length) {
      window.Plotly.purge(plotEl);
      plotEl.innerHTML = `<div class="station-map-empty">${escapeHtml(T("stations_map_empty"))}</div>`;
      state.plotted = null;
      return true;
    }

    const osm = useOsm();
    const background = osm ? "osm" : "outline";
    // A different kind of map, or the empty message, needs a fresh plot;
    // otherwise Plotly.react updates the plot in place.
    if (state.plotted !== background) {
      window.Plotly.purge(plotEl);
      plotEl.innerHTML = "";
    }
    const showLabels = labelsCheckbox && labelsCheckbox.checked;

    const categories = [
      {
        key: "dense",
        name: "Dense coverage: 10+ archives",
        color: "#2f6b4f",
      },
      {
        key: "regular",
        name: "Regular coverage: 4-9 archives",
        color: "#8a6a3d",
      },
      {
        key: "light",
        name: "Light coverage: 1-3 archives",
        color: "#a65b3b",
      },
    ];

    const traces = [];
    for (const category of categories) {
      const subset = points.filter((point) => stationCategory(point.item) === category.key);
      if (!subset.length) continue;

      const customdata = subset.map((point) => ([
        String(point.item.marker_name || ""),
        String(point.item.marker_number || ""),
        Number(point.item.archive_count || 0),
        Array.isArray(point.item.days) ? point.item.days.join(", ") : "",
        Number(point.item.altitude_m || 0).toFixed(1),
        Number(point.item.coordinate_spread_m || 0).toFixed(1),
        String(point.item.first_archive || ""),
        String(point.item.last_archive || ""),
        stationLabel(point.item),
      ]));

      const marker = {
        size: subset.map((point) => Math.max(10, Math.min(22, 10 + Math.log2(Math.max(Number(point.item.archive_count || 0), 1)) * 3))),
        color: category.color,
        opacity: 0.94,
      };
      if (!osm) {
        // MapLibre's circles take no outline.
        marker.line = {
          color: cssVar("--surface", "#0F0E0D"),
          width: 1,
        };
      }

      traces.push({
        type: osm ? "scattermap" : "scattergeo",
        mode: "markers",
        name: category.name,
        lon: subset.map((point) => point.lon),
        lat: subset.map((point) => point.lat),
        text: subset.map((point) => String(point.item.station_id || "")),
        customdata,
        marker,
        hovertemplate: [
          "<b>%{text}</b>",
          "Locality label: %{customdata[8]}",
          "Marker name: %{customdata[0]}",
          "Marker number: %{customdata[1]}",
          "Archives: %{customdata[2]}",
          "Days: %{customdata[3]}",
          "Altitude: %{customdata[4]} m",
          "Coordinate spread: %{customdata[5]} m",
          "First archive: %{customdata[6]}",
          "Last archive: %{customdata[7]}",
          "<extra></extra>",
        ].join("<br>"),
      });
    }

    if (showLabels) {
      const labels = {
        type: osm ? "scattermap" : "scattergeo",
        mode: "text",
        name: "Labels",
        lon: points.map((point) => point.lon),
        lat: points.map((point) => point.lat),
        text: points.map((point) => stationLabel(point.item) || String(point.item.station_id || "")),
        textposition: "bottom center",
        textfont: osm
          ? { family: LABEL_FONT, size: 11, color: cssVar("--text", "#EAE4D8") }
          : { size: 10, color: cssVar("--text-muted", "#655A4A") },
        hoverinfo: "skip",
        showlegend: false,
      };
      if (osm) {
        // Plotly's MapLibre map offsets text by the marker size, which a
        // text-only trace lacks (the offset becomes NaN and the map fails):
        // invisible markers provide it.
        labels.mode = "markers+text";
        labels.marker = { size: 14, opacity: 0 };
      }
      traces.push(labels);
    }

    const layout = {
      autosize: true,
      height: Math.max(plotEl.clientHeight || 0, 700),
      margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)",
      hovermode: "closest",
      // Keeps the user's zoom and pan while only labels or the theme change.
      uirevision: `${payload.year}|${payload.day}|${background}`,
      legend: {
        // Above the map rather than over it: Plotly grows the top margin to fit.
        orientation: "h",
        x: 0,
        y: 1,
        xanchor: "left",
        yanchor: "bottom",
        bgcolor: "rgba(0,0,0,0)",
        title: {
          text: "Archive coverage",
          font: { color: cssVar("--text-muted", "#7A7268"), size: 11 },
        },
        font: {
          color: cssVar("--text", "#EAE4D8"),
        },
      },
    };
    if (osm) {
      layout.map = {
        style: osmStyle(),
        ...fitView(points, plotEl.clientWidth || 900, layout.height - 40),
      };
    } else {
      layout.geo = {
        scope: "world",
        projection: { type: "natural earth" },
        fitbounds: "locations",
        bgcolor: cssVar("--surface-2", "#161512"),
        showland: true,
        landcolor: cssVar("--surface-3", "#1D1B18"),
        showocean: true,
        oceancolor: cssVar("--surface-2", "#161512"),
        showlakes: true,
        lakecolor: cssVar("--surface-2", "#161512"),
        showcountries: true,
        countrycolor: cssVar("--border", "#242220"),
        showcoastlines: true,
        coastlinecolor: cssVar("--border-light", "#2E2C28"),
        lonaxis: { showgrid: true, gridcolor: cssVar("--border", "#242220") },
        lataxis: { showgrid: true, gridcolor: cssVar("--border", "#242220") },
      };
    }

    try {
      await window.Plotly.react(plotEl, traces, layout, {
        responsive: true,
        displayModeBar: false,
        // Map outlines from the hub itself: by default Plotly fetches them from
        // cdn.plot.ly, which the CSP blocks and an offline network can't reach.
        topojsonURL: "/static/vendor/plotly/topojson/",
      });
    } catch (error) {
      if (!osm) throw error;
      // Tiles that can't be fetched (no internet) fail the whole plot with
      // a bare "Map error.", though the stations are drawn regardless.
      state.plotted = background;
      return false;
    }
    state.plotted = background;
    await window.Plotly.Plots.resize(plotEl);
    return true;
  }

  // Status line for a finished render: renderPlot is false when the
  // OpenStreetMap background failed to load.
  function setPlotStatus(drawn) {
    if (drawn) {
      setStatus("info", T("stations_map_loaded"));
    } else {
      setStatus("error", T("stations_map_tiles_failed"));
    }
  }

  function rerenderPlot() {
    if (!state.payload) return;
    renderPlot(state.payload).then(setPlotStatus, (error) => {
      const message = error instanceof Error ? error.message : T("stations_map_error");
      setStatus("error", message);
    });
  }

  function renderTable(payload) {
    if (!tableWrapEl) return;
    const stations = Array.isArray(payload.stations) ? payload.stations : [];
    if (!stations.length) {
      tableWrapEl.innerHTML = `<div style="font-size:12px; color:var(--text-muted);">${escapeHtml(T("stations_map_table_empty"))}</div>`;
      return;
    }

    const rows = stations.map((item) => {
      const days = Array.isArray(item.days) ? item.days.join(", ") : "";
      return `
        <tr>
          <td class="td-mono">${String(item.station_id || "")}</td>
          <td>${String(item.marker_name || "-")}</td>
          <td class="td-mono">${String(item.marker_number || "-")}</td>
          <td class="td-mono">${Number(item.latitude_deg || 0).toFixed(4)}</td>
          <td class="td-mono">${Number(item.longitude_deg || 0).toFixed(4)}</td>
          <td class="td-mono">${Number(item.altitude_m || 0).toFixed(1)}</td>
          <td class="td-mono">${Number(item.archive_count || 0)}</td>
          <td class="td-mono">${days || "-"}</td>
        </tr>
      `;
    }).join("");

    tableWrapEl.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>${escapeHtml(T("analysis_label_station"))}</th>
            <th>${escapeHtml(T("stations_map_marker_name"))}</th>
            <th>${escapeHtml(T("stations_map_marker_number"))}</th>
            <th>${escapeHtml(T("stations_map_latitude"))}</th>
            <th>${escapeHtml(T("stations_map_longitude"))}</th>
            <th>${escapeHtml(T("stations_map_altitude"))}</th>
            <th>${escapeHtml(T("stations_map_archive_count"))}</th>
            <th>${escapeHtml(T("stations_map_days"))}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  async function loadStationMap(forceRefresh) {
    if (!yearSelect || !daySelect) return;
    const year = String(yearSelect.value || "").trim();
    if (!year) {
      setStatus("error", T("stations_map_no_year"));
      return;
    }

    const params = new URLSearchParams({ year, day: String(daySelect.value || "") });
    if (forceRefresh) {
      params.set("refresh", "true");
    }

    const requestSeq = (state.loadSeq || 0) + 1;
    state.loadSeq = requestSeq;

    loadButton.disabled = true;
    refreshButton.disabled = true;
    setStatus("info", T("stations_map_loading"));

    try {
      const response = await fetch(`/stations-map/data?${params.toString()}`, {
        headers: { "Accept": "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(payload.detail || response.statusText || "Request failed"));
      }

      if (state.loadSeq !== requestSeq) {
        return;
      }
      state.payload = payload;
      renderSummary(payload);
      renderTable(payload);
      setPlotStatus(await renderPlot(payload));
    } catch (error) {
      if (state.loadSeq !== requestSeq) {
        return;
      }
      const message = error instanceof Error ? error.message : T("stations_map_error");
      setStatus("error", message);
    } finally {
      if (state.loadSeq === requestSeq) {
        loadButton.disabled = false;
        refreshButton.disabled = false;
      }
    }
  }

  if (yearSelect && daySelect && loadButton && refreshButton && state.tree.length) {
    populateYearOptions();
    populateDayOptions();
    yearSelect.addEventListener("change", () => {
      populateDayOptions("");
      loadStationMap(false);
    });
    daySelect.addEventListener("change", () => loadStationMap(false));
    loadButton.addEventListener("click", () => loadStationMap(false));
    refreshButton.addEventListener("click", () => loadStationMap(true));
    if (labelsCheckbox) {
      labelsCheckbox.addEventListener("change", rerenderPlot);
    }
    if (backgroundSelect) {
      try {
        const saved = window.localStorage.getItem(BACKGROUND_STORAGE_KEY);
        if (saved === "osm" || saved === "outline") backgroundSelect.value = saved;
      } catch (_) {}
      backgroundSelect.addEventListener("change", () => {
        try { window.localStorage.setItem(BACKGROUND_STORAGE_KEY, backgroundSelect.value); } catch (_) {}
        rerenderPlot();
      });
    }
    // Map colours come from the theme's CSS variables.
    new MutationObserver(rerenderPlot).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    loadStationMap(false);
  }
})();
