(function () {
  function bootReportPage() {
    const pageData = window.ExschoolBase?.readJsonScript("report-page-data");
    if (!pageData) {
      return;
    }

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

    const downloadReportButton = document.getElementById("download-report-image");
    const statusKey = `exschool-game-round-status:${pageData.gameId}`;
    const draftKey = `exschool-game-draft:${pageData.gameId}:${pageData.roundId}`;

    async function warmReportImageCache(maxAttempts) {
      for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
        try {
          const response = await fetch(pageData.reportImageCachedUrl, { cache: "no-store" });
          if (response.ok && response.status !== 204) {
            return true;
          }
        } catch (error) {
          return false;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 600));
      }
      return false;
    }

    function renderDownloadReportLink() {
      if (!downloadReportButton) {
        return;
      }
      const separator = pageData.reportImageCachedUrl.includes("?") ? "&" : "?";
      const pngUrl = `${pageData.reportImageCachedUrl}${separator}download=1&ts=${Date.now()}`;
      const downloadReportLink = document.createElement("a");
      downloadReportLink.id = "download-report-image";
      downloadReportLink.className = downloadReportButton.className || "button";
      downloadReportLink.textContent = downloadReportButton.textContent || pageData.downloadButtonLabel || "下载财报";
      downloadReportLink.href = pngUrl;
      downloadReportLink.download = `${pageData.exportFilenameBase}-${Date.now()}.png`;
      downloadReportLink.setAttribute("role", "button");
      downloadReportLink.setAttribute("aria-label", downloadReportLink.textContent);
      downloadReportLink.style.textDecoration = "none";
      downloadReportLink.style.color = "inherit";
      downloadReportButton.replaceWith(downloadReportLink);
    }

    function syncSubmittedRoundStatus() {
      const statusMap = window.ExschoolBase?.mergeRoundStatuses(
        window.ExschoolBase?.readStorageJson(statusKey, {}) || {},
        pageData.roundStatuses
      ) || {};
      statusMap[String(pageData.roundId).toLowerCase()] = "submitted";
      window.ExschoolBase?.writeStorageJson(statusKey, statusMap);
      window.ExschoolBase?.removeStorageItem(draftKey);
      window.ExschoolBase?.renderRoundStatusBadges(statusMap);
    }

    syncSubmittedRoundStatus();
    void warmReportImageCache(pageData.cacheWarmAttempts);
    renderDownloadReportLink();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootReportPage, { once: true });
  } else {
    bootReportPage();
  }
})();
