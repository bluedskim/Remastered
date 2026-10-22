(function () {
  const root = document.getElementById("signup-root");
  if (!root) return;

  const cooldownSec = parseInt(root.dataset.cooldown || "60", 10);
  const emailInput = document.getElementById("signup-email");
  const sendBtn = document.getElementById("send-verification-btn");
  const sendStatus = document.getElementById("send-verification-status");
  const nicknameInput = document.getElementById("signup-nickname");
  const nicknameStatus = document.getElementById("nickname-status");

  let cooldownTimer = null;

  function setSendCooldown(remaining) {
    if (remaining <= 0) {
      sendBtn.disabled = false;
      sendStatus.textContent = "";
      if (cooldownTimer) {
        clearInterval(cooldownTimer);
        cooldownTimer = null;
      }
      return;
    }
    sendBtn.disabled = true;
    sendStatus.textContent = remaining + "초 후 다시 요청할 수 있습니다.";
  }

  function startCooldown() {
    let left = cooldownSec;
    setSendCooldown(left);
    cooldownTimer = setInterval(function () {
      left -= 1;
      setSendCooldown(left);
    }, 1000);
  }

  sendBtn.addEventListener("click", async function () {
    const email = (emailInput.value || "").trim();
    if (!email) {
      sendStatus.textContent = "이메일을 입력해 주세요.";
      return;
    }
    sendBtn.disabled = true;
    sendStatus.textContent = "전송 중…";
    try {
      const body = new URLSearchParams();
      body.set("login_id", email);
      const resp = await fetch("/auth/send-verification", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      const data = await resp.json().catch(function () {
        return {};
      });
      if (!resp.ok) {
        sendStatus.textContent = data.message || "인증코드 전송에 실패했습니다.";
        sendBtn.disabled = false;
        return;
      }
      sendStatus.textContent = "인증코드를 이메일로 보냈습니다.";
      startCooldown();
    } catch (e) {
      sendStatus.textContent = "네트워크 오류가 발생했습니다.";
      sendBtn.disabled = false;
    }
  });

  let nicknameTimer = null;
  nicknameInput.addEventListener("input", function () {
    if (nicknameTimer) clearTimeout(nicknameTimer);
    const value = nicknameInput.value.trim();
    if (!value) {
      nicknameStatus.textContent = "";
      return;
    }
    nicknameTimer = setTimeout(async function () {
      try {
        const resp = await fetch(
          "/auth/nickname-available?nickname=" + encodeURIComponent(value)
        );
        const data = await resp.json();
        if (data.message) {
          nicknameStatus.textContent = data.message;
          nicknameStatus.className = "muted small nickname-bad";
          return;
        }
        if (data.available) {
          nicknameStatus.textContent = "사용 가능한 닉네임입니다.";
          nicknameStatus.className = "muted small nickname-ok";
        } else {
          nicknameStatus.textContent = "이미 사용 중인 닉네임입니다.";
          nicknameStatus.className = "muted small nickname-bad";
        }
      } catch (e) {
        nicknameStatus.textContent = "";
      }
    }, 300);
  });
})();
