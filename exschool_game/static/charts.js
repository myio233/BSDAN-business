(function () {
  const palette = ["#f54e00", "#1f8a65", "#1f5a8a", "#c08532", "#cf2d56", "#6b7e4d", "#9b6b3d", "#2f6f6f", "#8a4c7d", "#444a88"];

  function formatMoney(value) {
    return `¥${Math.round(Number(value || 0)).toLocaleString("en-US")}`;
  }

  function niceStep(rawStep) {
    const safeValue = Math.max(rawStep, 1);
    const exponent = Math.floor(Math.log10(safeValue));
    const base = 10 ** exponent;
    const fraction = safeValue / base;
    if (fraction <= 1) return base;
    if (fraction <= 2) return 2 * base;
    if (fraction <= 5) return 5 * base;
    return 10 * base;
  }

  function normalizeSeries(rawSeries) {
    return (Array.isArray(rawSeries) ? rawSeries : [])
      .map((item, index) => ({
        label: String(item.label || `Series ${index + 1}`),
        highlight: Boolean(item.highlight),
        color: item.highlight ? "#f54e00" : palette[index % palette.length],
        points: (Array.isArray(item.points) ? item.points : []).map((point) => ({
          label: String(point.label || ""),
          value: point.value == null ? null : Number(point.value),
        })),
      }))
      .filter((item) => item.points.length > 0);
  }

  function renderLegend(container, series, activeMap, rerender) {
    if (!container) return;
    container.innerHTML = "";
    series.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chart-legend-item";
      button.dataset.active = String(activeMap.get(item.label) !== false);
      button.innerHTML = `<span class="chart-legend-swatch" style="background:${item.color}"></span><span>${item.label}</span>`;
      button.addEventListener("click", () => {
        activeMap.set(item.label, activeMap.get(item.label) === false);
        renderLegend(container, series, activeMap, rerender);
        rerender();
      });
      container.appendChild(button);
    });
  }

  function renderAllCompanyTrend(options) {
    const canvas = options?.canvas;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const tooltip = options?.tooltip || null;
    const legend = options?.legend || null;
    const controls = options?.controls || {};
    const padding = { top: 20, right: 22, bottom: 42, left: 76 };
    const series = normalizeSeries(JSON.parse(canvas.dataset.series || "[]"));
    if (series.length === 0) return;

    const activeMap = new Map(series.map((item) => [item.label, true]));
    const state = { zoom: 1, hovered: null };
    const labels = series[0].points.map((point) => point.label);

    const yForValue = (value, chartMin, chartMax, innerHeight) => {
      const ratio = (value - chartMin) / (chartMax - chartMin || 1);
      return height - padding.bottom - ratio * innerHeight;
    };

    const getVisibleSeries = () => series.filter((item) => activeMap.get(item.label) !== false);

    const draw = () => {
      const visibleSeries = getVisibleSeries().filter((item) => item.points.some((point) => point.value !== null));
      if (visibleSeries.length === 0) {
        ctx.clearRect(0, 0, width, height);
        return;
      }
      const values = visibleSeries.flatMap((item) => item.points.map((point) => point.value).filter((value) => value !== null));
      const minValue = Math.min(...values);
      const maxValue = Math.max(...values);
      const baseRange = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.12, 1);
      const zoomedRange = Math.max(baseRange / state.zoom, 1);
      const center = (maxValue + minValue) / 2;
      const chartMin = Math.floor((center - zoomedRange / 2 - zoomedRange * 0.12) / niceStep(zoomedRange / 4)) * niceStep(zoomedRange / 4);
      const chartMax = Math.ceil((center + zoomedRange / 2 + zoomedRange * 0.12) / niceStep(zoomedRange / 4)) * niceStep(zoomedRange / 4);
      const innerWidth = width - padding.left - padding.right;
      const innerHeight = height - padding.top - padding.bottom;
      const stepX = labels.length > 1 ? innerWidth / (labels.length - 1) : 0;

      ctx.clearRect(0, 0, width, height);
      ctx.textBaseline = "middle";
      ctx.font = "12px system-ui";

      for (let i = 0; i <= 4; i += 1) {
        const tickValue = chartMin + ((chartMax - chartMin) * i) / 4;
        const y = yForValue(tickValue, chartMin, chartMax, innerHeight);
        ctx.strokeStyle = "rgba(38, 37, 30, 0.08)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();
        ctx.fillStyle = "rgba(38, 37, 30, 0.55)";
        ctx.textAlign = "left";
        ctx.fillText(formatMoney(tickValue), 14, y);
      }

      ctx.strokeStyle = "rgba(38, 37, 30, 0.24)";
      ctx.beginPath();
      ctx.moveTo(padding.left, padding.top);
      ctx.lineTo(padding.left, height - padding.bottom);
      ctx.lineTo(width - padding.right, height - padding.bottom);
      ctx.stroke();

      labels.forEach((label, index) => {
        const x = padding.left + stepX * index;
        ctx.fillStyle = "rgba(38, 37, 30, 0.65)";
        ctx.textAlign = "center";
        ctx.fillText(label, x, height - 20);
      });

      visibleSeries.forEach((item) => {
        const isHovered = state.hovered && state.hovered.seriesLabel === item.label;
        ctx.strokeStyle = item.color;
        ctx.lineWidth = item.highlight || isHovered ? 3 : 1.25;
        ctx.globalAlpha = item.highlight || isHovered ? 1 : 0.42;
        ctx.beginPath();
        let started = false;
        item.points.forEach((point, pointIndex) => {
          if (point.value === null) return;
          const x = padding.left + stepX * pointIndex;
          const y = yForValue(point.value, chartMin, chartMax, innerHeight);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();

        item.points.forEach((point, pointIndex) => {
          if (point.value === null) return;
          const x = padding.left + stepX * pointIndex;
          const y = yForValue(point.value, chartMin, chartMax, innerHeight);
          ctx.beginPath();
          ctx.fillStyle = item.color;
          ctx.globalAlpha = item.highlight || isHovered ? 1 : 0.6;
          ctx.arc(x, y, item.highlight || isHovered ? 3.8 : 2.2, 0, Math.PI * 2);
          ctx.fill();
        });
      });

      ctx.globalAlpha = 1;

      const labelTargets = visibleSeries.filter((item) => item.highlight || (state.hovered && state.hovered.seriesLabel === item.label));
      labelTargets.forEach((item) => {
        let lastPointIndex = -1;
        for (let index = item.points.length - 1; index >= 0; index -= 1) {
          if (item.points[index].value !== null) {
            lastPointIndex = index;
            break;
          }
        }
        if (lastPointIndex < 0) return;
        const point = item.points[lastPointIndex];
        if (point.value === null) return;
        const x = padding.left + stepX * lastPointIndex;
        const y = yForValue(point.value, chartMin, chartMax, innerHeight);
        ctx.fillStyle = item.color;
        ctx.textAlign = "left";
        ctx.fillText(item.label, x + 12, y);
      });
    };

    const updateTooltip = (event) => {
      const rect = canvas.getBoundingClientRect();
      const scaleX = width / rect.width;
      const scaleY = height / rect.height;
      const mouseX = (event.clientX - rect.left) * scaleX;
      const mouseY = (event.clientY - rect.top) * scaleY;
      const visibleSeries = getVisibleSeries();
      const values = visibleSeries.flatMap((item) => item.points.map((point) => point.value).filter((value) => value !== null));
      if (visibleSeries.length === 0 || values.length === 0) return;
      const minValue = Math.min(...values);
      const maxValue = Math.max(...values);
      const baseRange = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.12, 1);
      const zoomedRange = Math.max(baseRange / state.zoom, 1);
      const center = (maxValue + minValue) / 2;
      const chartMin = Math.floor((center - zoomedRange / 2 - zoomedRange * 0.12) / niceStep(zoomedRange / 4)) * niceStep(zoomedRange / 4);
      const chartMax = Math.ceil((center + zoomedRange / 2 + zoomedRange * 0.12) / niceStep(zoomedRange / 4)) * niceStep(zoomedRange / 4);
      const innerWidth = width - padding.left - padding.right;
      const innerHeight = height - padding.top - padding.bottom;
      const stepX = labels.length > 1 ? innerWidth / (labels.length - 1) : 0;
      let nearest = null;

      visibleSeries.forEach((item) => {
        item.points.forEach((point, pointIndex) => {
          if (point.value === null) return;
          const x = padding.left + stepX * pointIndex;
          const y = yForValue(point.value, chartMin, chartMax, innerHeight);
          const distance = Math.hypot(x - mouseX, y - mouseY);
          if (!nearest || distance < nearest.distance) {
            nearest = { distance, x, y, point, pointIndex, seriesLabel: item.label, color: item.color };
          }
        });
      });

      if (!nearest || nearest.distance > 18) {
        state.hovered = null;
        if (tooltip) tooltip.hidden = true;
        draw();
        return;
      }

      state.hovered = nearest;
      if (tooltip) {
        tooltip.hidden = false;
        tooltip.innerHTML = `<strong>${nearest.seriesLabel}</strong><br>${nearest.point.label} · ${formatMoney(nearest.point.value)}`;
        tooltip.style.left = `${event.clientX - rect.left}px`;
        tooltip.style.top = `${event.clientY - rect.top}px`;
      }
      draw();
    };

    controls.zoomIn?.addEventListener("click", () => {
      state.zoom = Math.min(state.zoom * 1.4, 8);
      draw();
    });
    controls.zoomOut?.addEventListener("click", () => {
      state.zoom = Math.max(state.zoom / 1.4, 1);
      draw();
    });
    controls.zoomReset?.addEventListener("click", () => {
      state.zoom = 1;
      draw();
    });

    canvas.addEventListener("mousemove", updateTooltip);
    canvas.addEventListener("mouseleave", () => {
      state.hovered = null;
      if (tooltip) tooltip.hidden = true;
      draw();
    });

    renderLegend(legend, series, activeMap, draw);
    draw();
  }

  window.ExschoolCharts = {
    renderAllCompanyTrend,
  };
})();
