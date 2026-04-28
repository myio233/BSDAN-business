(function () {
  const DEFAULT_ROUND_STATUS_LABELS = {
    submitted: "已提交",
    pending: "未提交",
    upcoming: "未开始",
  };

  function safeJsonParse(rawValue, fallbackValue) {
    if (typeof rawValue !== "string" || rawValue === "") {
      return fallbackValue;
    }
    try {
      return JSON.parse(rawValue);
    } catch (error) {
      return fallbackValue;
    }
  }

  function readJsonScript(id) {
    const node = document.getElementById(id);
    if (!node) return null;
    return safeJsonParse(node.textContent || "null", null);
  }

  function readPageData(fallbackId) {
    const explicitId = typeof fallbackId === "string" && fallbackId !== "" ? fallbackId : "";
    const pageDataId = explicitId || document.body?.dataset.pageDataId || "";
    return pageDataId ? readJsonScript(pageDataId) : null;
  }

  function readStorageJson(key, fallbackValue) {
    try {
      return safeJsonParse(window.localStorage.getItem(key), fallbackValue);
    } catch (error) {
      return fallbackValue;
    }
  }

  function writeStorageJson(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (error) {
      return false;
    }
  }

  function removeStorageItem(key) {
    try {
      window.localStorage.removeItem(key);
      return true;
    } catch (error) {
      return false;
    }
  }

  function mergeRoundStatuses(statusMap, serverRoundStatuses) {
    const nextStatusMap = { ...(statusMap || {}) };
    (serverRoundStatuses || []).forEach((item) => {
      const roundKey = String(item?.round_id || "").toLowerCase();
      if (!roundKey) return;
      if (item.status === "submitted") {
        nextStatusMap[roundKey] = "submitted";
        return;
      }
      if (!(roundKey in nextStatusMap)) {
        nextStatusMap[roundKey] = item.status;
      }
    });
    return nextStatusMap;
  }

  function renderRoundStatusBadges(statusMap, labels) {
    const labelMap = labels || DEFAULT_ROUND_STATUS_LABELS;
    document.querySelectorAll(".round-status-badge").forEach((badge) => {
      const badgeRoundId = String(badge.dataset.roundId || "").toLowerCase();
      const status = statusMap?.[badgeRoundId] || "upcoming";
      badge.className = `badge round-status-badge round-status-${status}`;
      badge.textContent = `${badgeRoundId.toUpperCase()} ${labelMap[status] || labelMap.upcoming}`;
    });
  }

  function injectCsrfIntoPostForms(token) {
    if (!token) return;
    document.querySelectorAll('form[method="post"]').forEach((form) => {
      if (form.querySelector('input[name="_csrf"]')) return;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "_csrf";
      hidden.value = token;
      form.appendChild(hidden);
    });
  }

  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  window.__EXSCHOOL_CSRF_TOKEN__ = token;
  window.ExschoolBase = {
    ...(window.ExschoolBase || {}),
    readJsonScript,
    readPageData,
    readStorageJson,
    writeStorageJson,
    removeStorageItem,
    mergeRoundStatuses,
    renderRoundStatusBadges,
  };
  injectCsrfIntoPostForms(token);
})();
