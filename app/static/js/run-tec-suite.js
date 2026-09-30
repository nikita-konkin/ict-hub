(function () {
  const I18N = JSON.parse(document.getElementById("run-i18n").textContent);
  const T = (key) => I18N[key] ?? key;

  let tree = [];
  const treeDataNode = document.getElementById("tec-rinex-tree-data");
  if (treeDataNode && treeDataNode.textContent) {
    try {
      tree = JSON.parse(treeDataNode.textContent);
    } catch (_) {
      tree = [];
    }
  }
  const yearSelect = document.getElementById("tec-year-select");
  const daySelect = document.getElementById("tec-day-select");
  const rootSubpathInput = document.getElementById("tec-root-subpath");

  if (!yearSelect || !daySelect || !rootSubpathInput) {
    return;
  }

  function findYear(yearName) {
    for (const item of tree) {
      if (item.year === yearName) return item;
    }
    return null;
  }

  function updateRootSubpath() {
    const year = yearSelect.value;
    const day = daySelect.value;
    if (!year) {
      rootSubpathInput.value = "";
      return;
    }
    rootSubpathInput.value = day ? "/" + year + "/" + day : "/" + year;
  }

  function repopulateDays() {
    const year = yearSelect.value;
    daySelect.innerHTML = "";

    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = T("option_all_days_selected_year");
    daySelect.appendChild(allOption);

    if (!year) {
      daySelect.disabled = true;
      updateRootSubpath();
      return;
    }

    const yearItem = findYear(year);
    if (!yearItem || !Array.isArray(yearItem.days) || yearItem.days.length === 0) {
      daySelect.disabled = true;
      updateRootSubpath();
      return;
    }

    function computeDoy(yearName, dayName) {
      const yearInt = parseInt(String(yearName).slice(0, 4), 10);
      if (!Number.isFinite(yearInt)) return null;

      const raw = String(dayName || "").trim();
      if (!raw) return null;

      // Expected folder format: "MM/DD" (e.g. "04/21").
      const parts = raw.split("/");
      if (parts.length === 2) {
        const month = parseInt(parts[0], 10);
        const day = parseInt(parts[1], 10);
        if (!Number.isFinite(month) || !Number.isFinite(day)) return null;
        const dateUtc = new Date(Date.UTC(yearInt, month - 1, day));
        if (!Number.isFinite(dateUtc.getTime())) return null;
        const jan1 = new Date(Date.UTC(yearInt, 0, 1));
        return Math.floor((dateUtc - jan1) / 86400000) + 1;
      }

      // Fallback: numeric day-of-year folder ("111").
      if (/^\\d{1,3}$/.test(raw)) {
        const doy = parseInt(raw, 10);
        return doy >= 1 && doy <= 366 ? doy : null;
      }

      return null;
    }

    for (const dayItem of yearItem.days) {
      const option = document.createElement("option");
      option.value = dayItem.day;
      const doy = computeDoy(year, dayItem.day);
      option.textContent = dayItem.day + " (" + dayItem.stations + " " + T("run_unit_stations") + (doy ? (", doy " + doy) : "") + ")";
      daySelect.appendChild(option);
    }
    daySelect.disabled = false;
    daySelect.value = "";
    updateRootSubpath();
  }

  yearSelect.addEventListener("change", repopulateDays);
  daySelect.addEventListener("change", updateRootSubpath);

  if (tree.length > 0) {
    yearSelect.value = tree[0].year;
    repopulateDays();
  } else {
    updateRootSubpath();
  }
})();
