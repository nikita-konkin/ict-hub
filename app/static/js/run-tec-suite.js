/* TEC-Suite run page: year/day folder pickers over the RINEX tree. */
(function () {
  const I18N = JSON.parse(document.getElementById("run-i18n").textContent);

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
    allOption.textContent = I18N.option_all_days_selected_year;
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

    for (const dayItem of yearItem.days) {
      const option = document.createElement("option");
      option.value = dayItem.day;
      option.textContent = dayItem.day + " (" + dayItem.stations + " " + I18N.run_unit_stations + ")";
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
