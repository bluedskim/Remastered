(function () {
  "use strict";

  var POLL_MS = 1200;

  function findStatusEl(button) {
    var host = button.closest(".ab-downloads") || button.parentElement;
    if (!host) return null;
    return host.querySelector("[data-video-download-status]");
  }

  function setStatus(button, message, isError) {
    var el = findStatusEl(button);
    if (!el) return;
    if (!message) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("video-download-status--error");
      return;
    }
    el.hidden = false;
    el.textContent = message;
    el.classList.toggle("video-download-status--error", !!isError);
  }

  function triggerFileDownload(url, filename) {
    var link = document.createElement("a");
    link.href = url;
    if (filename) {
      link.download = filename;
    }
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function pollStatus(jobId, button, filename) {
    return fetch("/api/jobs/" + encodeURIComponent(jobId) + "/video-download", {
      credentials: "same-origin",
    })
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error("Could not check video status.");
        }
        return resp.json();
      })
      .then(function (data) {
        if (data.status === "building") {
          setStatus(button, "Preparing video…", false);
          return new Promise(function (resolve) {
            setTimeout(function () {
              resolve(pollStatus(jobId, button, filename));
            }, POLL_MS);
          });
        }
        if (data.status === "ready" && data.download_url) {
          setStatus(button, "", false);
          triggerFileDownload(
            data.download_url,
            data.download_filename || filename
          );
          button.disabled = false;
          button.textContent = "Download video";
          return;
        }
        if (data.status === "failed") {
          throw new Error(data.error || "Video preparation failed.");
        }
        setStatus(button, "Preparing video…", false);
        return new Promise(function (resolve) {
          setTimeout(function () {
            resolve(pollStatus(jobId, button, filename));
          }, POLL_MS);
        });
      });
  }

  function onClick(button) {
    if (button.disabled) return;
    var jobId = button.getAttribute("data-job-id");
    if (!jobId) return;
    var filename = button.getAttribute("data-download-filename") || "";
    button.disabled = true;
    button.textContent = "Preparing video…";
    setStatus(button, "Preparing video…", false);

    fetch("/api/jobs/" + encodeURIComponent(jobId) + "/video-download", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (resp) {
        return resp.json().then(function (body) {
          if (!resp.ok) {
            var detail = body && body.detail ? body.detail : "Could not start video download.";
            throw new Error(typeof detail === "string" ? detail : "Could not start video download.");
          }
          return body;
        });
      })
      .then(function (data) {
        if (data.status === "ready" && data.download_url) {
          setStatus(button, "", false);
          triggerFileDownload(
            data.download_url,
            data.download_filename || filename
          );
          button.disabled = false;
          button.textContent = "Download video";
          return;
        }
        return pollStatus(jobId, button, filename);
      })
      .catch(function (err) {
        button.disabled = false;
        button.textContent = "Download video";
        setStatus(button, err.message || "Video preparation failed.", true);
      });
  }

  if (!window.__remasteredVideoDownloadClickBound) {
    window.__remasteredVideoDownloadClickBound = true;
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-video-download]");
      if (!button) return;
      onClick(button);
    });
  }
})();
