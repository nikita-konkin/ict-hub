(function () {
  const I18N = JSON.parse(document.getElementById("run-i18n").textContent);
  const T = (key) => I18N[key] ?? key;
  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const profilesNode = document.getElementById("dat-parquet-profiles-data");
  const sourceTreeNode = document.getElementById("dat-parquet-source-tree-data");
  const directionSelect = document.getElementById("direction");
  const profileSelect = document.getElementById("dataset-profile");
  const overwriteCheckbox = document.getElementById("overwrite");
  const srcHidden = document.getElementById("dat-parquet-src-hidden");
  const dstHidden = document.getElementById("dat-parquet-dst-hidden");
  const rootSubpathHidden = document.getElementById("dat-parquet-root-subpath");
  const yearSelect = document.getElementById("dat-parquet-year-select");
  const daySelect = document.getElementById("dat-parquet-day-select");
  const srcPreview = document.getElementById("dat-parquet-src-preview");
  const dstPreview = document.getElementById("dat-parquet-dst-preview");
  const srcHint = document.getElementById("dat-parquet-src-hint");
  const dstHint = document.getElementById("dat-parquet-dst-hint");

  if (!profilesNode || !sourceTreeNode || !directionSelect || !profileSelect || !overwriteCheckbox || !srcHidden || !dstHidden || !rootSubpathHidden || !yearSelect || !daySelect || !srcPreview || !dstPreview || !srcHint || !dstHint) {
    return;
  }

  let profileMatrix = {};
  let sourceTreeMatrix = {};
  try {
    profileMatrix = JSON.parse(profilesNode.textContent || "{}");
  } catch (_) {
    profileMatrix = {};
  }
  try {
    sourceTreeMatrix = JSON.parse(sourceTreeNode.textContent || "{}");
  } catch (_) {
    sourceTreeMatrix = {};
  }

  function joinSubpath(basePath, rootSubpath) {
    const cleanBase = (basePath || "").replace(/[\\/]+$/, "");
    const cleanSuffix = (rootSubpath || "").replace(/^\/+/, "");
    if (!cleanBase) return "";
    if (!cleanSuffix) return cleanBase;
    return cleanBase + "/" + cleanSuffix;
  }

  function getSourceTree() {
    const direction = directionSelect.value || "dat-to-parquet";
    const dirTree = sourceTreeMatrix[direction] || {};
    return dirTree[profileSelect.value] || [];
  }

  function updateRootSubpath() {
    const year = yearSelect.value;
    const day = daySelect.value;
    if (!year) {
      rootSubpathHidden.value = "";
      return;
    }
    rootSubpathHidden.value = day ? ("/" + year + "/" + day) : ("/" + year);
  }

  function repopulateDays() {
    const tree = getSourceTree();
    const selectedYear = yearSelect.value;
    const yearItem = tree.find(function (item) {
      return item.year === selectedYear;
    }) || null;

    daySelect.innerHTML = "";
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = T("option_all_days_selected_year");
    daySelect.appendChild(allOption);

    if (!yearItem || !Array.isArray(yearItem.days) || yearItem.days.length === 0) {
      daySelect.disabled = true;
      daySelect.value = "";
      updateRootSubpath();
      return;
    }

    yearItem.days.forEach(function (day) {
      const option = document.createElement("option");
      option.value = day;
      option.textContent = day;
      daySelect.appendChild(option);
    });
    daySelect.disabled = false;
    daySelect.value = "";
    updateRootSubpath();
  }

  function repopulateYears() {
    const tree = getSourceTree();
    const currentYear = yearSelect.value;
    yearSelect.innerHTML = "";

    const rootOption = document.createElement("option");
    rootOption.value = "";
    rootOption.textContent = T("run_option_whole_source_root");
    yearSelect.appendChild(rootOption);

    if (!Array.isArray(tree) || tree.length === 0) {
      yearSelect.disabled = true;
      daySelect.disabled = true;
      daySelect.innerHTML = '<option value="">' + escapeHtml(T("option_all_days_selected_year")) + '</option>';
      yearSelect.value = "";
      daySelect.value = "";
      updateRootSubpath();
      return;
    }

    tree.forEach(function (item) {
      const option = document.createElement("option");
      option.value = item.year;
      option.textContent = item.year;
      if (item.year === currentYear) {
        option.selected = true;
      }
      yearSelect.appendChild(option);
    });

    yearSelect.disabled = false;
    if (!yearSelect.value && yearSelect.options.length > 0) {
      yearSelect.value = yearSelect.options[0].value;
    }
    repopulateDays();
  }

  function rebuildProfiles() {
    const direction = directionSelect.value || "dat-to-parquet";
    const profiles = profileMatrix[direction] || {};
    const existingValue = profileSelect.value;
    profileSelect.innerHTML = "";
    Object.keys(profiles).forEach(function (key) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = profiles[key].label;
      if (key === existingValue) {
        option.selected = true;
      }
      profileSelect.appendChild(option);
    });
    if (!profileSelect.value && profileSelect.options.length > 0) {
      profileSelect.value = profileSelect.options[0].value;
    }
    updatePaths();
  }

  function updatePaths() {
    const direction = directionSelect.value || "dat-to-parquet";
    const profiles = profileMatrix[direction] || {};
    const profile = profiles[profileSelect.value] || null;
    if (!profile) {
      srcHidden.value = "";
      dstHidden.value = "";
      srcPreview.textContent = "";
      dstPreview.textContent = "";
      srcHint.textContent = T("run_no_source_profile");
      dstHint.textContent = T("run_no_destination_profile");
      yearSelect.disabled = true;
      daySelect.disabled = true;
      rootSubpathHidden.value = "";
      return;
    }

    repopulateYears();
    const overwrite = overwriteCheckbox.checked;
    const src = profile.src || "";
    const dst = profile.dst || "";
    const rootSubpath = rootSubpathHidden.value || "";
    srcHidden.value = joinSubpath(src, rootSubpath);
    dstHidden.value = joinSubpath(dst, rootSubpath);
    srcPreview.textContent = srcHidden.value;
    dstPreview.textContent = dstHidden.value;
    srcHint.textContent = profile.src_env ? ("Configured from environment variable " + profile.src_env + ".") : "";
    const dstSource = profile.dst_env ? ("Configured from environment variable " + profile.dst_env + ".") : "";
    dstHint.textContent = overwrite ? (dstSource + " " + T("run_hint_overwrite_enabled")).trim() : dstSource;
  }

  directionSelect.addEventListener("change", rebuildProfiles);
  profileSelect.addEventListener("change", updatePaths);
  overwriteCheckbox.addEventListener("change", updatePaths);
  yearSelect.addEventListener("change", function () {
    repopulateDays();
    updatePaths();
  });
  daySelect.addEventListener("change", function () {
    updateRootSubpath();
    updatePaths();
  });

  rebuildProfiles();
})();
