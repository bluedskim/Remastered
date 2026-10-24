(function () {
  "use strict";

  function wireThumbSlot(slot) {
    if (slot.dataset.ytThumbWired === "1") return;
    slot.dataset.ytThumbWired = "1";
    var placeholder = slot.querySelector(".yt-thumb-placeholder");
    var img = slot.querySelector("img.yt-thumb-img");
    if (!img) return;

    function showPlaceholder() {
      if (placeholder) placeholder.removeAttribute("hidden");
      img.hidden = true;
    }

    function showImage() {
      if (placeholder) placeholder.setAttribute("hidden", "");
      img.hidden = false;
    }

    img.addEventListener("load", function () {
      if (img.naturalWidth > 0) showImage();
      else showPlaceholder();
    });
    img.addEventListener("error", function () {
      showPlaceholder();
      img.removeAttribute("src");
    });

    if (!img.getAttribute("src")) {
      showPlaceholder();
      return;
    }
    if (img.complete) {
      if (img.naturalWidth > 0) showImage();
      else showPlaceholder();
    } else {
      showPlaceholder();
    }
  }

  function scan() {
    document.querySelectorAll("[data-yt-thumb-slot]").forEach(wireThumbSlot);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  document.body.addEventListener("htmx:afterSwap", scan);
})();
