(function () {
  function bootRoundPage() {
    const pageData = window.ExschoolBase?.readJsonScript("round-page-data");
    if (!pageData) {
      return;
    }

    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");
    const form = document.getElementById("decision-form");
    if (!form) {
      return;
    }

    const defaultsButton = document.getElementById("load-defaults");
    const previewButton = document.getElementById("open-submit-preview");
    const submitModeField = form.elements["submit_mode"];
    const previewModal = document.getElementById("decision-preview");
    const previewBody = document.getElementById("decision-preview-body");
    const confirmSubmitButton = document.getElementById("confirm-submit");
    const livePreviewStatus = document.getElementById("decision-preview-live-status");
    const livePreviewError = document.getElementById("decision-preview-live-error");
    const livePreviewMetrics = document.getElementById("decision-preview-live-metrics");
    const livePreviewWorkforce = document.getElementById("decision-preview-live-workforce");
    const livePreviewMarkets = document.getElementById("decision-preview-live-markets");
    const draftStorageKey = `exschool-game-draft:${pageData.gameId}:${pageData.roundId}`;
    const roundStatusStorageKey = `exschool-game-round-status:${pageData.gameId}`;
    const clientClockBaselineMs = Date.now();
    let allowDirectSubmit = false;
    let timeoutSubmitTriggered = false;
    let livePreviewRequestId = 0;
    let livePreviewTimer = null;

    function syncedNowMs() {
      return pageData.serverCurrentTimestampMs + (Date.now() - clientClockBaselineMs);
    }

    function clampHeadcount(currentValue, delta) {
      return Math.max(currentValue + delta, 0);
    }

    function parseInteger(value) {
      const parsed = Number.parseInt(value ?? "0", 10);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function parseDecimal(value) {
      const parsed = Number(value ?? 0);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function formatMoney(value) {
      return `¥${Math.round(parseDecimal(value)).toLocaleString("en-US")}`;
    }

    function formatPercent(value) {
      return `${(parseDecimal(value) * 100).toFixed(2)}%`;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function readRoundStatusMap() {
      return window.ExschoolBase?.readStorageJson(roundStatusStorageKey, {}) || {};
    }

    function writeRoundStatusMap(statusMap) {
      window.ExschoolBase?.writeStorageJson(roundStatusStorageKey, statusMap);
    }

    function syncServerRoundStatuses(statusMap) {
      return (
        window.ExschoolBase?.mergeRoundStatuses(statusMap, pageData.roundStatuses) || {
          ...(statusMap || {}),
        }
      );
    }

    function renderRoundStatuses(statusMap) {
      window.ExschoolBase?.renderRoundStatusBadges(statusMap);
    }

    function activateTab(targetId) {
      tabButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.target === targetId);
      });
      tabPanes.forEach((pane) => {
        pane.classList.toggle("active", pane.id === targetId);
      });
    }

    function updateCurrentValueHints() {
      const workerDelta = parseInteger(form.elements.workers?.value);
      const engineerDelta = parseInteger(form.elements.engineers?.value);
      const workerAfter = clampHeadcount(pageData.currentWorkers, workerDelta);
      const engineerAfter = clampHeadcount(pageData.currentEngineers, engineerDelta);
      const workerHint = document.querySelector('[data-after-field="workers"]');
      const engineerHint = document.querySelector('[data-after-field="engineers"]');
      if (workerHint) {
        workerHint.textContent = `提交后 ${workerAfter}`;
      }
      if (engineerHint) {
        engineerHint.textContent = `提交后 ${engineerAfter}`;
      }

      Object.entries(pageData.currentAgents || {}).forEach(([market, currentValue]) => {
        const input = form.elements[`${market.toLowerCase()}_agent_change`];
        const target = document.querySelector(`[data-after-agent="${market}"]`);
        if (!input || !target) {
          return;
        }
        target.textContent = `提交后 ${clampHeadcount(parseInteger(currentValue), parseInteger(input.value))}`;
      });
    }

    function buildPreviewMarkup() {
      const workerDelta = parseInteger(form.elements.workers?.value);
      const engineerDelta = parseInteger(form.elements.engineers?.value);
      const markets = Object.keys(pageData.currentAgents || {}).map((market) => {
        const slug = market.toLowerCase();
        const reportSubscribed = form.elements[`${slug}_market_report`]?.checked !== false;
        const agentDelta = parseInteger(form.elements[`${slug}_agent_change`]?.value);
        const marketing = parseDecimal(form.elements[`${slug}_marketing_investment`]?.value);
        const price = parseDecimal(form.elements[`${slug}_price`]?.value);
        const currentValue = parseInteger(pageData.currentAgents?.[market]);
        return {
          market,
          reportSubscribed,
          agentDelta,
          agentAfter: clampHeadcount(currentValue, agentDelta),
          marketing,
          price,
        };
      });
      const activeMarkets = markets.filter((item) => item.agentDelta !== 0 || item.marketing !== 0 || item.price !== 0 || !item.reportSubscribed);
      const marketRows = (activeMarkets.length ? activeMarkets : markets)
        .map(
          (item) => `
    <tr>
      <td>${escapeHtml(item.market)}</td>
      <td>${item.reportSubscribed ? "订阅" : "不订阅"}</td>
      <td>${item.agentDelta >= 0 ? "+" : ""}${item.agentDelta}</td>
      <td>${item.agentAfter}</td>
      <td>${formatMoney(item.marketing)}</td>
      <td>${formatMoney(item.price)}</td>
    </tr>
  `
        )
        .join("");

      return `
    <div class="preview-grid">
      <article class="preview-card">
        <span>贷款变动</span>
        <strong>${formatMoney(form.elements.loan_delta?.value)}</strong>
      </article>
      <article class="preview-card">
        <span>计划生产数量</span>
        <strong>${parseInteger(form.elements.products_planned?.value)}</strong>
      </article>
      <article class="preview-card">
        <span>工人数</span>
        <strong>${pageData.currentWorkers} -> ${clampHeadcount(pageData.currentWorkers, workerDelta)}</strong>
      </article>
      <article class="preview-card">
        <span>工程师数</span>
        <strong>${pageData.currentEngineers} -> ${clampHeadcount(pageData.currentEngineers, engineerDelta)}</strong>
      </article>
    </div>
    <div class="preview-table-wrap">
      <table class="report-table compact">
        <thead>
          <tr><th>市场</th><th>报表</th><th>代理变化</th><th>提交后代理</th><th>营销投入</th><th>售价</th></tr>
        </thead>
        <tbody>${marketRows}</tbody>
      </table>
    </div>
  `;
    }

    function openPreview() {
      if (!previewModal || !previewBody) {
        return;
      }
      previewBody.innerHTML = buildPreviewMarkup();
      previewModal.hidden = false;
      document.body.classList.add("modal-open");
      queueLivePreviewRefresh();
    }

    function closePreview() {
      if (!previewModal) {
        return;
      }
      previewModal.hidden = true;
      document.body.classList.remove("modal-open");
    }

    function setLivePreviewStatus(message) {
      if (livePreviewStatus) {
        livePreviewStatus.textContent = message;
      }
    }

    function setLivePreviewError(message) {
      if (!livePreviewError) {
        return;
      }
      const text = String(message || "").trim();
      livePreviewError.hidden = !text;
      livePreviewError.textContent = text;
    }

    function renderLivePreviewMetrics(preview) {
      if (!livePreviewMetrics) {
        return;
      }
      const metrics = [
        { label: "实际贷款变动", value: formatMoney(preview?.loan?.effective) },
        { label: "实际生产成品", value: `${Math.round(parseDecimal(preview?.production?.actual_products))}` },
        { label: "实际市场报表费用", value: formatMoney(preview?.investments?.market_report_cost_effective) },
        { label: "期末现金 / 负债", value: `${formatMoney(preview?.loan?.ending_cash)} / ${formatMoney(preview?.loan?.ending_debt)}` },
      ];
      livePreviewMetrics.innerHTML = metrics
        .map(
          (item) => `
            <article class="preview-card">
              <span>${escapeHtml(item.label)}</span>
              <strong>${escapeHtml(item.value)}</strong>
            </article>
          `
        )
        .join("");
    }

    function renderLivePreviewWorkforce(preview) {
      if (!livePreviewWorkforce) {
        return;
      }
      const workforceRows = Array.isArray(preview?.workforce) ? preview.workforce : [];
      livePreviewWorkforce.innerHTML = workforceRows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(row.category)}</td>
              <td>${Math.round(parseDecimal(row.starting_total))}</td>
              <td>${parseDecimal(row.requested_change) >= 0 ? "+" : ""}${Math.round(parseDecimal(row.requested_change))}</td>
              <td>${Math.round(parseDecimal(row.requested_total_after))}</td>
              <td>${Math.round(parseDecimal(row.effective_total))}</td>
              <td>${Math.round(parseDecimal(row.laid_off))}</td>
              <td>${Math.round(parseDecimal(row.quits))}</td>
              <td>${Math.round(parseDecimal(row.added))}</td>
              <td>${formatMoney(row.salary)} / ${formatMoney(row.average_salary)}</td>
              <td>${Number(parseDecimal(row.productivity_multiplier)).toFixed(3)}</td>
            </tr>
          `
        )
        .join("");
    }

    function renderLivePreviewMarkets(preview) {
      if (!livePreviewMarkets) {
        return;
      }
      const marketRows = Array.isArray(preview?.markets) ? preview.markets : [];
      livePreviewMarkets.innerHTML = marketRows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(row.market)}</td>
              <td>${row.effective_subscribed ? "订阅" : "不订阅"}</td>
              <td>${parseDecimal(row.effective_agent_change) >= 0 ? "+" : ""}${Math.round(parseDecimal(row.effective_agent_change))}</td>
              <td>${Math.round(parseDecimal(row.effective_agents_after))}</td>
              <td>${formatMoney(row.effective_marketing_investment)}</td>
              <td>${Math.round(parseDecimal(row.sales_volume)).toLocaleString("en-US")}</td>
              <td>${formatPercent(row.market_share)}</td>
            </tr>
          `
        )
        .join("");
    }

    function renderLivePreview(preview) {
      renderLivePreviewMetrics(preview);
      renderLivePreviewWorkforce(preview);
      renderLivePreviewMarkets(preview);
      const droppedMarkets = Array.isArray(preview?.investments?.subscriptions_dropped_markets)
        ? preview.investments.subscriptions_dropped_markets
        : [];
      if (droppedMarkets.length > 0) {
        setLivePreviewStatus(`已按当前输入完成真实试算；现金不足时已裁掉市场报表：${droppedMarkets.join("、")}。`);
        return;
      }
      setLivePreviewStatus("已按当前输入完成一次真实试算。以下展示的是实际会执行的结果。");
    }

    async function refreshLivePreview() {
      if (!pageData.previewUrl || !previewModal || previewModal.hidden) {
        return;
      }
      const requestId = ++livePreviewRequestId;
      setLivePreviewError("");
      setLivePreviewStatus("正在刷新当前输入的真实试算结果…");
      try {
        const formData = new FormData(form);
        formData.set("submit_mode", "preview");
        const response = await fetch(pageData.previewUrl, {
          method: "POST",
          body: formData,
          headers: {
            "X-Requested-With": "fetch",
          },
        });
        const payload = await response.json();
        if (requestId !== livePreviewRequestId) {
          return;
        }
        if (!response.ok || !payload?.ok) {
          throw new Error(payload?.detail || "真实试算失败，请检查输入。");
        }
        renderLivePreview(payload.preview || {});
      } catch (error) {
        if (requestId !== livePreviewRequestId) {
          return;
        }
        setLivePreviewStatus("真实试算未成功返回。");
        setLivePreviewError(error instanceof Error ? error.message : "真实试算失败，请稍后重试。");
        if (livePreviewMetrics) {
          livePreviewMetrics.innerHTML = "";
        }
        if (livePreviewWorkforce) {
          livePreviewWorkforce.innerHTML = "";
        }
        if (livePreviewMarkets) {
          livePreviewMarkets.innerHTML = "";
        }
      }
    }

    function queueLivePreviewRefresh() {
      if (!previewModal || previewModal.hidden) {
        return;
      }
      if (livePreviewTimer) {
        window.clearTimeout(livePreviewTimer);
      }
      livePreviewTimer = window.setTimeout(() => {
        livePreviewTimer = null;
        refreshLivePreview();
      }, 150);
    }

    function collectDraftPayload() {
      const draft = {};
      Array.from(form.elements).forEach((field) => {
        if (!field.name || field.name === "round_id" || field.name === "submit_mode") {
          return;
        }
        if (field.type === "checkbox") {
          draft[field.name] = field.checked;
          return;
        }
        draft[field.name] = field.value;
      });
      return draft;
    }

    function applyDraftPayload(draft) {
      Object.entries(draft || {}).forEach(([fieldName, value]) => {
        const field = form.elements[fieldName];
        if (!field) {
          return;
        }
        if (field.type === "checkbox") {
          field.checked = Boolean(value);
          return;
        }
        field.value = value ?? "";
      });
      updateCurrentValueHints();
    }

    function saveDraftPayload() {
      if (pageData.currentRoundSubmitted) {
        return;
      }
      window.ExschoolBase?.writeStorageJson(draftStorageKey, collectDraftPayload());
      const statusMap = syncServerRoundStatuses(readRoundStatusMap());
      statusMap[String(pageData.roundId).toLowerCase()] = "pending";
      writeRoundStatusMap(statusMap);
      renderRoundStatuses(statusMap);
    }

    function applyPayload(payload) {
      Object.entries(payload || {}).forEach(([key, value]) => {
        if (key === "round_id" || key === "submit_mode") {
          return;
        }
        if (key === "markets") {
          document.querySelectorAll(".market-card").forEach((card) => {
            const market = card.dataset.market;
            const marketPayload = value?.[market];
            const marketSlug = market?.toLowerCase();
            const reportCheckbox = form.elements[`${marketSlug}_market_report`];
            if (reportCheckbox) {
              reportCheckbox.checked = Boolean(marketPayload && marketPayload.subscribed_market_report !== false);
            }
            if (marketPayload) {
              form.elements[`${marketSlug}_agent_change`].value = marketPayload.agent_change;
              form.elements[`${marketSlug}_marketing_investment`].value = marketPayload.marketing_investment;
              form.elements[`${marketSlug}_price`].value = marketPayload.price;
            }
          });
          return;
        }
        if (form.elements[key]) {
          form.elements[key].value = value;
        }
      });
      updateCurrentValueHints();
    }

    function setupTabs() {
      tabButtons.forEach((button) => {
        button.addEventListener("click", () => {
          activateTab(button.dataset.target);
        });
      });
    }

    function setupTimer() {
      const timerCard = document.querySelector(".timer-card");
      if (!timerCard) {
        return;
      }
      const deadlineMs = Number(timerCard.dataset.roundDeadlineMs || 0);
      const countdown = document.getElementById("countdown");
      if (!countdown) {
        return;
      }

      const triggerTimeoutSubmit = () => {
        if (pageData.currentRoundSubmitted || timeoutSubmitTriggered) {
          return;
        }
        timeoutSubmitTriggered = true;
        closePreview();
        submitModeField.value = "timeout-auto";
        allowDirectSubmit = true;
        timerCard.classList.add("timer-card-expired");
        countdown.textContent = "00:00";
        window.setTimeout(() => {
          form.requestSubmit();
        }, 0);
      };

      const render = () => {
        const remainingMs = Math.max(deadlineMs - syncedNowMs(), 0);
        const remaining = Math.ceil(remainingMs / 1000);
        const minutes = String(Math.floor(remaining / 60)).padStart(2, "0");
        const seconds = String(remaining % 60).padStart(2, "0");
        countdown.textContent = `${minutes}:${seconds}`;
        if (remainingMs <= 0) {
          triggerTimeoutSubmit();
        }
      };

      render();
      window.setInterval(render, 1000);
    }

    setupTabs();

    defaultsButton?.addEventListener("click", () => {
      applyPayload(pageData.initialPayload);
      saveDraftPayload();
    });

    applyPayload(pageData.initialPayload);
    const initialRoundStatusMap = syncServerRoundStatuses(readRoundStatusMap());
    if (pageData.currentRoundSubmitted) {
      initialRoundStatusMap[String(pageData.roundId).toLowerCase()] = "submitted";
      window.ExschoolBase?.removeStorageItem(draftStorageKey);
    } else {
      const draft = window.ExschoolBase?.readStorageJson(draftStorageKey, null);
      if (draft) {
        applyDraftPayload(draft);
      } else {
        saveDraftPayload();
      }
      initialRoundStatusMap[String(pageData.roundId).toLowerCase()] = "pending";
    }
    writeRoundStatusMap(initialRoundStatusMap);
    renderRoundStatuses(initialRoundStatusMap);
    updateCurrentValueHints();
    setupTimer();

    previewButton?.addEventListener("click", () => {
      openPreview();
    });

    document.querySelectorAll("[data-close-preview]").forEach((button) => {
      button.addEventListener("click", closePreview);
    });

    previewModal?.addEventListener("click", (event) => {
      if (event.target === previewModal) {
        closePreview();
      }
    });

    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closePreview();
      }
    });

    confirmSubmitButton?.addEventListener("click", () => {
      submitModeField.value = "manual-confirmed";
      allowDirectSubmit = true;
      closePreview();
      form.requestSubmit();
    });

    form.addEventListener("submit", (event) => {
      if (pageData.currentRoundSubmitted || allowDirectSubmit) {
        return;
      }
      event.preventDefault();
      openPreview();
    });

    if (!pageData.currentRoundSubmitted) {
      form.addEventListener("input", () => {
        updateCurrentValueHints();
        saveDraftPayload();
        queueLivePreviewRefresh();
      });
      form.addEventListener("change", () => {
        updateCurrentValueHints();
        saveDraftPayload();
        queueLivePreviewRefresh();
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootRoundPage, { once: true });
  } else {
    bootRoundPage();
  }
})();
