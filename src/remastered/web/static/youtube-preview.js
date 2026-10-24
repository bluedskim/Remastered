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

    function clearPreview() {
      metaTitle = "";
      if (titleField) titleField.value = "";
      if (preview) preview.setAttribute("hidden", "");
      if (previewError) previewError.setAttribute("hidden", "");
      if (previewImg) previewImg.removeAttribute("src");
      if (previewTitle) previewTitle.textContent = "";
      if (previewAuthor) previewAuthor.textContent = "";
      if (previewDuration) previewDuration.textContent = "";
      onMetaChange(null, false);
    }

    function showError() {
      if (preview) preview.setAttribute("hidden", "");
      if (previewError) previewError.removeAttribute("hidden");
      metaTitle = "";
      if (titleField) titleField.value = "";
      onMetaChange(null, false);
    }

    function showMeta(data, overLimit) {
      metaTitle = data.title || "";
      if (titleField) titleField.value = metaTitle;
      if (previewImg) {
        previewImg.src = data.thumbnail_url || "";
        previewImg.alt = metaTitle ? "Thumbnail for " + metaTitle : "";
      }
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
      if (previewError) previewError.setAttribute("hidden", "");
      if (preview) preview.removeAttribute("hidden");
      onMetaChange(data, overLimit);
    }

    function fetchMeta() {
      var raw = (input.value || "").trim();
      if (!raw || !extractVideoId(raw)) {
        clearPreview();
        return;
      }
      var id = ++requestId;
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

    input.addEventListener("input", function () {
      clearPreview();
      scheduleFetch();
    });

    return {
      scheduleFetch: scheduleFetch,
      clearPreview: clearPreview,
      getMetaTitle: function () {
        return metaTitle;
      },
    };
  }

  window.initYoutubePreview = initYoutubePreview;
  window.extractYoutubeVideoId = extractVideoId;
})();
