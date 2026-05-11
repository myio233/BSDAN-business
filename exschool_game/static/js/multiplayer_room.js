(function () {
  const DEFAULT_STATUS_LABELS = {
    lobby: "等待中",
    waiting: "等待中",
    ready_check: "准备检查中",
    starting: "即将开始",
    started: "已开始",
    closed: "已关闭",
  };

  const DEFAULT_READY_LABELS = {
    ready: "已准备",
    not_ready: "未准备",
    open: "空席",
  };

  function bootMultiplayerRoomPage() {
    const pageData = window.ExschoolBase?.readJsonScript("multiplayer-room-page-data") || {};
    const seatList = document.getElementById("multiplayer-seat-list");
    const botList = document.getElementById("multiplayer-bot-list");
    const errorNode = document.getElementById("multiplayer-room-error");
    const messageNode = document.getElementById("multiplayer-room-message");
    const refreshButton = document.getElementById("multiplayer-refresh-button");
    const joinButton = document.getElementById("multiplayer-join-button");
    const leaveButton = document.getElementById("multiplayer-leave-button");
    const readyButton = document.getElementById("multiplayer-ready-button");
    const startButton = document.getElementById("multiplayer-start-button");
    const roomCodeNode = document.getElementById("multiplayer-room-code");
    const roomStatusNode = document.getElementById("multiplayer-room-status");
    const hostNameNode = document.getElementById("multiplayer-host-name");
    const hostMetaNode = document.getElementById("multiplayer-host-meta");
    const seatSummaryNode = document.getElementById("multiplayer-seat-summary");
    const seatMetaNode = document.getElementById("multiplayer-seat-meta");
    const readySummaryNode = document.getElementById("multiplayer-ready-summary");
    const readyMetaNode = document.getElementById("multiplayer-ready-meta");
    const botSummaryNode = document.getElementById("multiplayer-bot-summary");
    const botMetaNode = document.getElementById("multiplayer-bot-meta");
    const currentPlayerNameNode = document.getElementById("multiplayer-current-player-name");
    const currentPlayerMembershipNode = document.getElementById("multiplayer-current-player-membership");
    const currentPlayerSeatNode = document.getElementById("multiplayer-current-player-seat");
    const currentPlayerReadyNode = document.getElementById("multiplayer-current-player-ready");
    const currentPlayerBadgesNode = document.getElementById("multiplayer-current-player-badges");
    const homeCitySelect = document.getElementById("multiplayer-home-city-select");
    const homeCitySaveButton = document.getElementById("multiplayer-home-city-save");
    const homeCityCopyNode = document.getElementById("multiplayer-home-city-copy");
    const enterGameLink = document.getElementById("multiplayer-enter-game-link");
    const viewReportLink = document.getElementById("multiplayer-view-report-link");
    const viewFinalLink = document.getElementById("multiplayer-view-final-link");
    const lastSyncAtNode = document.getElementById("multiplayer-last-sync-at");
    const syncSourceNode = document.getElementById("multiplayer-sync-source");
    const pollingStatusNode = document.getElementById("multiplayer-polling-status");
    const pollingMetaNode = document.getElementById("multiplayer-polling-meta");

    if (
      !seatList ||
      !botList ||
      !refreshButton ||
      !joinButton ||
      !leaveButton ||
      !readyButton ||
      !startButton ||
      !homeCitySelect ||
      !homeCitySaveButton ||
      !homeCityCopyNode
    ) {
      return;
    }

    const actions = pageData.actions || {};
    const labels = pageData.labels || {};
    const homeCityOptions = safeArray(pageData.homeCityOptions);
    const homeCityLabels = pageData.homeCityLabels || {};
    const pollIntervalMs = clampNumber(pageData.pollIntervalMs, 1500, 30000, 4000);
    const roomId = pageData.roomId || pageData.room_id || "";
    let snapshot = normalizeSnapshot(pageData.initialSnapshot || null);
    let pollTimer = 0;
    let pollRequestId = 0;
    let actionInFlight = false;
    let latestSyncLabel = "尚未刷新";

    function clampNumber(value, min, max, fallback) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) {
        return fallback;
      }
      return Math.max(min, Math.min(max, numeric));
    }

    function safeArray(value) {
      return Array.isArray(value) ? value : [];
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function textOrFallback(value, fallbackText) {
      return String(value ?? "").trim() || fallbackText;
    }

    function setFeedback(node, message) {
      if (!node) return;
      const normalized = String(message || "").trim();
      node.hidden = normalized === "";
      node.textContent = normalized;
    }

    function clearFeedback() {
      setFeedback(errorNode, "");
      setFeedback(messageNode, "");
    }

    function setMessage(message) {
      setFeedback(messageNode, message);
    }

    function setError(message) {
      setFeedback(errorNode, message || labels.snapshotFailedMessage || "房间快照刷新失败。");
    }

    function formatTimestamp(value) {
      if (!value) {
        return "尚未刷新";
      }
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {
        return "尚未刷新";
      }
      return new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(date);
    }

    function extractSnapshot(payload) {
      if (!payload || typeof payload !== "object") {
        return null;
      }
      if (payload.snapshot && typeof payload.snapshot === "object") {
        return payload.snapshot;
      }
      if (payload.room && typeof payload.room === "object") {
        return payload.room;
      }
      return payload;
    }

    function normalizeSeat(rawSeat, index) {
      const seat = rawSeat && typeof rawSeat === "object" ? rawSeat : {};
      const occupant = seat.occupant && typeof seat.occupant === "object" ? seat.occupant : {};
      const player = seat.player && typeof seat.player === "object" ? seat.player : occupant;
      const label =
        seat.label ||
        seat.seat_label ||
        (seat.number ? `席位 ${seat.number}` : `席位 ${index + 1}`);
      const playerName =
        player.name ||
        seat.player_name ||
        seat.occupant_name ||
        (seat.is_bot ? labels.botName || "机器人" : "");
      const occupied =
        typeof seat.occupied === "boolean"
          ? seat.occupied
          : Boolean(playerName || seat.player_id || seat.occupant_id || seat.is_bot);
      const isBot =
        typeof seat.is_bot === "boolean" ? seat.is_bot : Boolean(player.is_bot || occupant.is_bot);
      const isHost =
        typeof seat.is_host === "boolean"
          ? seat.is_host
          : Boolean(player.is_host || occupant.is_host || seat.host);
      const ready =
        typeof seat.ready === "boolean"
          ? seat.ready
          : typeof player.ready === "boolean"
            ? player.ready
            : Boolean(seat.is_ready);
      const currentUserHere = Boolean(
        seat.current_user_here || player.current_user_here || seat.is_current_player
      );
      const submittedCurrentRound = Boolean(
        seat.submitted_current_round || player.submitted_current_round || seat.is_submitted
      );
      return {
        seatId: String(seat.seat_id || seat.id || seat.number || index + 1),
        label,
        occupied,
        isBot,
        isHost,
        ready,
        submittedCurrentRound,
        currentUserHere,
        playerName: textOrFallback(playerName, occupied ? "未命名玩家" : "空席"),
        playerEmail: player.email || seat.player_email || "",
        playerId: String(player.player_id || player.id || seat.player_id || seat.occupant_id || ""),
      };
    }

    function normalizeCurrentPlayer(rawPlayer, seats) {
      const player = rawPlayer && typeof rawPlayer === "object" ? rawPlayer : {};
      const currentSeat = seats.find((seat) => seat.currentUserHere) || null;
      const inRoom =
        typeof player.in_room === "boolean"
          ? player.in_room
          : Boolean(currentSeat || player.player_id || player.id || player.seat_id);
      const ready =
        typeof player.ready === "boolean" ? player.ready : currentSeat ? currentSeat.ready : false;
      const isHost =
        typeof player.is_host === "boolean" ? player.is_host : currentSeat ? currentSeat.isHost : false;
      const seatLabel =
        player.seat_label ||
        currentSeat?.label ||
        (player.seat_id ? `席位 ${player.seat_id}` : "未就座");
      return {
        name: player.name || pageData.currentUserName || labels.currentPlayerName || "当前玩家",
        inRoom,
        ready,
        isHost,
        isBot: Boolean(player.is_bot),
        submittedCurrentRound: Boolean(player.submitted_current_round),
        seatLabel,
        homeCity: player.home_city || "",
        homeCityLabel: player.home_city_label || "",
      };
    }

    function normalizeSnapshot(rawSnapshot) {
      const payload = extractSnapshot(rawSnapshot);
      if (!payload) {
        return null;
      }
      const seats = safeArray(payload.seats).map(normalizeSeat);
      const currentPlayer = normalizeCurrentPlayer(payload.current_player, seats);
      const hostFromSeat = seats.find((seat) => seat.isHost) || null;
      const host = payload.host && typeof payload.host === "object" ? payload.host : {};
      const totalSeats = Number(payload.max_seats || payload.total_seats || seats.length || 0);
      const occupiedSeats = seats.filter((seat) => seat.occupied).length;
      const readySeats = seats.filter((seat) => seat.occupied && seat.ready).length;
      const botSeats = seats.filter((seat) => seat.isBot).length;
      const availableSeats = seats.filter((seat) => !seat.occupied).length;
      const permissions = payload.permissions && typeof payload.permissions === "object" ? payload.permissions : {};
      return {
        roomId: String(payload.room_id || payload.id || roomId || ""),
        roomName: payload.room_name || pageData.roomName || "多人房间",
        roomCode: payload.room_code || pageData.roomCode || "",
        status: String(payload.status || "lobby"),
        statusLabel:
          payload.status_label || labels.statusLabels?.[payload.status] || DEFAULT_STATUS_LABELS[payload.status] || "等待中",
        hostName: host.name || payload.host_name || hostFromSeat?.playerName || "等待房主加入",
        hostSeatLabel: host.seat_label || hostFromSeat?.label || "",
        seats,
        currentPlayer,
        permissions,
        summary: {
          totalSeats,
          occupiedSeats,
          readySeats,
          botSeats,
          availableSeats,
        },
        updatedAt: payload.updated_at || payload.snapshot_at || new Date().toISOString(),
        started: Boolean(payload.started || payload.status === "started"),
      };
    }

    function createBadge(text) {
      return `<span class="badge">${escapeHtml(text)}</span>`;
    }

    function renderCurrentPlayer(currentPlayer) {
      currentPlayerNameNode.textContent = currentPlayer.name;
      currentPlayerMembershipNode.textContent = currentPlayer.inRoom
        ? labels.inRoomLabel || "你已加入当前房间。"
        : labels.notInRoomLabel || "你尚未加入当前房间。";
      currentPlayerSeatNode.textContent = currentPlayer.seatLabel;
      currentPlayerReadyNode.textContent = currentPlayer.inRoom
        ? currentPlayer.ready
          ? labels.readyLabel || "当前状态：已准备。"
          : labels.notReadyLabel || "当前状态：未准备。"
        : labels.joinFirstLabel || "加入房间后才会显示准备状态。";
      const selectedHomeCity = currentPlayer.homeCity || homeCityOptions[0] || "";
      homeCitySelect.innerHTML = homeCityOptions
        .map((city) => {
          const selected = city === selectedHomeCity ? " selected" : "";
          const label = homeCityLabels[city] || city;
          return `<option value="${escapeHtml(city)}"${selected}>${escapeHtml(label)}</option>`;
        })
        .join("");
      homeCityCopyNode.textContent = currentPlayer.inRoom
        ? `当前主场城市：${currentPlayer.homeCityLabel || homeCityLabels[selectedHomeCity] || selectedHomeCity || "未设置"}。多人局会按这个城市结算贷款、利率、材料和仓储参数。`
        : "加入房间后才能设置主场城市。";

      const badges = [];
      badges.push(currentPlayer.inRoom ? "已入房" : "未入房");
      badges.push(currentPlayer.ready ? "已准备" : "未准备");
      if (currentPlayer.isHost) badges.push("房主");
      if (currentPlayer.isBot) badges.push("机器人");
      currentPlayerBadgesNode.innerHTML = badges.map(createBadge).join("");
      if (enterGameLink) {
        const canEnterGame =
          currentPlayer.inRoom &&
          snapshot?.status === "active" &&
          !currentPlayer.submittedCurrentRound;
        enterGameLink.hidden = !canEnterGame;
        if (canEnterGame && actions.gameUrl) {
          enterGameLink.href = actions.gameUrl;
        }
      }
      if (viewReportLink) {
        const canViewReport = currentPlayer.inRoom && (snapshot?.status === "report" || snapshot?.status === "finished");
        viewReportLink.hidden = !canViewReport;
        if (canViewReport && actions.reportUrl) {
          viewReportLink.href = actions.reportUrl;
        }
      }
      if (viewFinalLink) {
        const canViewFinal = currentPlayer.inRoom && snapshot?.status === "finished";
        viewFinalLink.hidden = !canViewFinal;
        if (canViewFinal && actions.finalUrl) {
          viewFinalLink.href = actions.finalUrl;
        }
      }
    }

    function renderSeatList(currentSnapshot) {
      const visibleSeats = currentSnapshot?.seats.filter((seat) => !seat.isBot) || [];
      if (!currentSnapshot || visibleSeats.length === 0) {
        seatList.innerHTML = `
          <article class="panel" style="padding: 18px; background: rgba(148, 163, 184, 0.08);">
            <p style="margin: 0; color: #64748b;">当前还没有真人席位快照。</p>
          </article>
        `;
        return;
      }

      const canManageBots = Boolean(currentSnapshot.permissions.can_manage_bots);
      const canChooseSeat = currentSnapshot.permissions.can_choose_seat !== false;
      const cards = visibleSeats.map((seat) => {
        const badges = [];
        badges.push(seat.occupied ? "已占用" : "空席");
        badges.push(
          seat.occupied
            ? seat.ready
              ? DEFAULT_READY_LABELS.ready
              : DEFAULT_READY_LABELS.not_ready
            : DEFAULT_READY_LABELS.open
        );
        if (seat.isHost) badges.push("房主");
        if (seat.isBot) badges.push("机器人");
        if (seat.currentUserHere) badges.push("你在此席位");
        if (seat.submittedCurrentRound) badges.push("已提交");

        const actionsMarkup = [];
        if (
          !seat.occupied &&
          !currentSnapshot.currentPlayer.inRoom &&
          canChooseSeat &&
          actions.takeSeatUrl
        ) {
          actionsMarkup.push(
            `<button type="button" class="ghost" data-seat-action="take-seat" data-seat-id="${escapeHtml(seat.seatId)}">加入此席</button>`
          );
        }
        if (!seat.occupied && canManageBots && actions.addBotUrl) {
          actionsMarkup.push(
            `<button type="button" class="ghost" data-seat-action="add-bot" data-seat-id="${escapeHtml(seat.seatId)}">补机器人</button>`
          );
        }
        if (seat.isBot && canManageBots && actions.removeBotUrl) {
          actionsMarkup.push(
            `<button type="button" class="ghost" data-seat-action="remove-bot" data-seat-id="${escapeHtml(seat.seatId)}">移除机器人</button>`
          );
        }

        return `
          <article class="panel multiplayer-seat-card" style="padding: 18px; background: rgba(148, 163, 184, 0.08);" data-testid="multiplayer-seat-card-${escapeHtml(seat.seatId)}">
            <div class="multiplayer-seat-card-head" style="display: flex; justify-content: space-between; gap: 12px; align-items: flex-start;">
              <div class="multiplayer-seat-card-identity">
                <p class="eyebrow" style="margin-bottom: 6px;">${escapeHtml(seat.label)}</p>
                <h3 style="margin: 0 0 8px;">${escapeHtml(seat.playerName)}</h3>
              </div>
              <div class="inline-badges multiplayer-seat-card-badges">${badges.map(createBadge).join("")}</div>
            </div>
            <p class="multiplayer-seat-card-copy" style="margin: 12px 0 0; color: #475569; font-size: 0.95rem;">
              ${seat.occupied ? `席位状态：${seat.submittedCurrentRound ? "本轮已提交" : (seat.ready ? "已准备" : "未准备")}` : "席位状态：等待加入"}
            </p>
            <p class="multiplayer-seat-card-copy is-muted" style="margin: 6px 0 0; color: #64748b; font-size: 0.92rem;">
              ${seat.occupied ? (seat.currentUserHere ? "你当前就坐在这个真人席位。" : "当前真人席位已就坐。") : "空席可由玩家加入。"}
            </p>
            <div class="action-row" style="margin-top: 16px; display: flex; flex-wrap: wrap; gap: 10px;">
              ${actionsMarkup.join("") || '<span style="color: #94a3b8; font-size: 0.92rem;">当前无可执行席位操作。</span>'}
            </div>
          </article>
        `;
      });

      seatList.innerHTML = cards.join("");
    }

    function renderBotList(currentSnapshot) {
      const botSeats = currentSnapshot?.seats.filter((seat) => seat.isBot) || [];
      if (botSeats.length === 0) {
        botList.innerHTML = '<p style="margin: 0; color: #64748b;">当前没有机器人席位。</p>';
        return;
      }

      botList.innerHTML = botSeats
        .map(
          (seat) => `
            <article class="bot-summary-row" data-testid="multiplayer-bot-seat-${escapeHtml(seat.seatId)}">
              <div class="bot-summary-main">
                <strong>${escapeHtml(seat.playerName)}</strong>
                <span>${escapeHtml(seat.label)}</span>
              </div>
              <div class="bot-summary-meta">
                <span class="badge">${seat.ready ? "已准备" : "未准备"}</span>
                <span class="bot-summary-copy">机器人补位已锁定该席位。</span>
              </div>
            </article>
          `
        )
        .join("");
    }

    function updateControls(currentSnapshot) {
      const currentPlayer = currentSnapshot?.currentPlayer || {
        inRoom: false,
        ready: false,
        isHost: false,
      };
      const permissions = currentSnapshot?.permissions || {};
      const disableAll = actionInFlight;
      refreshButton.disabled = disableAll;

      joinButton.disabled =
        disableAll ||
        currentPlayer.inRoom ||
        currentSnapshot?.summary.availableSeats === 0 ||
        !actions.joinUrl;
      leaveButton.disabled =
        disableAll ||
        !currentPlayer.inRoom ||
        permissions.can_leave === false ||
        !actions.leaveUrl;
      readyButton.disabled =
        disableAll ||
        !currentPlayer.inRoom ||
        permissions.can_toggle_ready === false ||
        !actions.toggleReadyUrl;
      homeCitySelect.disabled =
        disableAll ||
        !currentPlayer.inRoom ||
        permissions.can_update_home_city === false ||
        !actions.homeCityUrl;
      homeCitySaveButton.disabled = homeCitySelect.disabled;
      readyButton.textContent = currentPlayer.ready ? "取消准备" : "标记准备";
      startButton.disabled =
        disableAll ||
        !currentPlayer.inRoom ||
        !currentPlayer.isHost ||
        permissions.can_start === false ||
        !actions.startUrl;
      joinButton.textContent = currentPlayer.inRoom ? "已加入房间" : "加入房间";
    }

    function renderSnapshot(currentSnapshot, sourceLabel) {
      if (!currentSnapshot) {
        pollingStatusNode.textContent = "等待快照";
        pollingMetaNode.textContent = "当前还没有可用的房间快照。";
        updateControls(null);
        return;
      }

      roomCodeNode.textContent = `房间码 ${currentSnapshot.roomCode || "待分配"}`;
      roomStatusNode.textContent = currentSnapshot.statusLabel;
      hostNameNode.textContent = currentSnapshot.hostName;
      hostMetaNode.textContent = currentSnapshot.hostSeatLabel
        ? `当前房主位于 ${currentSnapshot.hostSeatLabel}。`
        : "房主负责最终开局。";
      seatSummaryNode.textContent = `${currentSnapshot.summary.occupiedSeats} / ${currentSnapshot.summary.totalSeats}`;
      seatMetaNode.textContent = `空位 ${currentSnapshot.summary.availableSeats} 个，可继续加入或补机器人。`;
      readySummaryNode.textContent = String(currentSnapshot.summary.readySeats);
      readyMetaNode.textContent = `当前 ${currentSnapshot.summary.occupiedSeats} 个已占用席位中，有 ${currentSnapshot.summary.readySeats} 个已准备。`;
      botSummaryNode.textContent = String(currentSnapshot.summary.botSeats);
      botMetaNode.textContent = currentSnapshot.summary.botSeats
        ? `共有 ${currentSnapshot.summary.botSeats} 个机器人席位。`
        : "当前无需机器人补位。";
      renderCurrentPlayer(currentSnapshot.currentPlayer);
      renderSeatList(currentSnapshot);
      renderBotList(currentSnapshot);

      latestSyncLabel = formatTimestamp(currentSnapshot.updatedAt);
      lastSyncAtNode.textContent = latestSyncLabel;
      syncSourceNode.textContent = sourceLabel || "已同步房间快照。";
      pollingStatusNode.textContent = currentSnapshot.started ? "对局已开始" : "轮询中";
      pollingMetaNode.textContent = currentSnapshot.started
        ? "房间已进入对局状态，后续控制会以服务端权限为准。"
        : `每 ${Math.round(pollIntervalMs / 1000)} 秒自动刷新一次。`;
      updateControls(currentSnapshot);
    }

    function buildActionPayload(extraPayload) {
      return {
        room_id: snapshot?.roomId || roomId || "",
        room_code: snapshot?.roomCode || pageData.roomCode || "",
        ...(extraPayload || {}),
      };
    }

    async function requestJson(url, options) {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const errorMessage =
          data.detail ||
          data.message ||
          labels.networkFailedMessage ||
          "请求失败，请稍后再试。";
        throw new Error(errorMessage);
      }
      return data;
    }

    async function refreshSnapshot(options = {}) {
      if (!actions.snapshotUrl) {
        pollingStatusNode.textContent = "未配置";
        pollingMetaNode.textContent = "当前页面尚未接入房间快照接口。";
        return;
      }

      const requestId = ++pollRequestId;
      if (!options.silent) {
        pollingStatusNode.textContent = "刷新中";
        pollingMetaNode.textContent = "正在拉取最新房间快照...";
      }

      try {
        const data = await requestJson(actions.snapshotUrl, { method: "GET" });
        if (requestId !== pollRequestId) {
          return;
        }
        const nextSnapshot = normalizeSnapshot(data);
        if (nextSnapshot) {
          snapshot = nextSnapshot;
          clearFeedback();
          renderSnapshot(snapshot, options.sourceLabel || "已同步最新房间快照。");
        }
      } catch (error) {
        if (requestId !== pollRequestId) {
          return;
        }
        setError(error.message);
        pollingStatusNode.textContent = "刷新失败";
        pollingMetaNode.textContent = "保留最近一次成功快照。";
        updateControls(snapshot);
      }
    }

    async function performAction(actionUrl, payload, successMessage, sourceLabel) {
      if (!actionUrl || actionInFlight) {
        return;
      }

      actionInFlight = true;
      clearFeedback();
      updateControls(snapshot);
      pollingStatusNode.textContent = "提交中";
      pollingMetaNode.textContent = "正在提交房间操作...";

      try {
        const data = await requestJson(actionUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": window.__EXSCHOOL_CSRF_TOKEN__ || "",
          },
          body: JSON.stringify(buildActionPayload(payload)),
        });
        const nextSnapshot = normalizeSnapshot(data);
        if (nextSnapshot) {
          snapshot = nextSnapshot;
        }
        renderSnapshot(snapshot, sourceLabel || "已应用最新房间操作。");
        setMessage(data.message || successMessage || "操作已提交。");
        if (!nextSnapshot) {
          await refreshSnapshot({ silent: true, sourceLabel: sourceLabel || "已同步最新房间快照。" });
        }
      } catch (error) {
        setError(error.message);
        pollingStatusNode.textContent = "操作失败";
        pollingMetaNode.textContent = "请检查返回错误后重试。";
      } finally {
        actionInFlight = false;
        updateControls(snapshot);
      }
    }

    refreshButton.addEventListener("click", () => {
      void refreshSnapshot({ sourceLabel: "手动刷新成功。" });
    });

    joinButton.addEventListener("click", () => {
      void performAction(
        actions.joinUrl,
        {},
        labels.joinSuccessMessage || "已提交加入房间请求。",
        "已刷新加入后的房间快照。"
      );
    });

    leaveButton.addEventListener("click", () => {
      void performAction(
        actions.leaveUrl,
        {},
        labels.leaveSuccessMessage || "已离开房间。",
        "已刷新离开后的房间快照。"
      );
    });

    readyButton.addEventListener("click", () => {
      void performAction(
        actions.toggleReadyUrl,
        { ready: !(snapshot?.currentPlayer.ready) },
        snapshot?.currentPlayer.ready
          ? labels.unreadySuccessMessage || "已取消准备。"
          : labels.readySuccessMessage || "已标记准备。",
        "已刷新准备状态。"
      );
    });

    homeCitySaveButton.addEventListener("click", () => {
      void performAction(
        actions.homeCityUrl,
        { home_city: homeCitySelect.value },
        labels.homeCitySuccessMessage || "主场城市已更新。",
        "已刷新主场城市。"
      );
    });

    startButton.addEventListener("click", () => {
      void performAction(
        actions.startUrl,
        {},
        labels.startSuccessMessage || "已提交开始对局请求。",
        "已刷新开局后的房间快照。"
      );
    });

    seatList.addEventListener("click", (event) => {
      const target = event.target.closest("button[data-seat-action]");
      if (!target) {
        return;
      }
      const seatId = target.dataset.seatId || "";
      const seatAction = target.dataset.seatAction || "";
      if (!seatId || !seatAction) {
        return;
      }

      if (seatAction === "take-seat") {
        void performAction(
          actions.takeSeatUrl,
          { seat_id: seatId },
          labels.takeSeatSuccessMessage || "已加入选定席位。",
          "已刷新席位加入结果。"
        );
      }

      if (seatAction === "add-bot") {
        void performAction(
          actions.addBotUrl,
          { seat_id: seatId },
          labels.addBotSuccessMessage || "已补入机器人。",
          "已刷新机器人补位结果。"
        );
      }

      if (seatAction === "remove-bot") {
        void performAction(
          actions.removeBotUrl,
          { seat_id: seatId },
          labels.removeBotSuccessMessage || "已移除机器人。",
          "已刷新机器人移除结果。"
        );
      }
    });

    renderSnapshot(snapshot, snapshot ? "已加载初始房间快照。" : "等待首次房间快照。");
    void refreshSnapshot({ silent: Boolean(snapshot), sourceLabel: snapshot ? "已同步最新房间快照。" : "首次房间快照已加载。" });
    pollTimer = window.setInterval(() => {
      void refreshSnapshot({ silent: true, sourceLabel: "已同步最新房间快照。" });
    }, pollIntervalMs);

    window.addEventListener("beforeunload", () => {
      if (pollTimer) {
        window.clearInterval(pollTimer);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootMultiplayerRoomPage, { once: true });
  } else {
    bootMultiplayerRoomPage();
  }
})();
