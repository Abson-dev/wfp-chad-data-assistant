// Draws a chart from the server's checked description. The model never supplies a specification.
window.wfpChart = (function () {
  const INK = "#1F2933", MUTED = "#6B7785", GRID = "#E6E9EE", ACCENT = "#0F7C80", SERIES = { "Post-harvest": "#0F7C80", "Lean season": "#C17F3A" };

  function axisConfig() {
    return {
      font: "Inter, 'Segoe UI', Arial, sans-serif",
      axis: { labelColor: MUTED, titleColor: MUTED, gridColor: GRID, domainColor: GRID, tickColor: GRID, labelFontSize: 11, titleFontSize: 11 },
      legend: { labelColor: INK, titleColor: MUTED, orient: "top" },
      view: { stroke: null },
    };
  }

  function spec(pub) {
    const c = pub.chart, cat = (f) => ({ field: f, type: "nominal", title: null });
    // "value" says nothing on an axis: the result's title names the indicator.
    const num = (f) => ({ field: f, type: "quantitative", title: ["value", "as_merged"].includes(f) ? null : f.replace(/_/g, " ") });
    const base = { $schema: "https://vega.github.io/schema/vega-lite/v6.json", width: "container", height: c.type === "heatmap" ? 440 : 300,
      data: { values: pub.rows }, config: axisConfig() };
    const colour = c.series
      ? { field: c.series, type: "nominal", title: null, scale: c.series === "series" ? { domain: Object.keys(SERIES), range: Object.values(SERIES) } : undefined }
      : { value: ACCENT };
    const xCat = { ...cat(c.x), sort: c.x_order || null, axis: { labelAngle: -40 } };
    const tip = pub.columns.map((f) => ({ field: f }));

    if (c.type === "line") {
      const enc = { x: xCat, y: num(c.y), color: colour, tooltip: tip };
      return { ...base, layer: [
        { mark: { type: "line", strokeWidth: 2.5 }, encoding: enc },
        { transform: [{ filter: "datum.flagged !== true" }], mark: { type: "point", size: 70, filled: true, opacity: 1 }, encoding: enc },
        // Hollow where the cell has a known defect in the merged file.
        { transform: [{ filter: "datum.flagged === true" }], mark: { type: "point", size: 80, strokeWidth: 2.5, filled: false, fill: "#FFFFFF", opacity: 1 }, encoding: enc },
      ] };
    }
    if (c.type === "bar") {
      const yNumeric = pub.rows.some((r) => typeof r[c.y] === "number");
      const enc = yNumeric ? { x: { ...cat(c.x), sort: c.x_order || "-y", axis: { labelAngle: -40 } }, y: num(c.y) } : { y: { ...cat(c.y), sort: "-x" }, x: num(c.x) };
      if (c.series) { enc.color = colour; enc.xOffset = { field: c.series }; } else { enc.color = colour; }
      return { ...base, mark: { type: "bar" }, encoding: { ...enc, tooltip: tip } };
    }
    if (c.type === "scatter") {
      return { ...base, mark: { type: "point", size: 80, filled: true, color: ACCENT }, encoding: { x: num(c.x), y: num(c.y), tooltip: tip } };
    }
    return { ...base, mark: { type: "rect" }, encoding: { x: xCat, y: { ...cat(c.y) },
      color: { field: c.series, type: "quantitative", title: null, scale: { scheme: "orangered" } }, tooltip: tip } };
  }

  async function draw(container, pub) {
    if (!pub.chart || !window.vegaEmbed) return false;
    await window.vegaEmbed(container, spec(pub), { actions: false, renderer: "svg", ast: true, expr: window.vegaInterpreter && window.vegaInterpreter.expressionInterpreter });
    return true;
  }
  return { draw, spec };
})();
