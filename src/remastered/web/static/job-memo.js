(function () {
  "use strict";

  var MAX_LEN = 100;

  function codePointLength(str) {
    return Array.from(str).length;
  }

  function updateCounter(root) {
    var input = root.querySelector("[data-job-memo-input]");
    var counter = root.querySelector("[data-job-memo-counter]");
    if (!input || !counter) return;
    var len = codePointLength(input.value);
    counter.textContent = len + " / " + MAX_LEN;
    if (len > MAX_LEN) {
      counter.classList.add("job-memo-counter--over");
    } else {
      counter.classList.remove("job-memo-counter--over");
    }
  }

  function setStatus(root, text, isError) {
    var el = root.querySelector("[data-job-memo-status]");
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("job-memo-status--error", "job-memo-status--ok");
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle("job-memo-status--error", !!isError);
    el.classList.toggle("job-memo-status--ok", !isError);
  }

  function setSaving(root, saving) {
    var btn = root.querySelector("[data-job-memo-save]");
    var input = root.querySelector("[data-job-memo-input]");
    if (btn) {
      btn.disabled = !!saving;
      btn.textContent = saving ? "Saving…" : "Save";
    }
    if (input) input.disabled = !!saving;
    root.dataset.memoInFlight = saving ? "1" : "";
  }

  function saveMemo(root) {
    if (root.dataset.memoInFlight === "1") return;
    var jobId = root.getAttribute("data-job-id");
    var input = root.querySelector("[data-job-memo-input]");
    if (!jobId || !input) return;

    var value = input.value;
    if (codePointLength(value) > MAX_LEN) {
      setStatus(root, "Memo must be at most 100 characters.", true);
      return;
    }

    setSaving(root, true);
    setStatus(root, "", false);

    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/memo", {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ memo: value }),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () {
            return {};
          }).then(function (data) {
            var msg =
              (data && data.detail) || "Could not save your memo. Try again.";
            throw new Error(msg);
          });
        }
        return resp.json();
      })
      .then(function (data) {
        var saved = data.memo == null ? "" : String(data.memo);
        input.value = saved;
        updateCounter(root);
        setStatus(root, "Memo saved.", false);
      })
      .catch(function (err) {
        setStatus(
          root,
          err && err.message
            ? err.message
            : "Could not save your memo. Try again.",
          true
        );
      })
      .finally(function () {
        setSaving(root, false);
      });
  }

  function initMemoRoot(root) {
    if (!root || root.dataset.memoReady === "1") return;
    root.dataset.memoReady = "1";
    updateCounter(root);

    var input = root.querySelector("[data-job-memo-input]");
    if (input) {
      input.addEventListener("input", function () {
        updateCounter(root);
        setStatus(root, "", false);
      });
    }

    var saveBtn = root.querySelector("[data-job-memo-save]");
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        saveMemo(root);
      });
    }
  }

  function scan() {
    document.querySelectorAll("[data-job-memo]").forEach(initMemoRoot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
})();
