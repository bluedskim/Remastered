(function () {
  const CONFIRM_MESSAGE =
    "Permanently delete this job and its audio files? This cannot be undone.";

  function isDeleteBusy(btn) {
    if (!btn) return false;
    return (
      btn.disabled ||
      btn.dataset.deleteInFlight === "1" ||
      btn.dataset.deletePending === "1"
    );
  }

  function setDeleteInFlight(btn, inFlight) {
    if (!btn) return;
    btn.disabled = !!inFlight;
    if (inFlight) btn.dataset.deleteInFlight = "1";
    else delete btn.dataset.deleteInFlight;
  }

  function deleteJob(jobId, options) {
    const onSuccess = options && options.onSuccess;
    const redirectHome = !!(options && options.redirectHome);
    const btn = options && options.button;
    if (!jobId || isDeleteBusy(btn)) return;

    if (btn) btn.dataset.deletePending = "1";
    let confirmed = false;
    try {
      confirmed = window.confirm(CONFIRM_MESSAGE);
    } finally {
      if (btn && !confirmed) delete btn.dataset.deletePending;
    }
    if (!confirmed) return;
    if (btn) delete btn.dataset.deletePending;

    setDeleteInFlight(btn, true);

    fetch("/api/jobs/" + encodeURIComponent(jobId), {
      method: "DELETE",
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("delete failed");
        if (redirectHome) {
          window.location.href = "/";
          return;
        }
        if (onSuccess) onSuccess();
      })
      .catch(function () {
        setDeleteInFlight(btn, false);
        window.alert("Could not delete this job. Try again.");
      });
  }

  function wireDeleteButton(btn, options) {
    if (!btn || btn.dataset.deleteWired === "1") return;
    btn.dataset.deleteWired = "1";
    const jobId = btn.getAttribute("data-job-delete");
    btn.addEventListener("click", function (event) {
      if (event.currentTarget !== btn) return;
      event.preventDefault();
      event.stopPropagation();
      deleteJob(jobId, { button: btn, onSuccess: options.onSuccess, redirectHome: options.redirectHome });
    });
  }

  function wireListDeleteButtons() {
    document.querySelectorAll(".job-list [data-job-delete]").forEach(function (btn) {
      wireDeleteButton(btn, {
        onSuccess: function () {
          const item = btn.closest(".job-list-item");
          if (item) {
            item.remove();
            const list = document.querySelector(".job-list");
            if (list && !list.querySelector(".job-list-item")) {
              const section = list.closest("section");
              if (section) {
                list.remove();
                const empty = document.createElement("p");
                empty.className = "muted empty-state";
                empty.textContent = "No jobs yet. Start your first one above.";
                section.appendChild(empty);
              }
            }
            return;
          }
          window.location.reload();
        },
      });
    });
  }

  function wireDetailDeleteButton() {
    const btn = document.getElementById("job-delete-btn");
    wireDeleteButton(btn, { redirectHome: true });
  }

  window.initJobDeleteControls = function () {
    wireListDeleteButtons();
    wireDetailDeleteButton();
  };

  document.body.addEventListener("htmx:oobAfterSwap", function (event) {
    if (event.detail && event.detail.target && event.detail.target.id === "job-detail-actions") {
      wireDetailDeleteButton();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", window.initJobDeleteControls);
  } else {
    window.initJobDeleteControls();
  }
})();
