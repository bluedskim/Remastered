(function () {
  const root = document.getElementById("signup-root");
  if (!root) return;

  const cooldownSec = parseInt(root.dataset.cooldown || "60", 10);
  const emailInput = document.getElementById("signup-email");
  const sendBtn = document.getElementById("send-verification-btn");
  const sendStatus = document.getElementById("send-verification-status");

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
    sendStatus.textContent =
      "You can request another code in " + remaining + " seconds.";
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
      sendStatus.textContent = "Enter your email address.";
      return;
    }
    sendBtn.disabled = true;
    sendStatus.textContent = "Sending…";
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
        sendStatus.textContent =
          data.message || "Could not send verification code.";
        sendBtn.disabled = false;
        return;
      }
      sendStatus.textContent = "Verification code sent to your email.";
      startCooldown();
    } catch (e) {
      sendStatus.textContent = "Network error. Please try again.";
      sendBtn.disabled = false;
    }
  });
})();
