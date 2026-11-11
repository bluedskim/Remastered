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

  function removeJobListItem(btn) {
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

    function finishDelete(resp) {
      if (!resp.ok) throw new Error("delete failed");
      if (redirectHome) {
        window.location.href = "/";
        return;
      }
      if (onSuccess) onSuccess();
    }

    function postRemove() {
      return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/remove", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
    }

    fetch("/api/jobs/" + encodeURIComponent(jobId), {
      method: "DELETE",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (resp) {
        if (resp.ok) return resp;
        if (resp.status === 405) return postRemove();
        throw new Error("delete failed");
      })
      .catch(function (err) {
        if (err && err.message === "delete failed") throw err;
        return postRemove();
      })
      .then(finishDelete)
      .catch(function () {
        setDeleteInFlight(btn, false);
        window.alert("Could not delete this job. Try again.");
      });
  }

  function onDeleteClick(btn) {
    const jobId = btn.getAttribute("data-job-delete");
    const inList = !!btn.closest(".job-list");
    deleteJob(jobId, {
      button: btn,
      redirectHome: !inList,
      onSuccess: inList
        ? function () {
            removeJobListItem(btn);
          }
        : undefined,
    });
  }

  window.initJobDeleteControls = function () {
    /* List “load more” still calls this; clicks use document delegation. */
  };

  if (!window.__remasteredJobDeleteClickBound) {
    window.__remasteredJobDeleteClickBound = true;
    document.addEventListener("click", function (event) {
      const btn = event.target.closest("[data-job-delete]");
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      onDeleteClick(btn);
    });
  }
})();
