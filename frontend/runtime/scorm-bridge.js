(function(){
  "use strict";
  var params = new URLSearchParams(window.location.search);
  var registration = params.get("lms_registration");
  var token = params.get("lms_token");
  var lastError = "0";
  var values = {};
  var initialized = false;
  var finished = false;

  function request(method, body) {
    if (!registration || !token) {
      lastError = "101";
      return null;
    }
    try {
      var xhr = new XMLHttpRequest();
      xhr.open(method, "/runtime-api/scorm/registrations/" + encodeURIComponent(registration), false);
      xhr.setRequestHeader("Authorization", "Bearer " + token);
      if (body !== undefined) xhr.setRequestHeader("Content-Type", "application/json");
      xhr.send(body === undefined ? null : JSON.stringify(body));
      if (xhr.status < 200 || xhr.status >= 300) {
        lastError = "101";
        return null;
      }
      return xhr.responseText ? JSON.parse(xhr.responseText) : {};
    } catch (e) {
      lastError = "101";
      return null;
    }
  }

  var initial = request("GET");
  if (initial && initial.cmi) values = initial.cmi;

  function ok() {
    lastError = "0";
    return "true";
  }

  function commit() {
    if (finished) {
      lastError = "101";
      return "false";
    }
    var result = request("PUT", {cmi: values});
    return result ? ok() : "false";
  }

  var API = {
    LMSInitialize: function(){
      if (initialized) { lastError = "101"; return "false"; }
      initialized = true;
      return ok();
    },
    LMSFinish: function(){
      if (!initialized) { lastError = "301"; return "false"; }
      if (!commit()) return "false";
      finished = true;
      return ok();
    },
    LMSGetValue: function(key){
      if (!initialized) { lastError = "301"; return ""; }
      lastError = "0";
      var value = values[key];
      return value === undefined || value === null ? "" : String(value);
    },
    LMSSetValue: function(key, value){
      if (!initialized) { lastError = "301"; return "false"; }
      values[String(key)] = value === undefined || value === null ? "" : String(value);
      return ok();
    },
    LMSCommit: function(){
      if (!initialized) { lastError = "301"; return "false"; }
      return commit();
    },
    LMSGetLastError: function(){ return lastError; },
    LMSGetErrorString: function(code){
      var map = {"0":"No error","101":"General exception","301":"Not initialized"};
      return map[String(code)] || "SCORM error";
    },
    LMSGetDiagnostic: function(code){ return this.LMSGetErrorString(code || lastError); }
  };

  window.API = API;

  // Evita que el token de lanzamiento viaje como Referer a recursos externos.
  try {
    params.delete("lms_token");
    var clean = window.location.pathname + (params.toString() ? "?" + params.toString() : "") + window.location.hash;
    history.replaceState(null, "", clean);
  } catch (e) {}
})();