(function () {
  "use strict";

  function setInFlight(root, inFlight) {
    if (!root) return;
    root.querySelectorAll("[data-feedback-rating]").forEach(function (btn) {
      btn.disabled = !!inFlight;
      if (inFlight) btn.dataset.feedbackInFlight = "1";
      else delete btn.dataset.feedbackInFlight;
    });
  }

  function applyRating(root, rating) {
    root.querySelectorAll("[data-feedback-rating]").forEach(function (btn) {
      var v = Number(btn.getAttribute("data-feedback-rating"));
      var active = rating !== null && rating === v;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.classList.toggle("job-feedback-btn--active", active);
    });
    if (rating === null || rating === undefined) {
      root.removeAttribute("data-user-rating");
    } else {
      root.setAttribute("data-user-rating", String(rating));
    }
  }

  function updateAdminCounts(root, counts) {
    var el = root.querySelector("[data-feedback-counts]");
    if (!el || !counts) return;
    el.textContent =
      "\uD83D\uDC4D " + (counts.up || 0) + " \u00b7 \uD83D\uDC4E " + (counts.down || 0);
  }

  function sendVote(root, jobId, rating) {
    if (!jobId || root.dataset.feedbackInFlight === "1") return;
    setInFlight(root, true);
    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/feedback", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ rating: rating }),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () {
            return {};
          }).then(function (data) {
            var msg =
              (data && data.detail) ||
              "Could not save your feedback. Try again.";
            throw new Error(msg);
          });
        }
        return resp.json();
      })
      .then(function (data) {
        applyRating(root, data.rating == null ? null : Number(data.rating));
        if (data.counts) updateAdminCounts(root, data.counts);
      })
      .catch(function (err) {
        window.alert(
          err && err.message
            ? err.message
            : "Could not save your feedback. Try again."
        );
      })
      .finally(function () {
        setInFlight(root, false);
      });
  }

  function initFeedbackRoot(root) {
    if (!root || root.dataset.feedbackReady === "1") return;
    root.dataset.feedbackReady = "1";
    var jobId = root.getAttribute("data-job-id");
    var initial = root.getAttribute("data-user-rating");
    var rating =
      initial === "" || initial === null || initial === undefined
        ? null
        : Number(initial);
    if (rating !== 1 && rating !== -1) rating = null;
    applyRating(root, rating);
    root.querySelectorAll("[data-feedback-rating]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var v = Number(btn.getAttribute("data-feedback-rating"));
        if (v !== 1 && v !== -1) return;
        sendVote(root, jobId, v);
      });
    });
  }

  function scan() {
    document.querySelectorAll("[data-job-feedback]").forEach(initFeedbackRoot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  document.body.addEventListener("htmx:afterSwap", scan);
  document.body.addEventListener("htmx:oobAfterSwap", scan);
})();
