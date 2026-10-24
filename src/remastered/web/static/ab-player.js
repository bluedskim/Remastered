(function () {
  "use strict";

  var DRIFT_SEC = 0.05;
  var SYNC_MS = 250;
  var HAVE_FUTURE_DATA = 3;
  var LOOP_EPSILON = 0.12;
  var REPEAT_STORAGE_KEY = "remastered.abPlayer.repeat";

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

  function initAbPlayer(root) {
    if (root.dataset.abInitialized === "1") return;
    root.dataset.abInitialized = "1";

    var original = root.querySelector('[data-audio="original"]');
    var remastered = root.querySelector('[data-audio="remastered"]');
    if (!original || !remastered) return;

    var playBtn = root.querySelector("[data-ab-play]");
    var repeatBtn = root.querySelector("[data-ab-repeat]");
    var seek = root.querySelector("[data-ab-seek]");
    var vol = root.querySelector("[data-ab-volume]");
    var timeCur = root.querySelector("[data-ab-time-current]");
    var timeDur = root.querySelector("[data-ab-time-duration]");
    var sideButtons = root.querySelectorAll("[data-ab-side]");

    var activeSide = "remastered";
    var userWantsPlay = false;
    var seeking = false;
    var buffering = false;
    var softPausing = false;
    var syncTimer = null;
    var repeatEnabled = true;

    function loadRepeatPreference() {
      try {
        var stored = localStorage.getItem(REPEAT_STORAGE_KEY);
        if (stored === "0") repeatEnabled = false;
        else if (stored === "1") repeatEnabled = true;
      } catch (err) {
        repeatEnabled = true;
      }
    }

    function saveRepeatPreference() {
      try {
        localStorage.setItem(REPEAT_STORAGE_KEY, repeatEnabled ? "1" : "0");
      } catch (err) {
        /* ignore */
      }
    }

    function setRepeatUi() {
      if (!repeatBtn) return;
      repeatBtn.setAttribute("aria-pressed", repeatEnabled ? "true" : "false");
      repeatBtn.classList.toggle("ab-repeat-on", repeatEnabled);
    }

    function toggleRepeat() {
      repeatEnabled = !repeatEnabled;
      saveRepeatPreference();
      setRepeatUi();
    }

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
      sideButtons.forEach(function (btn) {
        btn.classList.toggle(
          "ab-listen-btn--active",
          btn.getAttribute("data-ab-side") === activeSide
        );
      });
    }

    function setPlayIcon(wantsPlay) {
      if (!playBtn) return;
      playBtn.textContent = wantsPlay ? "❚❚" : "▶";
      playBtn.setAttribute("aria-label", wantsPlay ? "Pause" : "Play");
    }

    function setBufferingUi(on) {
      root.classList.toggle("ab-buffering", on);
      if (playBtn) playBtn.setAttribute("aria-busy", on ? "true" : "false");
    }

    function duration() {
      var d = Math.max(original.duration || 0, remastered.duration || 0);
      if (!isFinite(d)) d = 0;
      return d;
    }

    function loopEndTime() {
      var d1 = original.duration;
      var d2 = remastered.duration;
      if (!isFinite(d1) || !isFinite(d2) || d1 <= 0 || d2 <= 0) return 0;
      return Math.min(d1, d2);
    }

    function restartBothFromStart() {
      softPausing = true;
      original.currentTime = 0;
      remastered.currentTime = 0;
      softPausing = false;
      updateTimeUi();
      if (!userWantsPlay) return;
      var p1;
      var p2;
      try {
        p1 = original.play();
        p2 = remastered.play();
      } catch (err) {
        handlePlayRejection(err);
        return;
      }
      Promise.all([p1, p2]).catch(handlePlayRejection);
    }

    function checkLoopRestart() {
      if (!repeatEnabled || !userWantsPlay || seeking) return;
      var end = loopEndTime();
      if (end <= LOOP_EPSILON) return;
      var master = audible();
      if (master.ended || master.currentTime >= end - LOOP_EPSILON) {
        restartBothFromStart();
      }
    }

    function handleTrackEnded() {
      if (repeatEnabled && userWantsPlay) {
        restartBothFromStart();
        return;
      }
      if (original.ended && remastered.ended) {
        userPauseBoth();
        updateTimeUi();
      }
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

    function alignTime(audio, t) {
      if (!isFinite(t)) return;
      if (Math.abs(audio.currentTime - t) > DRIFT_SEC) {
        audio.currentTime = t;
      }
    }

    function alignBothToMaster() {
      var t = audible().currentTime;
      alignTime(original, t);
      alignTime(remastered, t);
    }

    function syncDrift() {
      if (!userWantsPlay) return;
      if (original.seeking || remastered.seeking || seeking) return;
      if (original.readyState < HAVE_FUTURE_DATA || remastered.readyState < HAVE_FUTURE_DATA) {
        return;
      }
      var master = audible();
      var slave = mutedOther();
      if (master.paused && slave.paused && !buffering) return;
      var delta = slave.currentTime - master.currentTime;
      if (Math.abs(delta) > DRIFT_SEC) {
        alignTime(slave, master.currentTime);
      }
    }

    function userPauseBoth() {
      userWantsPlay = false;
      buffering = false;
      softPausing = true;
      original.pause();
      remastered.pause();
      softPausing = false;
      setPlayIcon(false);
      setBufferingUi(false);
    }

    function softPauseOther(waitingAudio) {
      var other = waitingAudio === original ? remastered : original;
      if (other.paused) return;
      softPausing = true;
      other.pause();
      softPausing = false;
    }

    function playPromises() {
      return [original.play(), remastered.play()];
    }

    function handlePlayRejection(err) {
      if (err && err.name === "NotAllowedError") {
        userPauseBoth();
      }
    }

    function startPlaybackFromGesture() {
      userWantsPlay = true;
      alignBothToMaster();
      setPlayIcon(true);
      setBufferingUi(false);
      var p1;
      var p2;
      try {
        p1 = original.play();
        p2 = remastered.play();
      } catch (err) {
        handlePlayRejection(err);
        return;
      }
      Promise.all([p1, p2]).catch(handlePlayRejection);
    }

    function resumeAfterBuffer() {
      if (!userWantsPlay) return;
      alignBothToMaster();
      buffering = false;
      setBufferingUi(false);
      setPlayIcon(true);
      var p1;
      var p2;
      try {
        p1 = original.play();
        p2 = remastered.play();
      } catch (err) {
        handlePlayRejection(err);
        return;
      }
      Promise.all([p1, p2]).catch(handlePlayRejection);
    }

    function onAudioWaiting(audio) {
      if (!userWantsPlay) return;
      buffering = true;
      setBufferingUi(true);
      setPlayIcon(true);
      softPauseOther(audio);
    }

    function onAudioCanPlay() {
      if (!userWantsPlay || !buffering) return;
      resumeAfterBuffer();
    }

    function onAudioPlaying() {
      if (!userWantsPlay) return;
      if (buffering) {
        resumeAfterBuffer();
        return;
      }
      setPlayIcon(true);
      setBufferingUi(false);
    }

    function setActiveSide(side) {
      if (side !== "original" && side !== "remastered") return;
      var prevMaster = audible();
      var t = prevMaster.currentTime;
      activeSide = side;
      applyMute();
      alignTime(original, t);
      alignTime(remastered, t);
      updateTimeUi();
      if (userWantsPlay) {
        var p1;
        var p2;
        try {
          p1 = original.play();
          p2 = remastered.play();
        } catch (err) {
          handlePlayRejection(err);
          return;
        }
        Promise.all([p1, p2]).catch(handlePlayRejection);
      }
    }

    function onSeekInput() {
      if (!seek) return;
      seeking = true;
      var d = duration();
      var ratio = Number(seek.value) / 1000;
      var t = d * ratio;
      alignTime(original, t);
      alignTime(remastered, t);
      updateTimeUi();
    }

    function onSeekCommit() {
      seeking = false;
      if (userWantsPlay) {
        startPlaybackFromGesture();
      }
    }

    loadRepeatPreference();
    setRepeatUi();
    applyMute();

    if (repeatBtn) {
      repeatBtn.addEventListener("click", function () {
        toggleRepeat();
      });
    }

    if (playBtn) {
      playBtn.addEventListener("click", function () {
        if (userWantsPlay) {
          userPauseBoth();
        } else {
          startPlaybackFromGesture();
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
      audio.addEventListener("timeupdate", function () {
        updateTimeUi();
        checkLoopRestart();
      });
      audio.addEventListener("loadedmetadata", updateTimeUi);
      audio.addEventListener("ended", handleTrackEnded);
      audio.addEventListener("waiting", function () {
        onAudioWaiting(audio);
      });
      audio.addEventListener("canplay", onAudioCanPlay);
      audio.addEventListener("playing", function () {
        onAudioPlaying();
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
        if (userWantsPlay) userPauseBoth();
        else startPlaybackFromGesture();
      } else if (e.key === "a" || e.key === "A") {
        setActiveSide("original");
      } else if (e.key === "b" || e.key === "B") {
        setActiveSide("remastered");
      } else if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        toggleRepeat();
      } else if (e.key === "t" || e.key === "T") {
        setActiveSide(activeSide === "original" ? "remastered" : "original");
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
