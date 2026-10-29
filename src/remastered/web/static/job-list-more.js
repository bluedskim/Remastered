(function () {
  "use strict";

  function wireShowMoreButton(btn) {
    if (!btn || btn.dataset.jobListMoreWired === "1") return;
    btn.dataset.jobListMoreWired = "1";

    btn.addEventListener("click", function () {
      if (btn.disabled || btn.dataset.loading === "1") return;

      const createdAt = btn.getAttribute("data-before-created-at");
      const jobId = btn.getAttribute("data-before-id");
      if (!createdAt || !jobId) return;

      btn.disabled = true;
      btn.dataset.loading = "1";
      const prevText = btn.textContent;
      btn.textContent = "Loading…";

      const params = new URLSearchParams({
        before_created_at: createdAt,
        before_id: jobId,
      });

      fetch("/api/jobs/list?" + params.toString(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (resp) {
          if (!resp.ok) throw new Error("load failed");
          return resp.json();
        })
        .then(function (data) {
          const list = document.querySelector(".job-list");
          if (list && data.html) {
            list.insertAdjacentHTML("beforeend", data.html);
          }
          if (window.initJobDeleteControls) {
            window.initJobDeleteControls();
          }
          document.body.dispatchEvent(new CustomEvent("remastered:job-list-appended"));

          if (!data.has_more || !data.next_cursor) {
            const wrap = btn.closest(".job-list-more-wrap");
            if (wrap) wrap.remove();
            else btn.remove();
            return;
          }
          btn.setAttribute(
            "data-before-created-at",
            data.next_cursor.created_at
          );
          btn.setAttribute("data-before-id", data.next_cursor.id);
          btn.disabled = false;
          delete btn.dataset.loading;
          btn.textContent = prevText;
        })
        .catch(function () {
          btn.disabled = false;
          delete btn.dataset.loading;
          btn.textContent = prevText;
          window.alert("Could not load more jobs. Try again.");
        });
    });
  }

  function init() {
    wireShowMoreButton(document.getElementById("job-list-more-btn"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
