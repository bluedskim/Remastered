(function () {
  var STORAGE_KEY = "remastered.signupSource";
  var COOKIE_NAME = "remastered_signup_source";
  var CONSUMED_COOKIE = "remastered_signup_ok";
  var MAX_LEN = 64;

  function normalize(raw) {
    if (raw == null || raw === "") return "";
    var value = String(raw).trim().toLowerCase();
    if (!value) return "";
    if (value.length > MAX_LEN) value = value.slice(0, MAX_LEN);
    if (!/^[a-z0-9_-]+$/.test(value)) return "";
    return value;
  }

  function readStored() {
    try {
      var stored = localStorage.getItem(STORAGE_KEY);
      return normalize(stored);
    } catch (err) {
      return "";
    }
  }

  function writeStored(value) {
    if (!value) return;
    try {
      if (readStored()) return;
      localStorage.setItem(STORAGE_KEY, value);
    } catch (err) {
      /* ignore */
    }
  }

  function sourceFromQuery(search) {
    var params = new URLSearchParams(search || window.location.search);
    if (params.has("utm_source")) {
      return normalize(params.get("utm_source"));
    }
    if (params.has("ref")) {
      return normalize(params.get("ref"));
    }
    return "";
  }

  function readCookie(name) {
    try {
      var parts = document.cookie ? document.cookie.split(";") : [];
      for (var i = 0; i < parts.length; i++) {
        var chunk = parts[i].trim();
        if (chunk.indexOf(name + "=") === 0) {
          return decodeURIComponent(chunk.slice(name.length + 1));
        }
      }
    } catch (err) {
      /* ignore */
    }
    return "";
  }

  function clearStored() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      /* ignore */
    }
  }

  if (readCookie(CONSUMED_COOKIE)) {
    clearStored();
  }

  var fromUrl = sourceFromQuery();
  if (fromUrl) {
    writeStored(fromUrl);
  } else {
    var fromCookie = normalize(readCookie(COOKIE_NAME));
    if (fromCookie && !readStored()) {
      writeStored(fromCookie);
    }
  }

  var signupForm = document.getElementById("signup-form");
  if (signupForm) {
    signupForm.addEventListener("submit", function () {
      var src = readStored();
      if (!src) return;
      var existing = signupForm.querySelector('input[name="signup_source"]');
      if (existing) {
        existing.value = src;
        return;
      }
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = "signup_source";
      input.value = src;
      signupForm.appendChild(input);
    });
  }
})();
