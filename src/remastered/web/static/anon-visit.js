(function () {
  var STORAGE_KEY = "remastered.anonVisit";
  var COOKIE_NAME = "remastered_anon_visit";
  var MAX_AGE_SECONDS = 60 * 60 * 24 * 400;

  function readVisitId() {
    try {
      var id = sessionStorage.getItem(STORAGE_KEY);
      if (id) return id;
      id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : String(Date.now()) + "-" + Math.random().toString(16).slice(2);
      sessionStorage.setItem(STORAGE_KEY, id);
      return id;
    } catch (err) {
      return "";
    }
  }

  function readCookie() {
    try {
      var parts = document.cookie ? document.cookie.split(";") : [];
      for (var i = 0; i < parts.length; i++) {
        var chunk = parts[i].trim();
        if (chunk.indexOf(COOKIE_NAME + "=") === 0) {
          return decodeURIComponent(chunk.slice(COOKIE_NAME.length + 1));
        }
      }
    } catch (err) {
      /* ignore */
    }
    return "";
  }

  function writeCookie(visitId) {
    if (!visitId) return;
    try {
      var secure = window.location.protocol === "https:" ? "; Secure" : "";
      document.cookie =
        COOKIE_NAME +
        "=" +
        encodeURIComponent(visitId) +
        "; Path=/; Max-Age=" +
        MAX_AGE_SECONDS +
        "; SameSite=Lax" +
        secure;
    } catch (err) {
      /* ignore */
    }
  }

  var visitId = readVisitId();
  var previousCookie = readCookie();
  writeCookie(visitId);
  if (previousCookie && previousCookie !== visitId) {
    try {
      var flag = sessionStorage.getItem(STORAGE_KEY + ".reload");
      if (!flag) {
        sessionStorage.setItem(STORAGE_KEY + ".reload", "1");
        window.location.reload();
        return;
      }
      sessionStorage.removeItem(STORAGE_KEY + ".reload");
    } catch (err) {
      /* ignore */
    }
  }

  document.body.addEventListener("htmx:configRequest", function (evt) {
    if (!visitId || !evt.detail) return;
    evt.detail.headers["X-Remastered-Anon-Visit"] = visitId;
  });
})();
