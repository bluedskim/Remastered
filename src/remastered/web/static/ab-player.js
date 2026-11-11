(function () {
  "use strict";

  var DRIFT_SEC = 0.05;
  var SYNC_MS = 250;
  var HAVE_FUTURE_DATA = 3;
  var LOOP_EPSILON = 0.12;
  var DURATION_MATCH_EPSILON = 0.35;
  var REPEAT_STORAGE_KEY = "remastered.abPlayer.repeat";
  var LOUDNESS_MATCH_STORAGE_KEY = "remastered.abPlayer.loudnessMatch";

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
    var loudnessMatchBtn = root.querySelector("[data-ab-loudness-match]");
    var loudnessAttenEl = root.querySelector("[data-ab-loudness-attenuation]");
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
    var pendingResumeOnVisible = false;
    var useNativeLoop = false;
    var loudnessMatchEnabled = true;
    var loudnessGainLinear = { original: 1, remastered: 1 };
    var loudnessGainDb = { original: 0, remastered: 0 };
    var loudnessMeasureReady = false;
    var loudnessMeasureToken = 0;
    var loudnessMeasureAttempted = false;

    function loudnessApi() {
      return typeof window !== "undefined" ? window.RemasteredAbLoudnessMatch : null;
    }

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

    function loadLoudnessMatchPreference() {
      try {
        var stored = localStorage.getItem(LOUDNESS_MATCH_STORAGE_KEY);
        if (stored === "0") loudnessMatchEnabled = false;
        else if (stored === "1") loudnessMatchEnabled = true;
      } catch (err) {
        loudnessMatchEnabled = true;
      }
    }

    function saveLoudnessMatchPreference() {
      try {
        localStorage.setItem(LOUDNESS_MATCH_STORAGE_KEY, loudnessMatchEnabled ? "1" : "0");
      } catch (err) {
        /* ignore */
      }
    }

    function formatAttenuationDb(db) {
      if (!isFinite(db) || db >= -0.05) return null;
      return "\u2212" + Math.abs(db).toFixed(1) + " dB";
    }

    function sideDisplayLabel(side) {
      return side === "original" ? "Original" : "Remastered";
    }

    function updateLoudnessAttenuationIndicator() {
      if (!loudnessAttenEl) return;
      if (!loudnessMatchEnabled || !loudnessMeasureReady) {
        loudnessAttenEl.hidden = true;
        loudnessAttenEl.textContent = "";
        return;
      }
      var db = activeSide === "original" ? loudnessGainDb.original : loudnessGainDb.remastered;
      var attenuation = formatAttenuationDb(db);
      if (!attenuation) {
        loudnessAttenEl.hidden = true;
        loudnessAttenEl.textContent = "";
        return;
      }
      loudnessAttenEl.hidden = false;
      loudnessAttenEl.textContent = sideDisplayLabel(activeSide) + " " + attenuation;
    }

    function setLoudnessMatchUi() {
      if (loudnessMatchBtn) {
        loudnessMatchBtn.setAttribute("aria-pressed", loudnessMatchEnabled ? "true" : "false");
        loudnessMatchBtn.classList.toggle("ab-loudness-match-on", loudnessMatchEnabled);
      }
      root.classList.toggle("ab-loudness-match-active", loudnessMatchEnabled);
      updateLoudnessAttenuationIndicator();
    }

    function toggleLoudnessMatch() {
      loudnessMatchEnabled = !loudnessMatchEnabled;
      saveLoudnessMatchPreference();
      setLoudnessMatchUi();
      applyMute();
    }

    function userVolumeLinear() {
      return vol ? Number(vol.value) / 100 : 1;
    }

    function sidePlaybackLinear(side) {
      var base = userVolumeLinear();
      if (!loudnessMatchEnabled) return base;
      if (side === "original") return base * loudnessGainLinear.original;
      if (side === "remastered") return base * loudnessGainLinear.remastered;
      return base;
    }

    function scheduleLoudnessMeasurement() {
      var api = loudnessApi();
      if (!api || typeof api.measureIntegratedLufsFromUrl !== "function") return;
      if (loudnessMeasureAttempted) return;

      var origUrl = original.currentSrc || original.src;
      var remUrl = remastered.currentSrc || remastered.src;
      if (!origUrl || !remUrl) return;

      loudnessMeasureAttempted = true;
      var token = ++loudnessMeasureToken;

      Promise.all([
        api.measureIntegratedLufsFromUrl(origUrl),
        api.measureIntegratedLufsFromUrl(remUrl),
      ])
        .then(function (pair) {
          if (token !== loudnessMeasureToken) return;
          var lOrig = pair[0];
          var lRem = pair[1];
          if (!isFinite(lOrig) || !isFinite(lRem)) return;
          if (typeof api.attenuationGainsDb === "function") {
            loudnessGainDb = api.attenuationGainsDb(lOrig, lRem);
          }
          if (typeof api.attenuationGainsLinear === "function") {
            loudnessGainLinear = api.attenuationGainsLinear(lOrig, lRem);
          }
          loudnessMeasureReady = true;
          applyMute();
        })
        .catch(function () {
          /* playback keeps using unity gain */
        });
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
      applyRepeatEngine();
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
      original.volume = sidePlaybackLinear("original");
      remastered.volume = sidePlaybackLinear("remastered");
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
      updateLoudnessAttenuationIndicator();
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

    function durationsCloseEnough() {
      var d1 = original.duration;
      var d2 = remastered.duration;
      if (!isFinite(d1) || !isFinite(d2) || d1 <= 0 || d2 <= 0) return false;
      return Math.abs(d1 - d2) <= DURATION_MATCH_EPSILON;
    }

    function loopEndTime() {
      var d1 = original.duration;
      var d2 = remastered.duration;
      if (!isFinite(d1) || !isFinite(d2) || d1 <= 0 || d2 <= 0) return 0;
      return Math.min(d1, d2);
    }

    function applyRepeatEngine() {
      useNativeLoop = repeatEnabled && durationsCloseEnough();
      original.loop = useNativeLoop;
      remastered.loop = useNativeLoop;
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
      if (useNativeLoop) return;
      var end = loopEndTime();
      if (end <= LOOP_EPSILON) return;
      var master = audible();
      if (master.ended || master.currentTime >= end - LOOP_EPSILON) {
        restartBothFromStart();
      }
    }

    function handleTrackEnded() {
      if (!repeatEnabled || !userWantsPlay) {
        if (original.ended && remastered.ended) {
          userPauseBoth();
          updateTimeUi();
        }
        return;
      }
      if (useNativeLoop) {
        alignBothToMaster();
        return;
      }
      restartBothFromStart();
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
      pendingResumeOnVisible = false;
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

    function handlePlayRejection(err) {
      if (err && err.name === "NotAllowedError") {
        if (userWantsPlay && (document.hidden || document.visibilityState === "hidden")) {
          pendingResumeOnVisible = true;
          return;
        }
        userPauseBoth();
      }
    }

    function playBothAsync() {
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

    function tryResumeAfterVisible() {
      if (!userWantsPlay) return;
      applyRepeatEngine();
      var end = loopEndTime();
      var master = audible();
      if (
        repeatEnabled &&
        end > LOOP_EPSILON &&
        (master.ended || master.currentTime >= end - LOOP_EPSILON)
      ) {
        restartBothFromStart();
        return;
      }
      if (original.paused || remastered.paused) {
        alignBothToMaster();
        setPlayIcon(true);
        playBothAsync();
      }
      pendingResumeOnVisible = false;
    }

    root._abOnVisible = function () {
      if (document.visibilityState !== "visible") return;
      if (pendingResumeOnVisible || (userWantsPlay && (original.paused || remastered.paused))) {
        tryResumeAfterVisible();
      }
    };

    function startPlaybackFromGesture() {
      userWantsPlay = true;
      pendingResumeOnVisible = false;
      alignBothToMaster();
      setPlayIcon(true);
      setBufferingUi(false);
      playBothAsync();
    }

    function resumeAfterBuffer() {
      if (!userWantsPlay) return;
      alignBothToMaster();
      buffering = false;
      setBufferingUi(false);
      setPlayIcon(true);
      playBothAsync();
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
        playBothAsync();
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

    function onMetadataLoaded() {
      applyRepeatEngine();
      updateTimeUi();
      scheduleLoudnessMeasurement();
    }

    loadRepeatPreference();
    loadLoudnessMatchPreference();
    setRepeatUi();
    setLoudnessMatchUi();
    applyMute();
    updateLoudnessAttenuationIndicator();
    applyRepeatEngine();
    bindVisibilityResume();
    scheduleLoudnessMeasurement();

    if (repeatBtn) {
      repeatBtn.addEventListener("click", function () {
        toggleRepeat();
      });
    }

    if (loudnessMatchBtn) {
      loudnessMatchBtn.addEventListener("click", function () {
        toggleLoudnessMatch();
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
      audio.addEventListener("loadedmetadata", onMetadataLoaded);
      audio.addEventListener("ended", handleTrackEnded);
      audio.addEventListener("waiting", function () {
        onAudioWaiting(audio);
      });
      audio.addEventListener("canplay", onAudioCanPlay);
      audio.addEventListener("playing", function () {
        onAudioPlaying();
      });
      audio.addEventListener("seeked", function () {
        if (useNativeLoop && userWantsPlay) {
          alignBothToMaster();
        }
      });
    });

    syncTimer = window.setInterval(function () {
      syncDrift();
      updateTimeUi();
    }, SYNC_MS);

    root.addEventListener("ab-player-destroy", function () {
      if (syncTimer) window.clearInterval(syncTimer);
      root._abOnVisible = null;
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

  function bindVisibilityResume() {
    if (window.__abPlayerVisibilityBound) return;
    window.__abPlayerVisibilityBound = true;
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      document.querySelectorAll("[data-ab-compare][data-ab-initialized='1']").forEach(function (root) {
        if (typeof root._abOnVisible === "function") root._abOnVisible();
      });
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
