/* AbsTEC run page: year, day and site multi-selects over the TEC-suite DAT tree. */
(function () {
  const treeDataNode = document.getElementById("abstec-dat-tree-data");
  const yearSelect = document.getElementById("abstec-year-select");
  const dayOfYearSelect = document.getElementById("abstec-day-of-year-select");
  const daysSelect = document.getElementById("abstec-days-select");
  const siteSelect = document.getElementById("abstec-site-select");
  const yearHidden = document.getElementById("abstec-year-hidden");
  const dayOfYearHidden = document.getElementById("abstec-day-of-year-hidden");
  const daysHidden = document.getElementById("abstec-days-hidden");
  const siteHidden = document.getElementById("abstec-site-hidden");
  const yearChips = document.getElementById("abstec-year-chips");
  const dayOfYearChips = document.getElementById("abstec-day-of-year-chips");
  const daysChips = document.getElementById("abstec-days-chips");
  const siteChips = document.getElementById("abstec-site-chips");

  if (
    !treeDataNode || !yearSelect || !dayOfYearSelect || !daysSelect || !siteSelect ||
    !yearHidden || !dayOfYearHidden || !daysHidden || !siteHidden ||
    !yearChips || !dayOfYearChips || !daysChips || !siteChips
  ) {
    return;
  }

  let tree = [];
  try {
    tree = JSON.parse(treeDataNode.textContent || "[]");
  } catch (_) {
    tree = [];
  }

  function selectedValues(selectNode) {
    return Array.from(selectNode.selectedOptions).map(function (opt) {
      return opt.value;
    });
  }

  function setHiddenFromSelect(selectNode, hiddenNode) {
    hiddenNode.value = selectedValues(selectNode).join(",");
  }

  function renderChips(containerNode, values) {
    containerNode.innerHTML = "";
    if (!values || values.length === 0) {
      const emptyChip = document.createElement("span");
      emptyChip.className = "badge badge-muted";
      emptyChip.textContent = "None selected";
      containerNode.appendChild(emptyChip);
      return;
    }
    values.forEach(function (value) {
      const chip = document.createElement("span");
      chip.className = "badge badge-info";
      chip.textContent = value;
      containerNode.appendChild(chip);
    });
  }

  function syncSelect(selectNode, hiddenNode, chipNode) {
    const values = selectedValues(selectNode);
    hiddenNode.value = values.join(",");
    renderChips(chipNode, values);
  }

  function clearSelect(selectNode) {
    Array.from(selectNode.options).forEach(function (opt) {
      opt.selected = false;
    });
  }

  function firstSelectedYear() {
    const years = selectedValues(yearSelect);
    return years.length > 0 ? years[0] : "";
  }

  function findYear(year) {
    return tree.find(function (item) {
      return item.year === year;
    }) || null;
  }

  function repopulateDayOptions() {
    const year = firstSelectedYear();
    const yearItem = year ? findYear(year) : null;

    dayOfYearSelect.innerHTML = "";
    daysSelect.innerHTML = "";

    if (!yearItem || !Array.isArray(yearItem.days)) {
      syncSelect(dayOfYearSelect, dayOfYearHidden, dayOfYearChips);
      syncSelect(daysSelect, daysHidden, daysChips);
      repopulateSites();
      return;
    }

    yearItem.days.forEach(function (dayItem) {
      const label = dayItem.day + " (" + (Array.isArray(dayItem.sites) ? dayItem.sites.length : 0) + " sites)";

      const daySingle = document.createElement("option");
      daySingle.value = dayItem.day;
      daySingle.textContent = label;
      dayOfYearSelect.appendChild(daySingle);

      const dayBatch = document.createElement("option");
      dayBatch.value = dayItem.day;
      dayBatch.textContent = label;
      daysSelect.appendChild(dayBatch);
    });

    syncSelect(dayOfYearSelect, dayOfYearHidden, dayOfYearChips);
    syncSelect(daysSelect, daysHidden, daysChips);
    repopulateSites();
  }

  function repopulateSites() {
    const year = firstSelectedYear();
    const yearItem = year ? findYear(year) : null;
    const selectedSingleDays = selectedValues(dayOfYearSelect);
    const selectedBatchDays = selectedValues(daysSelect);
    const selectedDaySet = new Set(selectedSingleDays.concat(selectedBatchDays));

    siteSelect.innerHTML = "";
    if (!yearItem || !Array.isArray(yearItem.days)) {
      syncSelect(siteSelect, siteHidden, siteChips);
      return;
    }

    const siteSet = new Set();
    yearItem.days.forEach(function (dayItem) {
      if (selectedDaySet.size > 0 && !selectedDaySet.has(dayItem.day)) {
        return;
      }
      (dayItem.sites || []).forEach(function (siteName) {
        siteSet.add(siteName);
      });
    });

    Array.from(siteSet).sort().forEach(function (siteName) {
      const option = document.createElement("option");
      option.value = siteName;
      option.textContent = siteName;
      siteSelect.appendChild(option);
    });

    syncSelect(siteSelect, siteHidden, siteChips);
  }

  yearSelect.addEventListener("change", function () {
    syncSelect(yearSelect, yearHidden, yearChips);
    repopulateDayOptions();
  });

  dayOfYearSelect.addEventListener("change", function () {
    if (selectedValues(dayOfYearSelect).length > 0) {
      clearSelect(daysSelect);
    }
    syncSelect(dayOfYearSelect, dayOfYearHidden, dayOfYearChips);
    syncSelect(daysSelect, daysHidden, daysChips);
    repopulateSites();
  });

  daysSelect.addEventListener("change", function () {
    if (selectedValues(daysSelect).length > 0) {
      clearSelect(dayOfYearSelect);
    }
    syncSelect(daysSelect, daysHidden, daysChips);
    syncSelect(dayOfYearSelect, dayOfYearHidden, dayOfYearChips);
    repopulateSites();
  });

  siteSelect.addEventListener("change", function () {
    syncSelect(siteSelect, siteHidden, siteChips);
  });

  if (tree.length > 0 && yearSelect.options.length > 0) {
    yearSelect.options[0].selected = true;
  }
  syncSelect(yearSelect, yearHidden, yearChips);
  repopulateDayOptions();
})();
