(function(){
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var registration = params.get("lms_registration");
  var token = params.get("lms_token");
  var lastError12 = "0";
  var lastError2004 = "0";
  var values = {};
  var initialized12 = false;
  var initialized2004 = false;
  var finished12 = false;
  var finished2004 = false;
  var standard = null;

  function request(method, body) {
    if (!registration || !token) return null;
    try {
      var xhr = new XMLHttpRequest();
      xhr.open(method, "/runtime-api/scorm/registrations/" + encodeURIComponent(registration), false);
      xhr.setRequestHeader("Authorization", "Bearer " + token);
      if (body !== undefined) xhr.setRequestHeader("Content-Type", "application/json");
      xhr.send(body === undefined ? null : JSON.stringify(body));
      if (xhr.status < 200 || xhr.status >= 300) return null;
      return xhr.responseText ? JSON.parse(xhr.responseText) : {};
    } catch (e) {
      return null;
    }
  }

  var initial = request("GET");
  if (initial) {
    standard = initial.standard || null;
    if (initial.cmi) values = initial.cmi;
  }

  function commit12() {
    if (finished12) {
      lastError12 = "101";
      return "false";
    }
    var result = request("PUT", {cmi: values});
    if (!result) {
      lastError12 = "101";
      return "false";
    }
    lastError12 = "0";
    return "true";
  }

  function commit2004() {
    if (finished2004) {
      lastError2004 = "101";
      return "false";
    }
    var result = request("PUT", {cmi: values});
    if (!result) {
      lastError2004 = "101";
      return "false";
    }
    lastError2004 = "0";
    return "true";
  }

  window.API = {
    LMSInitialize: function(){
      if (initialized12) { lastError12 = "101"; return "false"; }
      initialized12 = true;
      lastError12 = "0";
      return "true";
    },
    LMSFinish: function(){
      if (!initialized12) { lastError12 = "301"; return "false"; }
      if (commit12() !== "true") return "false";
      finished12 = true;
      lastError12 = "0";
      return "true";
    },
    LMSGetValue: function(key){
      if (!initialized12) { lastError12 = "301"; return ""; }
      lastError12 = "0";
      var value = values[String(key)];
      return value === undefined || value === null ? "" : String(value);
    },
    LMSSetValue: function(key, value){
      if (!initialized12) { lastError12 = "301"; return "false"; }
      values[String(key)] = value === undefined || value === null ? "" : String(value);
      lastError12 = "0";
      return "true";
    },
    LMSCommit: function(){
      if (!initialized12) { lastError12 = "301"; return "false"; }
      return commit12();
    },
    LMSGetLastError: function(){ return lastError12; },
    LMSGetErrorString: function(code){
      var map = {
        "0":"No error",
        "101":"General exception",
        "201":"Invalid argument error",
        "301":"Not initialized",
        "401":"Not implemented error"
      };
      return map[String(code)] || "SCORM 1.2 error";
    },
    LMSGetDiagnostic: function(code){ return this.LMSGetErrorString(code || lastError12); }
  };

  window.API_1484_11 = {
    Initialize: function(){
      if (initialized2004) { lastError2004 = "103"; return "false"; }
      initialized2004 = true;
      lastError2004 = "0";
      return "true";
    },
    Terminate: function(){
      if (!initialized2004) { lastError2004 = "112"; return "false"; }
      if (commit2004() !== "true") return "false";
      finished2004 = true;
      lastError2004 = "0";
      return "true";
    },
    GetValue: function(key){
      if (!initialized2004) { lastError2004 = "122"; return ""; }
      lastError2004 = "0";
      var value = values[String(key)];
      return value === undefined || value === null ? "" : String(value);
    },
    SetValue: function(key, value){
      if (!initialized2004) { lastError2004 = "132"; return "false"; }
      values[String(key)] = value === undefined || value === null ? "" : String(value);
      lastError2004 = "0";
      return "true";
    },
    Commit: function(){
      if (!initialized2004) { lastError2004 = "142"; return "false"; }
      return commit2004();
    },
    GetLastError: function(){ return lastError2004; },
    GetErrorString: function(code){
      var map = {
        "0":"No error",
        "101":"General exception",
        "102":"General initialization failure",
        "103":"Already initialized",
        "112":"Termination before initialization",
        "122":"Retrieve data before initialization",
        "132":"Store data before initialization",
        "142":"Commit before initialization",
        "401":"Undefined data model element"
      };
      return map[String(code)] || "SCORM 2004 error";
    },
    GetDiagnostic: function(code){ return this.GetErrorString(code || lastError2004); }
  };

  window.__LMS_SCORM_STANDARD__ = standard;

  try {
    params.delete("lms_token");
    var clean = window.location.pathname
      + (params.toString() ? "?" + params.toString() : "")
      + window.location.hash;
    history.replaceState(null, "", clean);
  } catch (e) {}
})();