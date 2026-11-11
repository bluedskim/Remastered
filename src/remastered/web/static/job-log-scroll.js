(function () {
  "use strict";

  var JOB_STATUS_ID = "job-status";
  var JOB_STATUS_LOG_ID = "job-status-log";
  var NEAR_BOTTOM_PX = 48;

  function logRoot() {
    return document.getElementById(JOB_STATUS_LOG_ID);
  }

  function logBox(root) {
    if (!root) return null;
    return root.querySelector("[data-job-log-box], .log-box");
  }

  function distanceFromBottom(box) {
    return box.scrollHeight - box.scrollTop - box.clientHeight;
  }

  function captureLogScroll() {
    var root = logRoot();
    var box = logBox(root);
    if (!root || !box) return;
    root.dataset.jobLogScroll = JSON.stringify({
      scrollTop: box.scrollTop,
      stickBottom: distanceFromBottom(box) <= NEAR_BOTTOM_PX,
    });
  }

  function applyLogScroll() {
    var root = logRoot();
    if (!root) return;
    var raw = root.dataset.jobLogScroll;
    if (!raw) return;
    delete root.dataset.jobLogScroll;
    var state;
    try {
      state = JSON.parse(raw);
    } catch (_e) {
      return;
    }
    var box = logBox(root);
    if (!box) return;
    requestAnimationFrame(function () {
      if (state.stickBottom) {
        box.scrollTop = box.scrollHeight;
      } else if (typeof state.scrollTop === "number") {
        box.scrollTop = state.scrollTop;
      }
    });
  }

  var applyScheduled = false;

  function scheduleApplyLogScroll() {
    if (applyScheduled) return;
    applyScheduled = true;
    requestAnimationFrame(function () {
      applyScheduled = false;
      applyLogScroll();
    });
  }

  function swapTarget(evt) {
    return evt.detail && evt.detail.target;
  }

  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    var target = swapTarget(evt);
    if (!target || target.id !== JOB_STATUS_ID) return;
    captureLogScroll();
  });

  document.body.addEventListener("htmx:oobBeforeSwap", function (evt) {
    var target = swapTarget(evt);
    if (!target || target.id !== JOB_STATUS_LOG_ID) return;
    captureLogScroll();
  });

  document.body.addEventListener("htmx:oobAfterSwap", function (evt) {
    var target = swapTarget(evt);
    if (!target || target.id !== JOB_STATUS_LOG_ID) return;
    scheduleApplyLogScroll();
  });

  document.body.addEventListener("htmx:afterSettle", function () {
    var root = logRoot();
    if (root && root.dataset.jobLogScroll) {
      scheduleApplyLogScroll();
    }
  });
})();
