(function () {
  function parseSeries(rawValue) {
    if (typeof rawValue !== "string" || rawValue === "") {
      return [];
    }
    try {
      return JSON.parse(rawValue);
    } catch (error) {
      return [];
    }
  }

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

  function renderNetAssetsTrend(options) {
    const canvas = options?.canvas;
    if (!canvas) {
      return;
    }

    const series = parseSeries(canvas.dataset.series).map((item, index) => ({
      label: String(item?.label || `Round ${index + 1}`),
      value: Number(item?.value || 0),
    }));
    if (series.length === 0) {
      return;
    }

    const tooltip = options?.tooltip || null;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const padding = { top: 22, right: 20, bottom: 42, left: 76 };
    const values = series.map((item) => item.value);
    const minValue = Math.min(...values);
    const maxValue = Math.max(...values);
    const range = Math.max(maxValue - minValue, Math.max(Math.abs(maxValue), 1) * 0.12, 1);
    const step = niceStep(range / 4);
    const chartMin = Math.floor((minValue - range * 0.15) / step) * step;
    const chartMax = Math.ceil((maxValue + range * 0.15) / step) * step;
    const innerWidth = width - padding.left - padding.right;
    const innerHeight = height - padding.top - padding.bottom;
    const stepX = series.length > 1 ? innerWidth / (series.length - 1) : 0;
    const points = series.map((item, index) => ({
      label: item.label,
      value: item.value,
      x: padding.left + stepX * index,
      y: 0,
    }));
    let hoveredIndex = -1;

    function yForValue(value) {
      const ratio = (value - chartMin) / (chartMax - chartMin || 1);
      return height - padding.bottom - ratio * innerHeight;
    }

    points.forEach((point) => {
      point.y = yForValue(point.value);
    });

    function draw() {
      ctx.clearRect(0, 0, width, height);
      ctx.textBaseline = "middle";
      ctx.font = "12px system-ui";

      const gradient = ctx.createLinearGradient(0, padding.top, 0, height - padding.bottom);
      gradient.addColorStop(0, "rgba(245, 78, 0, 0.24)");
      gradient.addColorStop(1, "rgba(245, 78, 0, 0.02)");

      for (let index = 0; index <= 4; index += 1) {
        const tickValue = chartMin + ((chartMax - chartMin) * index) / 4;
        const y = yForValue(tickValue);
        ctx.strokeStyle = "rgba(38, 37, 30, 0.1)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();
        ctx.fillStyle = "rgba(38, 37, 30, 0.52)";
        ctx.textAlign = "left";
        ctx.fillText(formatMoney(tickValue), 14, y);
      }

      ctx.strokeStyle = "rgba(38, 37, 30, 0.24)";
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.moveTo(padding.left, padding.top);
      ctx.lineTo(padding.left, height - padding.bottom);
      ctx.lineTo(width - padding.right, height - padding.bottom);
      ctx.stroke();

      ctx.beginPath();
      points.forEach((point, index) => {
        if (index === 0) {
          ctx.moveTo(point.x, point.y);
          return;
        }
        ctx.lineTo(point.x, point.y);
      });
      ctx.lineTo(points[points.length - 1].x, height - padding.bottom);
      ctx.lineTo(points[0].x, height - padding.bottom);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();

      ctx.strokeStyle = "#f54e00";
      ctx.lineWidth = 3;
      ctx.beginPath();
      points.forEach((point, index) => {
        if (index === 0) {
          ctx.moveTo(point.x, point.y);
          return;
        }
        ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();

      points.forEach((point, index) => {
        const isHovered = hoveredIndex === index;
        ctx.fillStyle = isHovered ? "#26251e" : "#f54e00";
        ctx.beginPath();
        ctx.arc(point.x, point.y, isHovered ? 6 : 4.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "rgba(38, 37, 30, 0.74)";
        ctx.textAlign = "center";
        ctx.fillText(point.label, point.x, height - 24);

        if (isHovered || points.length <= 4) {
          ctx.fillStyle = isHovered ? "#26251e" : "rgba(38, 37, 30, 0.72)";
          ctx.fillText(formatMoney(point.value), point.x, point.y - 14);
        }
      });
    }

    function updateHover(event) {
      const rect = canvas.getBoundingClientRect();
      const scaleX = width / rect.width;
      const scaleY = height / rect.height;
      const mouseX = (event.clientX - rect.left) * scaleX;
      const mouseY = (event.clientY - rect.top) * scaleY;
      hoveredIndex = points.findIndex((point) => Math.hypot(point.x - mouseX, point.y - mouseY) <= 14);

      if (hoveredIndex >= 0 && tooltip) {
        const point = points[hoveredIndex];
        tooltip.hidden = false;
        tooltip.textContent = `${point.label} · ${formatMoney(point.value)}`;
        tooltip.style.left = `${event.clientX - rect.left + 12}px`;
        tooltip.style.top = `${event.clientY - rect.top - 12}px`;
      } else if (tooltip) {
        tooltip.hidden = true;
      }

      draw();
    }

    canvas.addEventListener("mousemove", updateHover);
    canvas.addEventListener("mouseleave", () => {
      hoveredIndex = -1;
      if (tooltip) {
        tooltip.hidden = true;
      }
      draw();
    });

    draw();
  }

  function bootFinalPage() {
    renderNetAssetsTrend({
      canvas: document.getElementById("net-assets-chart"),
      tooltip: document.getElementById("net-assets-tooltip"),
    });

    window.ExschoolCharts?.renderAllCompanyTrend({
      canvas: document.getElementById("all-company-chart"),
      tooltip: document.getElementById("all-company-tooltip"),
      legend: document.getElementById("all-company-legend"),
      controls: {
        zoomIn: document.getElementById("all-company-zoom-in"),
        zoomOut: document.getElementById("all-company-zoom-out"),
        zoomReset: document.getElementById("all-company-zoom-reset"),
      },
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootFinalPage, { once: true });
  } else {
    bootFinalPage();
  }
})();
