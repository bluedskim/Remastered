(function () {
  "use strict";

  var DRIFT_SEC = 0.05;
  var SYNC_MS = 250;

  function isTypingTarget(el) {
    if (!el || !el.tagName) return false;
    var tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
  }

  function formatTime(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function waitCanPlay(audio) {
    return new Promise(function (resolve) {
      if (audio.readyState >= 2) {
        resolve();
        return;
      }
      audio.addEventListener("canplay", function () { resolve(); }, { once: true });
    });
  }

  function initAbPlayer(root) {
    if (root.dataset.abInitialized === "1") return;
    root.dataset.abInitialized = "1";

    var original = root.querySelector('[data-audio="original"]');
    var remastered = root.querySelector('[data-audio="remastered"]');
    if (!original || !remastered) return;

    var playBtn = root.querySelector("[data-ab-play]");
    var seek = root.querySelector("[data-ab-seek]");
    var vol = root.querySelector("[data-ab-volume]");
    var timeCur = root.querySelector("[data-ab-time-current]");
    var timeDur = root.querySelector("[data-ab-time-duration]");
    var activeLabel = root.querySelector("[data-ab-active-label]");
    var sideButtons = root.querySelectorAll("[data-ab-side]");

    var activeSide = "remastered";
    var userWantsPlay = false;
    var seeking = false;
    var syncTimer = null;

    function audible() {
      return activeSide === "original" ? original : remastered;
    }

    function mutedOther() {
      return activeSide === "original" ? remastered : original;
    }

    function applyMute() {
      original.muted = activeSide !== "original";
      remastered.muted = activeSide !== "remastered";
      original.volume = vol ? Number(vol.value) / 100 : 1;
      remastered.volume = vol ? Number(vol.value) / 100 : 1;
      sideButtons.forEach(function (btn) {
        var side = btn.getAttribute("data-ab-side");
        btn.setAttribute("aria-pressed", side === activeSide ? "true" : "false");
      });
      if (activeLabel) {
        activeLabel.textContent = activeSide === "original" ? "원본" : "리마스터";
      }
    }

    function setPlayIcon(playing) {
      if (!playBtn) return;
      playBtn.textContent = playing ? "❚❚" : "▶";
      playBtn.setAttribute("aria-label", playing ? "일시정지" : "재생");
    }

    function duration() {
      var d = Math.max(original.duration || 0, remastered.duration || 0);
      if (!isFinite(d)) d = 0;
      return d;
    }

    function updateTimeUi() {
      var master = audible();
      var t = master.currentTime || 0;
      var d = duration();
      if (timeCur) timeCur.textContent = formatTime(t);
      if (timeDur) timeDur.textContent = formatTime(d);
      if (seek && !seeking && d > 0) {
        seek.value = String(Math.round((t / d) * 1000));
      }
    }

    function syncDrift() {
      if (!userWantsPlay) return;
      var master = audible();
      var slave = mutedOther();
      if (master.paused) return;
      var delta = slave.currentTime - master.currentTime;
      if (Math.abs(delta) > DRIFT_SEC) {
        slave.currentTime = master.currentTime;
      }
    }

    function pauseBoth() {
      userWantsPlay = false;
      original.pause();
      remastered.pause();
      setPlayIcon(false);
    }

    function playBothFromGesture() {
      userWantsPlay = true;
      var t = audible().currentTime;
      original.currentTime = t;
      remastered.currentTime = t;
      return Promise.all([waitCanPlay(original), waitCanPlay(remastered)]).then(function () {
        return Promise.all([original.play(), remastered.play()]);
      }).then(function () {
        setPlayIcon(true);
      }).catch(function () {
        pauseBoth();
      });
    }

    function setActiveSide(side) {
      if (side !== "original" && side !== "remastered") return;
      var master = audible();
      var t = master.currentTime;
      activeSide = side;
      applyMute();
      original.currentTime = t;
      remastered.currentTime = t;
      updateTimeUi();
    }

    function onSeekInput() {
      if (!seek) return;
      seeking = true;
      var d = duration();
      var ratio = Number(seek.value) / 1000;
      var t = d * ratio;
      original.currentTime = t;
      remastered.currentTime = t;
      updateTimeUi();
    }

    function onSeekCommit() {
      seeking = false;
      if (userWantsPlay) {
        playBothFromGesture();
      }
    }

    applyMute();

    if (playBtn) {
      playBtn.addEventListener("click", function () {
        if (userWantsPlay && !audible().paused) {
          pauseBoth();
        } else {
          playBothFromGesture();
        }
      });
    }

    sideButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        setActiveSide(btn.getAttribute("data-ab-side"));
      });
    });

    if (seek) {
      seek.addEventListener("input", onSeekInput);
      seek.addEventListener("change", onSeekCommit);
    }

    if (vol) {
      vol.addEventListener("input", applyMute);
    }

    [original, remastered].forEach(function (audio) {
      audio.addEventListener("timeupdate", updateTimeUi);
      audio.addEventListener("loadedmetadata", updateTimeUi);
      audio.addEventListener("ended", function () {
        if (original.ended && remastered.ended) {
          pauseBoth();
          updateTimeUi();
        }
      });
      audio.addEventListener("waiting", function () {
        if (userWantsPlay) pauseBoth();
      });
      audio.addEventListener("stalled", function () {
        if (userWantsPlay) pauseBoth();
      });
    });

    syncTimer = window.setInterval(function () {
      syncDrift();
      updateTimeUi();
    }, SYNC_MS);

    root.addEventListener("ab-player-destroy", function () {
      if (syncTimer) window.clearInterval(syncTimer);
    });

    root._abHandleKey = function (e) {
      if (!root.isConnected) return;
      if (isTypingTarget(document.activeElement)) return;
      if (e.code === "Space") {
        e.preventDefault();
        if (userWantsPlay && !audible().paused) pauseBoth();
        else playBothFromGesture();
      } else if (e.key === "a" || e.key === "A") {
        setActiveSide("original");
      } else if (e.key === "b" || e.key === "B") {
        setActiveSide("remastered");
      }
    };
  }

  function bindGlobalKeys() {
    if (window.__abPlayerKeysBound) return;
    window.__abPlayerKeysBound = true;
    document.addEventListener("keydown", function (e) {
      var root = document.querySelector("[data-ab-compare][data-ab-initialized]");
      if (!root || !root._abHandleKey) return;
      root._abHandleKey(e);
    });
  }

  function scan() {
    bindGlobalKeys();
    document.querySelectorAll("[data-ab-compare]").forEach(initAbPlayer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  document.body.addEventListener("htmx:afterSwap", scan);
  document.body.addEventListener("htmx:oobAfterSwap", scan);
})();
