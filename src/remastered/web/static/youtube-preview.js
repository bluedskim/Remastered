(function () {
  "use strict";

  function extractVideoId(text) {
    var raw = (text || "").trim();
    if (!raw) return "";
    try {
      var u = new URL(raw);
      var host = u.hostname.replace(/^www\./, "");
      if (host === "youtu.be") return (u.pathname.split("/").filter(Boolean)[0] || "").slice(0, 11);
      if (host.indexOf("youtube.com") !== -1) {
        if (u.pathname.indexOf("/shorts/") === 0) {
          return u.pathname.split("/")[2] || "";
        }
        return u.searchParams.get("v") || "";
      }
    } catch (err) {
      return "";
    }
    return "";
  }

  function initYoutubePreview(options) {
    var input = document.getElementById(options.inputId);
    if (!input) return null;

    var preview = document.getElementById(options.previewId);
    var previewImg = document.getElementById(options.previewImgId);
    var previewSkeleton = document.getElementById(
      options.previewSkeletonId ||
        String(options.previewImgId || "").replace(/-img$/, "-skeleton")
    );
    var previewTitle = document.getElementById(options.previewTitleId);
    var previewAuthor = document.getElementById(options.previewAuthorId);
    var previewDuration = document.getElementById(options.previewDurationId);
    var previewError = document.getElementById(options.previewErrorId);
    var titleField = options.titleFieldId
      ? document.getElementById(options.titleFieldId)
      : null;
    var maxDuration = options.maxDurationSeconds || 0;
    var onMetaChange = options.onMetaChange || function () {};
    var metaTitle = "";
    var timer = null;
    var requestId = 0;

    function hideImg() {
      if (!previewImg) return;
      previewImg.hidden = true;
      previewImg.removeAttribute("src");
      previewImg.alt = "";
    }

    function hideSkeleton() {
      if (previewSkeleton) previewSkeleton.setAttribute("hidden", "");
    }

    function showSkeleton() {
      if (previewSkeleton) previewSkeleton.removeAttribute("hidden");
      hideImg();
    }

    function clearPreview() {
      metaTitle = "";
      if (titleField) titleField.value = "";
      if (preview) preview.setAttribute("hidden", "");
      if (previewError) previewError.setAttribute("hidden", "");
      hideSkeleton();
      hideImg();
      if (previewTitle) previewTitle.textContent = "";
      if (previewAuthor) {
        previewAuthor.textContent = "";
        previewAuthor.hidden = true;
      }
      if (previewDuration) previewDuration.textContent = "";
      onMetaChange(null, false);
    }

    function showError() {
      if (preview) preview.setAttribute("hidden", "");
      if (previewError) previewError.removeAttribute("hidden");
      hideSkeleton();
      hideImg();
      metaTitle = "";
      if (titleField) titleField.value = "";
      onMetaChange(null, false);
    }

    function fillTextMeta(data) {
      metaTitle = data.title || "";
      if (titleField) titleField.value = metaTitle;
      if (previewTitle) previewTitle.textContent = data.title || "";
      if (previewAuthor) {
        previewAuthor.textContent = data.author_name || "";
        previewAuthor.hidden = !data.author_name;
      }
      if (previewDuration) {
        previewDuration.textContent = data.duration_label
          ? "Duration: " + data.duration_label
          : "";
      }
    }

    function showLoading() {
      if (previewError) previewError.setAttribute("hidden", "");
      if (preview) preview.removeAttribute("hidden");
      if (previewTitle) previewTitle.textContent = "";
      if (previewAuthor) {
        previewAuthor.textContent = "";
        previewAuthor.hidden = true;
      }
      if (previewDuration) previewDuration.textContent = "";
      showSkeleton();
    }

    function showMeta(data, overLimit) {
      fillTextMeta(data);
      if (previewError) previewError.setAttribute("hidden", "");
      if (preview) preview.removeAttribute("hidden");
      hideSkeleton();

      var thumb = (data.thumbnail_url || "").trim();
      if (!thumb || !previewImg) {
        hideImg();
        onMetaChange(data, overLimit);
        return;
      }

      previewImg.alt = metaTitle ? "Thumbnail for " + metaTitle : "";

      function revealThumbIfReady() {
        if (previewImg.naturalWidth > 0) {
          previewImg.hidden = false;
        }
      }

      previewImg.onerror = function () {
        hideImg();
      };
      previewImg.onload = function () {
        revealThumbIfReady();
      };

      var currentSrc = previewImg.getAttribute("src") || "";
      if (currentSrc === thumb && previewImg.complete && previewImg.naturalWidth > 0) {
        previewImg.hidden = false;
        onMetaChange(data, overLimit);
        return;
      }

      previewImg.hidden = true;
      if (currentSrc === thumb) {
        previewImg.removeAttribute("src");
      }
      previewImg.src = thumb;
      revealThumbIfReady();
      if (previewImg.hidden) {
        setTimeout(revealThumbIfReady, 0);
        requestAnimationFrame(revealThumbIfReady);
      }
      onMetaChange(data, overLimit);
    }

    function fetchMeta() {
      var raw = (input.value || "").trim();
      if (!raw || !extractVideoId(raw)) {
        clearPreview();
        return;
      }
      var id = ++requestId;
      showLoading();
      fetch("/api/youtube/meta?url=" + encodeURIComponent(raw))
        .then(function (res) {
          if (id !== requestId) return null;
          if (!res.ok) throw new Error("meta failed");
          return res.json();
        })
        .then(function (data) {
          if (!data || id !== requestId) return;
          var overLimit =
            maxDuration > 0 &&
            data.duration_seconds != null &&
            data.duration_seconds > maxDuration;
          showMeta(data, overLimit);
        })
        .catch(function () {
          if (id !== requestId) return;
          showError();
        });
    }

    function scheduleFetch() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(fetchMeta, 400);
    }

    function refreshFromInput() {
      if (timer) clearTimeout(timer);
      timer = null;
      fetchMeta();
    }

    if (previewImg) {
      previewImg.hidden = true;
      previewImg.removeAttribute("src");
    }
    hideSkeleton();

    input.addEventListener("input", function () {
      clearPreview();
      scheduleFetch();
    });

    return {
      scheduleFetch: scheduleFetch,
      refreshFromInput: refreshFromInput,
      clearPreview: clearPreview,
      getMetaTitle: function () {
        return metaTitle;
      },
    };
  }

  function initYoutubeUrlClear(options) {
    var input = document.getElementById(options.inputId);
    var btn = document.getElementById(options.clearButtonId);
    var onClear = options.onClear || function () {};
    if (!input || !btn) return;

    function syncButton() {
      var hasValue = !!(input.value || "").trim();
      if (hasValue) {
        btn.removeAttribute("hidden");
      } else {
        btn.setAttribute("hidden", "");
      }
      btn.disabled = !!input.disabled;
    }

    function clearField() {
      if (input.disabled) return;
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      onClear();
      syncButton();
      input.focus();
    }

    btn.addEventListener("click", function () {
      clearField();
    });
    input.addEventListener("input", syncButton);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        clearField();
      }
    });
    syncButton();
  }

  function syncYoutubeUrlClear(inputId, clearButtonId) {
    var input = document.getElementById(inputId);
    var btn = document.getElementById(clearButtonId);
    if (!input || !btn) return;
    var hasValue = !!(input.value || "").trim();
    if (hasValue && !input.disabled) {
      btn.removeAttribute("hidden");
    } else {
      btn.setAttribute("hidden", "");
    }
    btn.disabled = !!input.disabled;
  }

  window.initYoutubePreview = initYoutubePreview;
  window.initYoutubeUrlClear = initYoutubeUrlClear;
  window.syncYoutubeUrlClear = syncYoutubeUrlClear;
  window.extractYoutubeVideoId = extractVideoId;
})();
