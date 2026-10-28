(function () {
  const CONFIRM_MESSAGE =
    "Permanently delete this job and its audio files? This cannot be undone.";

  function deleteJob(jobId, options) {
    const onSuccess = options && options.onSuccess;
    const redirectHome = !!(options && options.redirectHome);
    if (!jobId) return;
    if (!window.confirm(CONFIRM_MESSAGE)) return;
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
        window.alert("Could not delete this job. Try again.");
      });
  }

  function wireListDeleteButtons() {
    document.querySelectorAll("[data-job-delete]").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        const jobId = btn.getAttribute("data-job-delete");
        deleteJob(jobId, {
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
    });
  }

  function wireDetailDeleteButton() {
    const btn = document.getElementById("job-delete-btn");
    if (!btn || btn.dataset.deleteWired === "1") return;
    btn.dataset.deleteWired = "1";
    const jobId = btn.getAttribute("data-job-delete");
    btn.addEventListener("click", function () {
      deleteJob(jobId, { redirectHome: true });
    });
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
