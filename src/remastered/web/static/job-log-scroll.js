(function () {
  "use strict";

  var JOB_STATUS_ID = "job-status";
  var JOB_STATUS_LOG_ID = "job-status-log";
  var NEAR_BOTTOM_PX = 48;

  function jobStatusTarget(el) {
    if (!el) return null;
    if (el.id === JOB_STATUS_ID || el.id === JOB_STATUS_LOG_ID) return el;
    if (el.closest) {
      if (el.closest("#" + JOB_STATUS_ID)) {
        return document.getElementById(JOB_STATUS_ID);
      }
      if (el.closest("#" + JOB_STATUS_LOG_ID)) {
        return document.getElementById(JOB_STATUS_LOG_ID);
      }
    }
    return null;
  }

  function logBox(root) {
    if (!root) return null;
    return root.querySelector("[data-job-log-box], .log-box");
  }

  function distanceFromBottom(box) {
    return box.scrollHeight - box.scrollTop - box.clientHeight;
  }

  function captureState(statusRoot) {
    var box = logBox(statusRoot);
    if (!box) return;
    statusRoot.dataset.jobLogScroll = JSON.stringify({
      scrollTop: box.scrollTop,
      stickBottom: distanceFromBottom(box) <= NEAR_BOTTOM_PX,
    });
  }

  function applyState(statusRoot) {
    var raw = statusRoot.dataset.jobLogScroll;
    if (!raw) return;
    delete statusRoot.dataset.jobLogScroll;
    var state;
    try {
      state = JSON.parse(raw);
    } catch (_e) {
      return;
    }
    var box = logBox(statusRoot);
    if (!box) return;
    requestAnimationFrame(function () {
      if (state.stickBottom) {
        box.scrollTop = box.scrollHeight;
      } else if (typeof state.scrollTop === "number") {
        box.scrollTop = state.scrollTop;
      }
    });
  }

  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    var statusRoot = jobStatusTarget(evt.detail && evt.detail.target);
    if (!statusRoot) return;
    captureState(statusRoot);
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var statusRoot = jobStatusTarget(evt.detail && evt.detail.target);
    if (!statusRoot) return;
    applyState(statusRoot);
  });
})();
