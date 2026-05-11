(function () {
  const pageData = window.ExschoolBase?.readJsonScript("auth-page-data");
  const sendCodeButton = document.getElementById("send-code-button");
  const registerEmailInput = document.getElementById("register-email");
  const codeStatus = document.getElementById("code-status");

  if (!pageData || !sendCodeButton || !registerEmailInput || !codeStatus) {
    return;
  }

  function setCodeStatus(text, kind) {
    codeStatus.textContent = text;
    codeStatus.className = `auth-status ${kind || ""}`.trim();
  }

  function startCooldown(seconds) {
    let remaining = seconds;
    sendCodeButton.disabled = true;
    sendCodeButton.textContent = `${remaining}s`;
    const timer = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        window.clearInterval(timer);
        sendCodeButton.disabled = false;
        sendCodeButton.textContent = pageData.sendButtonLabel;
        return;
      }
      sendCodeButton.textContent = `${remaining}s`;
    }, 1000);
  }

  sendCodeButton.addEventListener("click", async () => {
    const email = registerEmailInput.value.trim();
    if (!email) {
      setCodeStatus(pageData.missingEmailMessage, "error");
      return;
    }

    setCodeStatus(pageData.sendingMessage);
    try {
      const response = await fetch(pageData.sendCodeUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": window.__EXSCHOOL_CSRF_TOKEN__ || "",
        },
        body: JSON.stringify({ email, purpose: pageData.codePurpose }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        setCodeStatus(data.detail || pageData.sendFailedMessage, "error");
        return;
      }

      setCodeStatus(data.message || pageData.sendSuccessMessage, "success");
      startCooldown(Number(data.cooldown_seconds || pageData.defaultCooldownSeconds));
    } catch (error) {
      setCodeStatus(pageData.networkFailedMessage, "error");
    }
  });
})();
