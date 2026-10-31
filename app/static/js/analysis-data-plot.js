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
      // Column-oriented series must be zipped, not concatenated.
      if (arrays.length && arrays.every(([, v]) => v.every((item) => item == null || typeof item !== "object")) &&
          (arrays.length > 1 || Object.keys(base).length === 0)) {
        const length = Math.max(...arrays.map(([, v]) => v.length));
        return Array.from({ length }, (_, i) => ({ ...base, ...Object.fromEntries(arrays.map(([k, v]) => [k, v[i] ?? null])) }));
      }
      const children = Object.entries(value).filter(([k, v]) => v && typeof v === "object" && !["meta", "metadata", "layout"].includes(k));
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

  window.AnalysisDataPlot = { flattenRows, numericColumns, buildFigure };
})();
