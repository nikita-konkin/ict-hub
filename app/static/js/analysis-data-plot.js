// Shared data-response adapter. Kept independent of the DOM for verification.
(function () {
  function flattenRows(payload) {
    function visit(value, context) {
      if (Array.isArray(value)) {
        return value.flatMap((item) => item && typeof item === "object"
          ? visit(item, context) : [{ ...context, value: item }]);
      }
      if (!value || typeof value !== "object") return [{ ...context, value }];
      const scalars = Object.fromEntries(Object.entries(value).filter(([, v]) => v == null || typeof v !== "object"));
      const base = { ...context, ...scalars };
      const arrays = Object.entries(value).filter(([, v]) => Array.isArray(v));
      const primitiveLists = arrays.filter(([, v]) => v.every((item) => item == null || typeof item !== "object"));
      if (arrays.length && primitiveLists.length === arrays.length && arrays.every(([key]) => /stations$|satellites$|days$/.test(key))) {
        return arrays.flatMap(([key, list]) => list.map((item) => ({ ...base, collection: key, [key]: item, value: item })));
      }
      // Column-oriented series must be zipped, not concatenated.
      if (arrays.length && arrays.every(([, v]) => v.every((item) => item == null || typeof item !== "object"))) {
        const length = Math.max(...arrays.map(([, v]) => v.length));
        return Array.from({ length }, (_, i) => ({ ...base, ...Object.fromEntries(arrays.map(([k, v]) => [k, v[i] ?? null])) }));
      }
      const hasRecords = arrays.some(([, v]) => v.some((item) => item && typeof item === "object"));
      const children = Object.entries(value).filter(([k, v]) => v && typeof v === "object" && !["meta", "metadata", "layout"].includes(k) &&
        !(hasRecords && (!Array.isArray(v) || v.every((item) => item == null || typeof item !== "object"))));
      if (!children.length) return [base];
      return children.flatMap(([key, child]) => visit(child, { ...base, collection: context.collection ? context.collection + "/" + key : key }));
    }
    return visit(payload, {});
  }

  function isNumber(value) {
    return (typeof value === "number" || typeof value === "string" && value.trim() !== "") && Number.isFinite(Number(value));
  }

  function numericColumns(rows) {
    return [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) => rows.some((row) => isNumber(row[key])));
  }

  function defaultAxes(rows, endpoint) {
    const cols = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) => rows.some((row) => row[key] != null && row[key] !== ""));
    const numeric = numericColumns(rows);
    if (endpoint.startsWith("stations/") || /\/(stations|days|satellites)(\/range)?$/.test(endpoint)) {
      return { x: ["stations", "satellites", "days", "station", "satellite", "day", "value"].find((key) => cols.includes(key)) || "", y: "__count__" };
    }
    const x = ["timestamp", "datetime", "date", "concat_ut", "concat_hour", "ut", "hour", "time", "doy", "day"].find((key) => cols.includes(key)) || "";
    const preferred = endpoint.startsWith("propagation/") ? ["b_k", "mean_b_k", "gdd", "mean_gdd", "tec"]
      : endpoint.startsWith("cb/") ? ["cb", "mean_cb", "tec"] : ["tec", "mean_tec", "tec_l1l2", "tec_c1p2"];
    const metadata = [x, "year", "doy", "day", "doy_start", "doy_end", "alpha", "total_days", "f_hz", "n", "validity", "tsn"];
    const y = preferred.find((key) => numeric.includes(key)) || numeric.find((key) => !metadata.includes(key)) || "__count__";
    return { x, y };
  }

  function buildFigure(rows, xKey, yKeys, title) {
    if (yKeys.includes("__count__")) {
      const counts = new Map();
      for (const row of rows) {
        const key = xKey ? row[xKey] : "All records";
        counts.set(key, (counts.get(key) || 0) + 1);
      }
      return { title, xlabel: xKey || "Records", ylabel: "Count", data: rows.length ? [{
        type: "bar", name: "Count", x: [...counts.keys()], y: [...counts.values()],
      }] : [] };
    }
    const groups = new Map();
    for (const [index, row] of rows.entries()) {
      const group = ["station", "satellite", "year", "doy", "day", "collection"]
        .filter((key) => key !== xKey && row[key] != null).map((key) => key + "=" + row[key]).join(" · ");
      for (const yKey of yKeys) {
        const name = [group, yKey].filter(Boolean).join(" · ");
        if (!groups.has(name)) groups.set(name, { x: [], y: [] });
        const series = groups.get(name);
        series.x.push(xKey ? row[xKey] : index + 1);
        series.y.push(isNumber(row[yKey]) ? Number(row[yKey]) : null);
      }
    }
    return {
      title, xlabel: xKey || "Row", ylabel: yKeys.join(", "),
      data: [...groups].filter(([, series]) => series.y.some((v) => v !== null)).map(([name, series]) => ({
        type: "scatter", mode: "lines+markers", name, ...series, connectgaps: false,
      })),
    };
  }

  window.AnalysisDataPlot = { flattenRows, numericColumns, defaultAxes, buildFigure };
})();
