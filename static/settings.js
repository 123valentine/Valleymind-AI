// ── Settings System ──────────────────────────────────────────

var SETTINGS_SECTIONS = [
  { id: "account",       label: "Account" },
  { id: "memory",        label: "Memory" },
  { id: "projects",      label: "Projects" },
  { id: "creator",       label: "Creator Profile" },
  { id: "preferences",   label: "AI Preferences" },
  { id: "appearance",    label: "Appearance" },
  { id: "notifications", label: "Notifications" },
  { id: "knowledge",     label: "Knowledge Base" },
  { id: "media",         label: "Media Library" },
  { id: "storage",       label: "Storage" },
  { id: "billing",       label: "Billing" },
  { id: "privacy",       label: "Privacy & Security" },
  { id: "language",      label: "Language & Region" },
  { id: "integrations",  label: "Integrations" },
  { id: "extensions",    label: "Extensions" },
  { id: "usage",         label: "Usage" },
];

function buildSettingsNav() {
  var nav = document.getElementById("settingsNav");
  nav.innerHTML = SETTINGS_SECTIONS.map(function (s) {
    return '<div class="settings-nav-item" data-section="' + s.id + '" onclick="switchSettingsSection(\'' + s.id + '\')" style="display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;color:#64748b;font-size:13px;font-family:\'Inter\',sans-serif;transition:all 0.15s;border-left:3px solid transparent;" onmouseover="if(!this.classList.contains(\'active\')){this.style.background=\'rgba(255,255,255,0.03)\';this.style.color=\'#e2e8f0\'}" onmouseout="if(!this.classList.contains(\'active\')){this.style.background=\'transparent\';this.style.color=\'#64748b\'}">' +
      '<span>' + s.label + '</span>' +
      '</div>';
  }).join("");
  nav.innerHTML += '<div onclick="openDeveloperMode();closeSettings()" style="display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;color:rgba(100,116,139,0.4);font-size:11px;font-family:\'Inter\',sans-serif;margin-top:12px;transition:color 0.2s;border-left:3px solid transparent;" onmouseover="this.style.color=\'rgba(100,116,139,0.7)\'" onmouseout="this.style.color=\'rgba(100,116,139,0.4)\'"><span>Advanced</span></div>';
}

var _settingsCurrentSection = "account";

function switchSettingsSection(id) {
  _settingsCurrentSection = id;
  document.querySelectorAll(".settings-nav-item").forEach(function (el) {
    el.classList.remove("active");
    el.style.background = "transparent";
    el.style.color = "#64748b";
    el.style.borderLeftColor = "transparent";
  });
  var active = document.querySelector('.settings-nav-item[data-section="' + id + '"]');
  if (active) {
    active.classList.add("active");
    active.style.background = "rgba(0,212,255,0.08)";
    active.style.color = "#00d4ff";
    active.style.borderLeftColor = "#00d4ff";
  }
  renderSettingsContent(id);
}

function renderSettingsContent(id) {
  var container = document.getElementById("settingsContent");
  container.innerHTML = "";
  switch (id) {
    case "account":       renderAccountSection(container);       break;
    case "memory":        renderMemorySection(container);        break;
    case "projects":      renderProjectsSection(container);      break;
    case "creator":       renderCreatorSection(container);       break;
    case "preferences":   renderPreferencesSection(container);   break;
    case "appearance":    renderAppearanceSection(container);    break;
    case "notifications": renderNotificationsSection(container); break;
    case "knowledge":     renderKnowledgeSection(container);     break;
    case "media":         renderMediaSection(container);         break;
    case "storage":       renderStorageSection(container);       break;
    case "billing":       renderBillingSection(container);       break;
    case "privacy":       renderPrivacySection(container);       break;
    case "language":      renderLanguageSection(container);      break;
    case "integrations":  renderIntegrationsSection(container);  break;
    case "extensions":    renderExtensionsSection(container);    break;
    case "usage":         renderUsageSection(container);         break;
  }
}

function _showSaved() {
  var el = document.getElementById("settingsSaveIndicator");
  if (el) { el.style.opacity = "1"; setTimeout(function () { el.style.opacity = "0"; }, 2000); }
}

function settingsApiSave(section, data) {
  return apiFetch("/api/settings/" + section, {
    method: "PUT",
    credentials: "include",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(data)
  }).then(function (r) { return r.json(); });
}

function settingsApiGet(section) {
  return apiFetch("/api/settings/" + section, {
    credentials: "include",
    headers: authHeaders()
  }).then(function (r) { return r.json(); });
}

var _SH = {
  sectionHeader: function (title, subtitle) {
    return '<div style="margin-bottom:24px;"><h2 style="color:#f1f5f9;font-size:18px;font-weight:700;font-family:\'Inter\',sans-serif;margin:0 0 4px;">' + title + '</h2>' +
      (subtitle ? '<p style="color:#64748b;font-size:12px;margin:0;font-family:\'Inter\',sans-serif;">' + subtitle + '</p>' : '') + '</div>';
  },
  card: function (title, html) {
    return '<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);border-radius:12px;padding:20px;margin-bottom:16px;transition:border-color 0.2s;" onmouseover="this.style.borderColor=\'rgba(255,255,255,0.1)\'" onmouseout="this.style.borderColor=\'rgba(255,255,255,0.06)\'">' +
      (title ? '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;"><span style="color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:0.15em;font-family:\'Space Grotesk\',sans-serif;font-weight:600;">' + title + '</span></div>' : '') +
      html +
      '</div>';
  },
  input: function (placeholder, id, type, value, opts) {
    opts = opts || {};
    var extraStyle = opts.style || "";
    return '<input id="' + id + '" type="' + (type || "text") + '" placeholder="' + placeholder + '" value="' + (value || "") + '" autocomplete="off" style="width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:10px 12px;color:#e2e8f0;font-size:13px;outline:none;font-family:\'Inter\',sans-serif;box-sizing:border-box;transition:border-color 0.2s;' + extraStyle + '" onfocus="this.style.borderColor=\'rgba(0,212,255,0.4)\'" onblur="this.style.borderColor=\'rgba(255,255,255,0.08)\'">';
  },
  textarea: function (placeholder, id, value) {
    return '<textarea id="' + id + '" placeholder="' + placeholder + '" style="width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:10px 12px;color:#e2e8f0;font-size:13px;outline:none;font-family:\'Inter\',sans-serif;box-sizing:border-box;min-height:72px;resize:vertical;transition:border-color 0.2s;" onfocus="this.style.borderColor=\'rgba(0,212,255,0.4)\'" onblur="this.style.borderColor=\'rgba(255,255,255,0.08)\'">' + (value || "") + '</textarea>';
  },
  btn: function (text, onclick, color, opts) {
    opts = opts || {};
    var extra = opts.style || "";
    var bg = color || "rgba(0,212,255,0.12)";
    var txtColor = color ? "#fff" : "#00d4ff";
    return '<button onclick="' + onclick + '" style="border:none;border-radius:8px;background:' + bg + ';color:' + txtColor + ';font-weight:600;padding:9px 16px;cursor:pointer;font-size:12px;font-family:\'Inter\',sans-serif;transition:all 0.2s;' + extra + '" onmouseover="this.style.opacity=\'0.85\'" onmouseout="this.style.opacity=\'1\'">' + text + '</button>';
  },
  select: function (options, id, selected) {
    var opts = options.map(function (o) {
      var val = typeof o === "object" ? o.value : o;
      var label = typeof o === "object" ? o.label : o;
      var sel = val === selected ? " selected" : "";
      return '<option value="' + val + '"' + sel + '>' + label + '</option>';
    }).join("");
    return '<select id="' + id + '" style="width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:10px 12px;color:#e2e8f0;font-size:13px;outline:none;font-family:\'Inter\',sans-serif;cursor:pointer;">' + opts + '</select>';
  },
  toggle: function (id, checked, onChange) {
    var cb = onChange ? ' onchange="' + onChange + '"' : ' onchange="this.parentNode.querySelector(\'.slider\').style.background=this.checked?\'rgba(0,212,255,0.7)\':\'rgba(255,255,255,0.1)\'"';
    return '<label style="position:relative;display:inline-block;width:36px;height:20px;cursor:pointer;flex-shrink:0;"><input id="' + id + '" type="checkbox" ' + (checked ? "checked" : "") + ' style="opacity:0;width:0;height:0;"' + cb + '><span class="slider" style="position:absolute;inset:0;background:' + (checked ? "rgba(0,212,255,0.7)" : "rgba(255,255,255,0.1)") + ';border-radius:10px;transition:0.2s;"></span><span style="position:absolute;top:2px;left:' + (checked ? "18px" : "2px") + ';width:16px;height:16px;border-radius:50%;background:#fff;transition:0.2s;"></span></label>';
  },
  row: function (label, control, desc) {
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><div style="flex:1;"><span style="color:#e2e8f0;font-size:13px;font-family:\'Inter\',sans-serif;">' + label + '</span>' + (desc ? '<p style="color:#64748b;font-size:11px;margin:2px 0 0;font-family:\'Inter\',sans-serif;">' + desc + '</p>' : '') + '</div>' + control + '</div>';
  },
  badge: function (label, color) {
    var c = color || "#00d4ff";
    return '<span style="display:inline-block;background:rgba(' + (c === "#00d4ff" ? "0,212,255" : "100,116,139") + ',0.1);color:' + c + ';border:1px solid rgba(' + (c === "#00d4ff" ? "0,212,255" : "100,116,139") + ',0.2);border-radius:6px;padding:3px 8px;font-size:10px;font-family:\'Inter\',sans-serif;">' + label + '</span>';
  },
  statusSpan: function (id) {
    return '<span style="margin-left:8px;font-size:11px;color:#94a3b8;font-family:\'Inter\',sans-serif;" id="' + id + '"></span>';
  },
  colorPicker: function (id, value) {
    return '<input id="' + id + '" type="color" value="' + (value || "#00d4ff") + '" style="width:36px;height:36px;border:none;border-radius:6px;cursor:pointer;background:transparent;padding:0;">';
  },
};

function _getVal(id) { var el = document.getElementById(id); return el ? el.value : ""; }
function _getChecked(id) { var el = document.getElementById(id); return el ? el.checked : false; }
function _pluralize(n, s) { return n + " " + s + (n !== 1 ? "s" : ""); }

function _saveSettingsAndShow(section, data, statusId) {
  settingsApiSave(section, data).then(function (res) {
    _showSaved();
    var el = statusId ? document.getElementById(statusId) : null;
    if (el) { el.textContent = res.status === "success" ? "Saved!" : "Error saving"; setTimeout(function () { el.textContent = ""; }, 2500); }
  }).catch(function () {
    var el = statusId ? document.getElementById(statusId) : null;
    if (el) { el.textContent = "Connection error"; setTimeout(function () { el.textContent = ""; }, 2500); }
  });
}

// ══════════════════════════════════════════════════════════════
// ACCOUNT
// ══════════════════════════════════════════════════════════════

function renderAccountSection(container) {
  container.innerHTML = _SH.sectionHeader("Account", "Manage your ValleyMind identity and security") + '<p style="color:#64748b;font-size:12px;">Loading profile...</p>';
  apiFetch("/api/settings/profile", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }).then(function (d) {
    if (d.status !== "success") return;
    var p = d.profile || {};
    var email = p.email || "user@valleymind.ai";
    var name = p.username || email.split("@")[0] || "User";
    container.innerHTML =
      _SH.sectionHeader("Account", "Manage your ValleyMind identity and security") +
      _SH.card("Profile", '<div style="display:flex;align-items:center;gap:16px;">' +
        '<div style="width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#22d3ee,#0ea5e9);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:22px;font-family:\'Space Grotesk\',sans-serif;flex-shrink:0;">' + (email[0] || "U").toUpperCase() + '</div>' +
        '<div style="flex:1;"><p style="color:#f1f5f9;font-size:16px;font-weight:600;margin:0;font-family:\'Inter\',sans-serif;">' + name + '</p>' +
        '<p style="color:#64748b;font-size:12px;margin:2px 0 0;font-family:\'Inter\',sans-serif;">' + email + '</p></div></div>') +
      _SH.card("Username", _SH.input("Choose a display name", "settingsUsername", "text", name) + '<div style="margin-top:8px;">' + _SH.btn("Update Profile", "updateProfile()", "rgba(14,165,233,0.8)") + _SH.statusSpan("settingsProfileStatus") + '</div>') +
      _SH.card("Email", '<p style="color:#64748b;font-size:12px;margin:0 0 8px;font-family:\'Inter\',sans-serif;">' + email + '</p><p style="color:#475569;font-size:11px;margin:0;font-family:\'Inter\',sans-serif;">Email cannot be changed at this time.</p>') +
      _SH.card("Password", _SH.input("Current password", "settingsOldPass", "password") + '<div style="height:8px;"></div>' + _SH.input("New password", "settingsNewPass", "password") + '<div style="height:10px;"></div>' + _SH.btn("Update Password", "settingsChangePassword()", "rgba(14,165,233,0.8)") + _SH.statusSpan("settingsPassStatus")) +
      _SH.card("Two-Factor Authentication", '<div style="display:flex;align-items:center;justify-content:space-between;"><div><span style="color:#e2e8f0;font-size:13px;font-family:\'Inter\',sans-serif;">Two-Factor Authentication</span><p style="color:#64748b;font-size:11px;margin:2px 0 0;font-family:\'Inter\',sans-serif;">Add an extra layer of security</p></div>' + _SH.toggle("settings2FA", false) + '</div>') +
      _SH.card("Connected Accounts", '<div style="display:flex;align-items:center;gap:12px;padding:8px 12px;background:rgba(15,23,42,0.5);border-radius:8px;"><div style="width:32px;height:32px;border-radius:50%;background:#4285F4;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;flex-shrink:0;">G</div><div style="flex:1;"><span style="color:#e2e8f0;font-size:13px;font-family:\'Inter\',sans-serif;">Google</span><p style="color:#64748b;font-size:10px;margin:1px 0 0;font-family:\'Inter\',sans-serif;">Connected</p></div><span style="color:#22c55e;font-size:10px;font-family:\'Inter\',sans-serif;">✓ Linked</span></div>') +
      _SH.card("Active Sessions", '<p style="color:#64748b;font-size:12px;margin:0;font-family:\'Inter\',sans-serif;">1 active session (current device)</p>') +
      _SH.card("Login History", '<div style="color:#64748b;font-size:12px;font-family:\'Inter\',sans-serif;"><div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span>Current session</span><span style="color:#475569;">Just now</span></div><p style="margin-top:8px;color:#475569;font-size:11px;">Full login history will appear here as you use ValleyMind.</p></div>') +
      _SH.card("Devices", '<div style="display:flex;align-items:center;gap:12px;padding:8px 12px;background:rgba(15,23,42,0.5);border-radius:8px;"><div style="flex:1;"><span style="color:#e2e8f0;font-size:13px;font-family:\'Inter\',sans-serif;">Current Device</span><p style="color:#64748b;font-size:10px;margin:1px 0 0;font-family:\'Inter\',sans-serif;">Active now</p></div><span style="color:#22c55e;font-size:10px;font-family:\'Inter\',sans-serif;">Current</span></div>');
  }).catch(function () {
    container.innerHTML = _SH.sectionHeader("Account") + '<p style="color:#64748b;">Could not load profile.</p>';
  });
}

function settingsChangePassword() {
  var oldPass = _getVal("settingsOldPass");
  var newPass = _getVal("settingsNewPass");
  var status = document.getElementById("settingsPassStatus");
  if (!oldPass || !newPass) { if (status) status.textContent = "Fill in both fields."; return; }
  if (newPass.length < 4) { if (status) status.textContent = "Password must be 4+ characters."; return; }
  apiFetch("/auth/change-password", {
    method: "POST", credentials: "include",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ current_password: oldPass, new_password: newPass })
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (status) status.textContent = d.status === "success" ? "Password updated!" : d.message || "Failed.";
    if (d.status === "success") { document.getElementById("settingsOldPass").value = ""; document.getElementById("settingsNewPass").value = ""; }
  }).catch(function () { if (status) status.textContent = "Connection error."; });
}

function updateProfile() {
  var username = _getVal("settingsUsername");
  apiFetch("/api/settings/profile", {
    method: "PUT", credentials: "include",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ username: username })
  }).then(function (r) { return r.json(); }).then(function (d) {
    var el = document.getElementById("settingsProfileStatus");
    if (el) el.textContent = d.status === "success" ? "Profile updated!" : "Error";
    if (d.status === "success") {
      _showSaved();
      var sidebarName = document.getElementById("sidebarUserName");
      if (sidebarName) sidebarName.textContent = username || sidebarName.textContent;
    }
    setTimeout(function () { if (el) el.textContent = ""; }, 2500);
  }).catch(function () { var el = document.getElementById("settingsProfileStatus"); if (el) el.textContent = "Connection error"; });
}

// ══════════════════════════════════════════════════════════════
// MEMORY
// ══════════════════════════════════════════════════════════════

var MEMORY_FIELDS = [
  { key: "about_me", label: "About Me" },
  { key: "my_goals", label: "My Goals" },
  { key: "current_projects", label: "Current Projects" },
  { key: "long_term_vision", label: "Long-Term Vision" },
  { key: "skills", label: "Skills" },
  { key: "interests", label: "Interests" },
  { key: "preferred_communication_style", label: "Preferred Communication Style" },
  { key: "always_remember", label: "Always Remember" },
  { key: "never_remember", label: "Never Remember" },
];

function renderMemorySection(container) {
  container.innerHTML = _SH.sectionHeader("Memory", "Tell ValleyMind about yourself") + '<p style="color:#64748b;">Loading...</p>';
  apiFetch("/api/settings/memory-fields", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var data = d.fields || {};
      var fieldsHtml = MEMORY_FIELDS.map(function (f) {
        var val = data[f.key] || "";
        return '<div style="margin-bottom:14px;"><p style="color:#94a3b8;font-size:11px;margin:0 0 6px;font-family:\'Inter\',sans-serif;">' + f.label + '</p>' +
          _SH.textarea("Tell ValleyMind about " + f.label.toLowerCase() + "...", "mem_" + f.key, val) + '</div>';
      }).join("");
      container.innerHTML = _SH.sectionHeader("Memory", "Tell ValleyMind about yourself — every update is saved to long-term memory") +
        _SH.card("Your Personal Context", fieldsHtml + '<div style="margin-top:8px;">' + _SH.btn("Save All Memory Fields", "saveAllMemoryFields()", "rgba(0,212,255,0.8)") + _SH.statusSpan("memSaveStatus") + '</div>') +
        _SH.card("Memory Tools", '<div style="display:flex;flex-wrap:wrap;gap:8px;">' +
          _SH.btn("Memory Timeline", "showMemoryTimeline()") +
          _SH.btn("Memory Review", "showMemoryReview()") +
          _SH.btn("Import Memory", "showComingSoon('Memory Import')") +
          _SH.btn("Export Memory", "exportMemory()") +
          '</div><div id="memoryTimelineContainer" style="margin-top:12px;"></div>');
    })
    .catch(function () { container.innerHTML = _SH.sectionHeader("Memory") + '<p style="color:#64748b;">Could not load memory fields.</p>'; });
}

function saveAllMemoryFields() {
  var data = {};
  MEMORY_FIELDS.forEach(function (f) { var val = _getVal("mem_" + f.key); if (val) data[f.key] = val; });
  apiFetch("/api/settings/memory-fields", { method: "PUT", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(data) })
    .then(function (r) { return r.json(); }).then(function (d) {
      _showSaved();
      var el = document.getElementById("memSaveStatus");
      if (el) { el.textContent = d.status === "success" ? "Memory updated!" : "Error"; setTimeout(function () { el.textContent = ""; }, 3000); }
    }).catch(function () { var el = document.getElementById("memSaveStatus"); if (el) { el.textContent = "Connection error"; setTimeout(function () { el.textContent = ""; }, 2500); } });
}

function showMemoryTimeline() {
  var container = document.getElementById("memoryTimelineContainer");
  if (!container) return;
  container.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;"><p style="color:#94a3b8;font-size:11px;">Loading timeline...</p></div>';
  apiFetch("/api/settings/memory-timeline", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var entries = d.entries || [];
      if (entries.length === 0) { container.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;"><p style="color:#64748b;font-size:11px;">No memory entries yet.</p></div>'; return; }
      var html = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;max-height:200px;overflow-y:auto;">';
      entries.forEach(function (e) { html += '<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:#00d4ff;font-size:10px;min-width:60px;">' + e.type + '</span><span style="color:#e2e8f0;font-size:11px;"><strong>' + e.key + '</strong>: ' + e.value + '</span></div>'; });
      html += '</div>';
      container.innerHTML = html;
    }).catch(function () { container.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;"><p style="color:#ef4444;font-size:11px;">Failed to load.</p></div>'; });
}

function showMemoryReview() {
  var container = document.getElementById("memoryTimelineContainer");
  if (!container) return;
  apiFetch("/api/settings/memory-fields", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var data = d.fields || {};
      var count = Object.keys(data).filter(function (k) { return data[k]; }).length;
      container.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;"><p style="color:#94a3b8;font-size:11px;">Memory Review</p><p style="color:#e2e8f0;font-size:13px;">You have ' + _pluralize(count, "memory field") + ' saved.</p><p style="color:#64748b;font-size:11px;">Open the Memory section to review each field.</p></div>';
    }).catch(function () { container.innerHTML = '<div style="padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-top:8px;"><p style="color:#ef4444;font-size:11px;">Failed to load.</p></div>'; });
}

function exportMemory() {
  apiFetch("/api/settings/memory-fields", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var blob = new Blob([JSON.stringify(d.fields || {}, null, 2)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a"); a.href = url; a.download = "valleymind-memory.json"; a.click();
      URL.revokeObjectURL(url);
    }).catch(function () { alert("Could not export memory."); });
}

// ══════════════════════════════════════════════════════════════
// PROJECTS
// ══════════════════════════════════════════════════════════════

function renderProjectsSection(container) {
  container.innerHTML = _SH.sectionHeader("Projects", "Save projects so ValleyMind has context about your work");
  apiFetch("/api/settings/projects", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) { renderProjectList(container, d.projects || []); })
    .catch(function () { renderProjectList(container, []); });
}

function renderProjectList(container, projects) {
  var listHtml = projects.length > 0 ? projects.map(function (p) {
    var sc = { active: "#22c55e", archived: "#64748b", completed: "#00d4ff" }[p.status] || "#64748b";
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;background:rgba(15,23,42,0.5);border-radius:8px;margin-bottom:6px;"><div style="flex:1;"><span style="color:#e2e8f0;font-size:13px;font-weight:600;font-family:\'Inter\',sans-serif;">' + (p.name || "Untitled") + '</span>' + (p.description ? '<p style="color:#64748b;font-size:11px;margin:2px 0 0;">' + p.description.slice(0, 80) + '</p>' : '') + '</div><div style="display:flex;align-items:center;gap:8px;"><span style="color:' + sc + ';font-size:10px;text-transform:uppercase;">' + p.status + '</span><button onclick="deleteProject(\'' + p.id + '\')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px;padding:4px;">&times;</button></div></div>';
  }).join("") : '<p style="color:#475569;font-size:12px;">No projects yet.</p>';

  container.innerHTML = _SH.sectionHeader("Projects", "Save projects so ValleyMind has context about your work") +
    _SH.card("New Project", _SH.input("Project name", "projName") + '<div style="height:8px;"></div>' + _SH.textarea("Description (optional)", "projDesc") + '<div style="height:8px;"></div>' +
      '<div style="display:flex;gap:8px;"><div style="flex:1;">' + _SH.input("Goal", "projGoal") + '</div><div style="flex:1;">' + _SH.input("Deadline (optional)", "projDeadline") + '</div></div>' +
      '<div style="margin-top:10px;display:flex;gap:8px;">' + _SH.btn("Save Project", "saveProject()", "rgba(0,212,255,0.8)") + '<span style="margin:auto 0;font-size:11px;color:#94a3b8;" id="projStatus"></span></div>') +
    _SH.card("Project Library", listHtml);
}

function saveProject() {
  var name = _getVal("projName");
  var status = document.getElementById("projStatus");
  if (!name) { if (status) status.textContent = "Project name required."; return; }
  apiFetch("/api/settings/projects", {
    method: "POST", credentials: "include",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name: name, description: _getVal("projDesc"), goal: _getVal("projGoal"), deadline: _getVal("projDeadline"), status: "active" })
  }).then(function (r) { return r.json(); }).then(function (d) {
    _showSaved();
    if (status) status.textContent = "Saved!";
    document.getElementById("projName").value = ""; document.getElementById("projDesc").value = "";
    document.getElementById("projGoal").value = ""; document.getElementById("projDeadline").value = "";
    renderSettingsContent("projects");
    setTimeout(function () { if (status) status.textContent = ""; }, 2500);
  }).catch(function () { if (status) status.textContent = "Connection error"; });
}

function deleteProject(pid) {
  if (!confirm("Delete this project?")) return;
  apiFetch("/api/settings/projects/" + pid, { method: "DELETE", credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function () { _showSaved(); renderSettingsContent("projects"); })
    .catch(function () { alert("Failed to delete."); });
}

// ══════════════════════════════════════════════════════════════
// CREATOR PROFILE
// ══════════════════════════════════════════════════════════════

function renderCreatorSection(container) {
  container.innerHTML = _SH.sectionHeader("Creator Profile", "Configure your creative identity — used automatically for content generation");
  settingsApiGet("creator").then(function (d) {
    var data = d.data || {};
    container.innerHTML = _SH.sectionHeader("Creator Profile", "Configure your creative identity") +
      _SH.card("Primary Goal & Niche", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Primary Goal</p>' + _SH.select(["Grow YouTube", "Build a Business", "Learn Programming", "Graphic Design", "Marketing", "Student", "Personal Productivity", "Other"], "creatorGoal", data.goal) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Niche</p>' + _SH.input("Your niche", "creatorNiche", "text", data.niche) + '</div></div>') +
      _SH.card("Platforms", '<div style="display:flex;flex-wrap:wrap;gap:6px;" id="creatorPlatformsContainer">' + ["YouTube", "TikTok", "Instagram", "Facebook", "LinkedIn", "X (Twitter)", "Snapchat", "Pinterest"].map(function (p) {
        var active = (data.platforms || []).indexOf(p) !== -1;
        return '<span onclick="toggleCreatorPlatform(\'' + p + '\')" id="cplat_' + p.replace(/[^a-zA-Z0-9]/g, "_") + '" style="display:inline-block;background:' + (active ? "rgba(0,212,255,0.15)" : "rgba(255,255,255,0.04)") + ';color:' + (active ? "#00d4ff" : "#64748b") + ';border:1px solid ' + (active ? "rgba(0,212,255,0.3)" : "rgba(255,255,255,0.08)") + ';border-radius:6px;padding:4px 10px;font-size:11px;cursor:pointer;transition:all 0.15s;">' + p + '</span>';
      }).join("") + '</div>') +
      _SH.card("Content & Editing", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Content Style</p>' + _SH.select(["Educational", "Funny", "Storytelling", "Documentary", "Cinematic", "Gaming", "Tech", "Business", "Lifestyle", "Motivation"], "creatorContentStyle", data.content_style) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Editing Style</p>' + _SH.select(["Fast-paced", "Minimal", "Cinematic", "Documentary", "Shorts/Reels", "Podcast", "Vlog"], "creatorEditStyle", data.editing_style) + '</div></div>') +
      _SH.card("Brand Identity", '<div style="display:flex;gap:10px;"><div style="flex:1;">' + _SH.input("Brand Name", "brandName", "text", data.brand_name) + '</div><div style="width:40px;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Color</p>' + _SH.colorPicker("brandColor", data.brand_color || "#00d4ff") + '</div></div>' +
        '<div style="height:8px;"></div><div style="display:flex;gap:10px;"><div style="flex:1;">' + _SH.input("Intro (optional)", "brandIntro", "text", data.intro) + '</div><div style="flex:1;">' + _SH.input("Outro (optional)", "brandOutro", "text", data.outro) + '</div></div>' +
        '<div style="height:8px;"></div><div style="display:flex;gap:10px;"><div style="flex:1;">' + _SH.input("Watermark text", "brandWatermark", "text", data.watermark) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Font</p>' + _SH.select(["Inter", "Space Grotesk", "Roboto", "Poppins", "Playfair Display", "Monospace"], "brandFont", data.font) + '</div></div>') +
      _SH.card("Preferred AI Personality", _SH.select(["Professional", "Friendly", "Creative", "Technical", "Funny"], "creatorPersonality", data.personality)) +
      '<div style="margin-top:4px;">' + _SH.btn("Save Creator Profile", "saveCreatorProfile()", "rgba(0,212,255,0.8)") + _SH.statusSpan("creatorStatus") + '</div>';
  }).catch(function () { container.innerHTML = _SH.sectionHeader("Creator Profile") + '<p style="color:#64748b;">Could not load creator settings.</p>'; });
}

var _creatorPlatforms = [];
function toggleCreatorPlatform(platform) {
  var id = "cplat_" + platform.replace(/[^a-zA-Z0-9]/g, "_");
  var el = document.getElementById(id);
  if (!el) return;
  var idx = _creatorPlatforms.indexOf(platform);
  if (idx === -1) {
    _creatorPlatforms.push(platform);
    el.style.background = "rgba(0,212,255,0.15)"; el.style.color = "#00d4ff"; el.style.borderColor = "rgba(0,212,255,0.3)";
  } else {
    _creatorPlatforms.splice(idx, 1);
    el.style.background = "rgba(255,255,255,0.04)"; el.style.color = "#64748b"; el.style.borderColor = "rgba(255,255,255,0.08)";
  }
}

function saveCreatorProfile() {
  _saveSettingsAndShow("creator", {
    goal: _getVal("creatorGoal"), niche: _getVal("creatorNiche"), platforms: _creatorPlatforms,
    content_style: _getVal("creatorContentStyle"), editing_style: _getVal("creatorEditStyle"),
    brand_name: _getVal("brandName"), brand_color: _getVal("brandColor"),
    intro: _getVal("brandIntro"), outro: _getVal("brandOutro"), watermark: _getVal("brandWatermark"),
    font: _getVal("brandFont"), personality: _getVal("creatorPersonality"),
  }, "creatorStatus");
}

// ══════════════════════════════════════════════════════════════
// AI PREFERENCES
// ══════════════════════════════════════════════════════════════

function renderPreferencesSection(container) {
  container.innerHTML = _SH.sectionHeader("AI Preferences", "Customize how ValleyMind generates responses");
  settingsApiGet("preferences").then(function (d) {
    var data = d.data || {};
    container.innerHTML = _SH.sectionHeader("AI Preferences", "Customize how ValleyMind generates responses") +
      _SH.card("Response Style", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Response Length</p>' + _SH.select(["Short", "Balanced", "Detailed"], "prefResponseLength", data.response_length) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Creativity</p>' + _SH.select(["Conservative", "Moderate", "Creative", "Very Creative"], "prefCreativity", data.creativity) + '</div></div><div style="height:10px;"></div><div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Thinking Style</p>' + _SH.select(["Analytical", "Creative", "Balanced"], "prefThinkingStyle", data.thinking_style) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Default Language</p>' + _SH.select(["English", "Spanish", "French", "German", "Portuguese", "Arabic", "Chinese", "Japanese"], "prefLanguage", data.language) + '</div></div>') +
      _SH.card("Writing & Coding", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Writing Style</p>' + _SH.select(["Professional", "Conversational", "Academic", "Creative", "Technical"], "prefWritingStyle", data.writing_style) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Coding Style</p>' + _SH.select(["Minimal", "Explanatory", "Production-ready"], "prefCodingStyle", data.coding_style) + '</div></div>') +
      _SH.card("Media Preferences", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Image Style</p>' + _SH.select(["Standard", "Cinematic", "Artistic", "Realistic", "Anime", "3D Render"], "prefImageStyle", data.image_style) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Video Style</p>' + _SH.select(["Standard", "Cinematic", "Animation", "Documentary"], "prefVideoStyle", data.video_style) + '</div></div><div style="height:10px;"></div><div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Voice Style</p>' + _SH.select(["Natural", "Professional", "Friendly", "Energetic"], "prefVoiceStyle", data.voice_style) + '</div><div style="flex:1;display:flex;align-items:flex-end;gap:8px;padding-bottom:4px;"><span style="color:#94a3b8;font-size:11px;">Auto Suggestions</span>' + _SH.toggle("prefAutoSuggest", data.auto_suggestions !== false) + '</div></div>') +
      '<div style="margin-top:4px;">' + _SH.btn("Save AI Preferences", "saveAIPreferences()", "rgba(0,212,255,0.8)") + _SH.statusSpan("prefStatus") + '</div>';
  }).catch(function () { container.innerHTML = _SH.sectionHeader("AI Preferences") + '<p style="color:#64748b;">Could not load preferences.</p>'; });
}

function saveAIPreferences() {
  _saveSettingsAndShow("preferences", {
    response_length: _getVal("prefResponseLength"), creativity: _getVal("prefCreativity"),
    thinking_style: _getVal("prefThinkingStyle"), language: _getVal("prefLanguage"),
    writing_style: _getVal("prefWritingStyle"), coding_style: _getVal("prefCodingStyle"),
    image_style: _getVal("prefImageStyle"), video_style: _getVal("prefVideoStyle"),
    voice_style: _getVal("prefVoiceStyle"), auto_suggestions: _getChecked("prefAutoSuggest"),
  }, "prefStatus");
}

// ══════════════════════════════════════════════════════════════
// APPEARANCE
// ══════════════════════════════════════════════════════════════

function renderAppearanceSection(container) {
  container.innerHTML = _SH.sectionHeader("Appearance", "Customize the look and feel of ValleyMind");
  settingsApiGet("appearance").then(function (d) {
    var data = d.data || {};
    container.innerHTML = _SH.sectionHeader("Appearance", "Customize the look and feel of ValleyMind") +
      _SH.card("Theme & Colors", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Theme</p>' + _SH.select(["Dark", "Light (coming soon)"], "appTheme", data.theme) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Font Size</p>' + _SH.select(["Small", "Medium", "Large"], "appFontSize", data.font_size) + '</div></div>' +
        '<div style="margin-top:12px;"><p style="color:#94a3b8;font-size:10px;margin:0 0 8px;">Accent Color</p><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">' +
        ["#00d4ff", "#22c55e", "#eab308", "#ef4444", "#a855f7", "#ec4899"].map(function (c) {
          var sel = (data.accent_color || "#00d4ff") === c;
          return '<div onclick="setSettingsAccent(\'' + c + '\')" style="width:32px;height:32px;border-radius:50%;background:' + c + ';cursor:pointer;border:2px solid ' + (sel ? "rgba(255,255,255,0.5)" : "transparent") + ';transition:all 0.15s;" onmouseover="this.style.transform=\'scale(1.15)\'" onmouseout="this.style.transform=\'scale(1)\'" id="saccent-' + c.replace("#", "") + '"></div>';
        }).join("") + _SH.colorPicker("appAccentCustom", data.accent_color || "#00d4ff") + '</div></div>') +
      _SH.card("Layout", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Sidebar Style</p>' + _SH.select(["Full", "Icons Only", "Collapsed"], "appSidebar", data.sidebar_style) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Chat Bubble Style</p>' + _SH.select(["Modern", "Minimal", "Classic"], "appChatStyle", data.chat_style) + '</div></div>') +
      _SH.card("Accessibility", '<div style="display:flex;flex-direction:column;gap:10px;">' +
        _SH.row("Enable Animations", _SH.toggle("appAnimations", data.animations !== false), "Smooth transitions and effects") +
        _SH.row("Reduced Motion", _SH.toggle("appReducedMotion", data.reduced_motion === true), "Minimize animations for accessibility") +
        _SH.row("High Contrast", _SH.toggle("appHighContrast", data.high_contrast === true), "Increase contrast for readability") + '</div>') +
      '<div style="margin-top:4px;">' + _SH.btn("Save Appearance", "saveAppearance()", "rgba(0,212,255,0.8)") + _SH.statusSpan("appearStatus") + '</div>';
  }).catch(function () { container.innerHTML = _SH.sectionHeader("Appearance") + '<p style="color:#64748b;">Could not load appearance settings.</p>'; });
}

function saveAppearance() {
  var data = {
    theme: _getVal("appTheme"), font_size: _getVal("appFontSize"), accent_color: _getVal("appAccentCustom") || "#00d4ff",
    sidebar_style: _getVal("appSidebar"), chat_style: _getVal("appChatStyle"),
    animations: _getChecked("appAnimations"), reduced_motion: _getChecked("appReducedMotion"), high_contrast: _getChecked("appHighContrast"),
  };
  _saveSettingsAndShow("appearance", data, "appearStatus");
}

function setSettingsAccent(color) {
  document.querySelectorAll("[id^='saccent-']").forEach(function (el) { el.style.borderColor = "transparent"; });
  var el = document.getElementById("saccent-" + color.replace("#", ""));
  if (el) el.style.borderColor = "rgba(255,255,255,0.5)";
  document.getElementById("appAccentCustom").value = color;
  document.documentElement.style.setProperty("--accent-color", color);
}

// ══════════════════════════════════════════════════════════════
// NOTIFICATIONS
// ══════════════════════════════════════════════════════════════

function renderNotificationsSection(container) {
  container.innerHTML = _SH.sectionHeader("Notifications", "Control how ValleyMind communicates with you");
  settingsApiGet("notifications").then(function (d) {
    var data = d.data || {};
    container.innerHTML = _SH.sectionHeader("Notifications", "Control how ValleyMind communicates with you") +
      _SH.card("Notification Preferences", '<div style="display:flex;flex-direction:column;">' +
        _SH.row("Daily Summary", _SH.toggle("notifDaily", data.daily_summary !== false), "Receive a daily summary from ValleyMind") +
        _SH.row("Deadlines", _SH.toggle("notifDeadlines", data.deadlines !== false), "Notify about approaching deadlines") +
        _SH.row("AI Suggestions", _SH.toggle("notifSuggestions", data.ai_suggestions === true), "Receive proactive AI suggestions") +
        _SH.row("Project Updates", _SH.toggle("notifProjects", data.project_updates !== false), "Get notified about project changes") +
        _SH.row("Memory Reminders", _SH.toggle("notifMemory", data.memory_reminders !== false), "Periodic memory review reminders") + '</div>') +
      _SH.card("Delivery Methods", '<div style="display:flex;flex-direction:column;">' +
        _SH.row("Email Notifications", _SH.toggle("notifEmail", data.email_notifications !== false), "Receive notifications via email") +
        _SH.row("Push Notifications", _SH.toggle("notifPush", data.push_notifications !== false), "Receive push notifications in browser") + '</div>') +
      '<div style="margin-top:4px;">' + _SH.btn("Save Notification Preferences", "saveNotifications()", "rgba(0,212,255,0.8)") + _SH.statusSpan("notifStatus") + '</div>';
  }).catch(function () { container.innerHTML = _SH.sectionHeader("Notifications") + '<p style="color:#64748b;">Could not load notification settings.</p>'; });
}

function saveNotifications() {
  _saveSettingsAndShow("notifications", {
    daily_summary: _getChecked("notifDaily"), deadlines: _getChecked("notifDeadlines"),
    ai_suggestions: _getChecked("notifSuggestions"), project_updates: _getChecked("notifProjects"),
    memory_reminders: _getChecked("notifMemory"), email_notifications: _getChecked("notifEmail"),
    push_notifications: _getChecked("notifPush"),
  }, "notifStatus");
}

// ══════════════════════════════════════════════════════════════
// KNOWLEDGE BASE
// ══════════════════════════════════════════════════════════════

function renderKnowledgeSection(container) {
  container.innerHTML = _SH.sectionHeader("Knowledge Base", "Upload documents, PDFs, and notes — ValleyMind uses this for context");
  apiFetch("/api/settings/knowledge", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var items = d.items || [];
      var listHtml = items.length > 0 ? items.map(function (item) {
        var icons = { pdf: "PDF", doc: "DOC", website: "WEB", image: "IMG", note: "NOTE" };
        return '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-bottom:4px;"><div style="flex:1;"><span style="color:#e2e8f0;font-size:12px;">' + item.title + '</span></div><button onclick="deleteKnowledgeItem(\'' + item.id + '\')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px;">&times;</button></div>';
      }).join("") : '<p style="color:#475569;font-size:12px;">No items yet.</p>';
      container.innerHTML = _SH.sectionHeader("Knowledge Base", "Upload documents, PDFs, and notes — ValleyMind uses this for context") +
        _SH.card("Add to Knowledge Base", '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + _SH.btn("Add Note", "showAddKnowledgeNote()") + _SH.btn("Upload PDF", "showComingSoon('PDF Upload')") + _SH.btn("Upload Document", "showComingSoon('Document Upload')") + _SH.btn("Add Website", "showComingSoon('Website Import')") + '</div><div id="knowledgeAddForm" style="margin-top:10px;"></div>') +
        _SH.card("Your Knowledge Items", listHtml);
    }).catch(function () { container.innerHTML = _SH.sectionHeader("Knowledge Base") + '<p style="color:#64748b;">Could not load knowledge base.</p>'; });
}

function showAddKnowledgeNote() {
  var form = document.getElementById("knowledgeAddForm");
  if (!form) return;
  form.innerHTML = '<div style="background:rgba(15,23,42,0.5);border-radius:8px;padding:12px;">' + _SH.input("Note title", "knowledgeNoteTitle") + '<div style="height:6px;"></div>' + _SH.textarea("Write your note content...", "knowledgeNoteContent") + '<div style="margin-top:8px;">' + _SH.btn("Save Note", "saveKnowledgeNote()", "rgba(0,212,255,0.8)") + ' <span style="font-size:11px;color:#94a3b8;" id="knowStatus"></span></div></div>';
}

function saveKnowledgeNote() {
  var title = _getVal("knowledgeNoteTitle");
  var content = _getVal("knowledgeNoteContent");
  if (!title || !content) { var el = document.getElementById("knowStatus"); if (el) el.textContent = "Title and content required."; return; }
  apiFetch("/api/settings/knowledge", { method: "POST", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ type: "note", title: title, content: content }) })
    .then(function (r) { return r.json(); }).then(function () { _showSaved(); renderSettingsContent("knowledge"); })
    .catch(function () { alert("Failed to save."); });
}

function deleteKnowledgeItem(id) {
  if (!confirm("Delete this item?")) return;
  apiFetch("/api/settings/knowledge", { method: "DELETE", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ id: id }) })
    .then(function (r) { return r.json(); }).then(function () { _showSaved(); renderSettingsContent("knowledge"); })
    .catch(function () { alert("Failed to delete."); });
}

// ══════════════════════════════════════════════════════════════
// MEDIA LIBRARY
// ══════════════════════════════════════════════════════════════

function renderMediaSection(container) {
  container.innerHTML = _SH.sectionHeader("Media Library", "All your generated images and videos in one place");
  apiFetch("/api/settings/media", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var images = d.images || [];
      var videos = d.videos || [];
      var imageHtml = images.length > 0 ? images.map(function (img) {
        return '<div style="position:relative;border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,0.06);background:rgba(15,23,42,0.5);"><img src="' + img.url + '" alt="' + img.name + '" style="width:100%;height:140px;object-fit:cover;display:block;" loading="lazy"><div style="padding:6px 8px;display:flex;align-items:center;justify-content:space-between;background:rgba(2,6,23,0.6);"><span style="color:#94a3b8;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100px;">' + img.name + '</span><div style="display:flex;gap:4px;"><a href="' + img.url + '" download style="background:none;border:none;color:#00d4ff;cursor:pointer;font-size:12px;text-decoration:none;" title="Download">DL</a><button onclick="deleteMedia(\'' + img.url + '\')" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;padding:0;" title="Delete">&times;</button></div></div></div>';
      }).join("") : null;
      container.innerHTML = _SH.sectionHeader("Media Library", "All your generated images and videos in one place") +
        _SH.card("Images", imageHtml ? '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;">' + imageHtml + '</div>' : '<p style="color:#475569;font-size:12px;">No images yet. Generated images will appear here automatically.</p>') +
        _SH.card("Videos", '<p style="color:#475569;font-size:12px;">No videos yet. Generated videos will appear here automatically.</p>');
    }).catch(function () { container.innerHTML = _SH.sectionHeader("Media Library") + '<p style="color:#64748b;">Could not load media library.</p>'; });
}

function deleteMedia(url) {
  if (!confirm("Delete this media file?")) return;
  apiFetch("/api/settings/media", { method: "DELETE", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ url: url }) })
    .then(function () { _showSaved(); renderSettingsContent("media"); })
    .catch(function () { alert("Failed to delete."); });
}

// ══════════════════════════════════════════════════════════════
// STORAGE
// ══════════════════════════════════════════════════════════════

function renderStorageSection(container) {
  container.innerHTML = _SH.sectionHeader("Storage", "Real-time storage usage");
  apiFetch("/api/settings/storage", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var u = d.usage || {};
      var items = [
        { label: "Images", key: "images_mb", color: "#00d4ff" },
        { label: "Videos", key: "videos_mb", color: "#a855f7" },
        { label: "Documents", key: "documents_mb", color: "#22c55e" },
        { label: "Knowledge", key: "knowledge_mb", color: "#eab308" },
        { label: "Memory", key: "memory_mb", color: "#ec4899" },
        { label: "Cache", key: "cache_mb", color: "#64748b" },
      ];
      var bars = items.map(function (item) {
        var val = u[item.key] || 0;
        var pct = u.total_mb > 0 ? Math.min((val / u.total_mb) * 100, 100) : 0;
        return '<div><div style="display:flex;justify-content:space-between;margin-bottom:4px;"><span style="color:#94a3b8;font-size:11px;">' + item.label + '</span><span style="color:#475569;font-size:11px;">' + val + ' MB</span></div><div style="width:100%;height:6px;background:rgba(255,255,255,0.06);border-radius:3px;overflow:hidden;"><div style="width:' + pct + '%;height:100%;background:' + item.color + ';border-radius:3px;transition:width 0.6s;"></div></div></div>';
      }).join("");
      var totalPct = u.used_pct || 0;
      container.innerHTML = _SH.sectionHeader("Storage", "Real-time storage usage") +
        _SH.card("Usage Breakdown", bars + '<div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06);">' +
          '<div style="display:flex;justify-content:space-between;"><span style="color:#e2e8f0;font-size:13px;font-weight:600;">Total Used</span><span style="color:#e2e8f0;font-size:13px;font-weight:600;">' + (u.total_mb || 0) + ' MB</span></div>' +
          '<div style="display:flex;justify-content:space-between;margin-top:4px;"><span style="color:#64748b;font-size:11px;">Available</span><span style="color:#64748b;font-size:11px;">' + (u.available_mb || 500) + ' MB</span></div>' +
          '<div style="margin-top:10px;width:100%;height:8px;background:rgba(255,255,255,0.06);border-radius:4px;overflow:hidden;"><div style="width:' + Math.min(totalPct, 100) + '%;height:100%;background:linear-gradient(90deg,#00d4ff,#0ea5e9);border-radius:4px;transition:width 0.6s;"></div></div>' +
          '<p style="color:#475569;font-size:10px;margin-top:4px;">' + totalPct + '% of 500 MB used</p></div>') +
        _SH.card("Manage Storage", '<div style="display:flex;gap:8px;flex-wrap:wrap;">' + _SH.btn("Clear Cache", "clearLocalCache()") + _SH.btn("Clear Generated Images", "showComingSoon('Clear Images')") + '</div>');
    }).catch(function () { container.innerHTML = _SH.sectionHeader("Storage") + '<p style="color:#64748b;">Could not load storage info.</p>'; });
}

// ══════════════════════════════════════════════════════════════
// BILLING
// ══════════════════════════════════════════════════════════════

function renderBillingSection(container) {
  container.innerHTML = _SH.sectionHeader("Billing", "Manage your subscription — Nigerian Naira (₦)");
  apiFetch("/api/settings/billing", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var plans = d.plans || {};
      var currentPlan = d.plan || "free";
      var usage = d.usage || {};
      var planCards = Object.keys(plans).map(function (key) {
        var p = plans[key];
        var isCurrent = key === currentPlan;
        return '<div style="flex:1;min-width:180px;background:' + (isCurrent ? "rgba(0,212,255,0.06)" : "rgba(255,255,255,0.03)") + ';border:1px solid ' + (isCurrent ? "#00d4ff" : "rgba(255,255,255,0.06)") + ';border-radius:12px;padding:16px;"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;"><span style="color:#f1f5f9;font-size:15px;font-weight:700;">' + p.name + '</span>' + (isCurrent ? '<span style="background:rgba(0,212,255,0.15);color:#00d4ff;font-size:9px;padding:2px 6px;border-radius:4px;text-transform:uppercase;">Current</span>' : '') + '</div>' +
          '<p style="color:#00d4ff;font-size:22px;font-weight:800;margin:0 0 10px;">₦' + p.price.toLocaleString() + '<span style="font-size:11px;color:#64748b;font-weight:400;">/mo</span></p>' +
          (p.features || []).map(function (f) { return '<div style="display:flex;align-items:center;gap:6px;padding:3px 0;"><span style="color:#22c55e;font-size:12px;">✓</span><span style="color:#94a3b8;font-size:11px;">' + f + '</span></div>'; }).join("") +
          (isCurrent ? '' : '<div style="margin-top:10px;">' + _SH.btn("Upgrade", "showComingSoon('Upgrade to " + p.name + "')", key === "free" ? "rgba(100,116,139,0.3)" : "rgba(0,212,255,0.8)") + '</div>') + '</div>';
      }).join("");
      container.innerHTML = _SH.sectionHeader("Billing", "Manage your subscription — Nigerian Naira (₦)") +
        _SH.card("Current Plan: " + (plans[currentPlan] ? plans[currentPlan].name : "Free"), '<div style="display:flex;flex-wrap:wrap;gap:12px;">' + planCards + '</div>') +
        _SH.card("Monthly Usage", '<div style="display:flex;gap:16px;flex-wrap:wrap;">' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:8px;padding:12px;flex:1;min-width:100px;"><p style="color:#94a3b8;font-size:10px;">Chats Used</p><p style="color:#e2e8f0;font-size:18px;font-weight:700;">' + (usage.monthly_chats || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:8px;padding:12px;flex:1;min-width:100px;"><p style="color:#94a3b8;font-size:10px;">Images</p><p style="color:#e2e8f0;font-size:18px;font-weight:700;">' + (usage.monthly_images || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:8px;padding:12px;flex:1;min-width:100px;"><p style="color:#94a3b8;font-size:10px;">Credits</p><p style="color:#e2e8f0;font-size:18px;font-weight:700;">' + (usage.credits_remaining || 0) + '</p></div></div>') +
        _SH.card("Billing History", '<p style="color:#475569;font-size:12px;">No billing history yet.</p>') +
        _SH.card("Payment Methods", '<p style="color:#475569;font-size:12px;">No payment methods saved. Card, bank transfer, and USSD options coming soon.</p>');
    }).catch(function () { container.innerHTML = _SH.sectionHeader("Billing") + '<p style="color:#64748b;">Could not load billing info.</p>'; });
}

// ══════════════════════════════════════════════════════════════
// PRIVACY & SECURITY
// ══════════════════════════════════════════════════════════════

function renderPrivacySection(container) {
  container.innerHTML = _SH.sectionHeader("Privacy & Security", "Control your data and account security") +
    _SH.card("Data Management", '<div style="display:flex;flex-wrap:wrap;gap:8px;">' +
      _SH.btn("Export My Data", "exportMyData()") +
      _SH.btn("Download Data", "exportMyData()") +
      _SH.btn("Delete All Chats", "confirmDeleteChats()", "rgba(239,68,68,0.15)") +
      _SH.btn("Clear Memory", "confirmClearMemory()", "rgba(239,68,68,0.15)") +
      _SH.btn("Clear Local Cache", "clearLocalCache()") + '</div>') +
    _SH.card("Privacy Controls", '<div style="display:flex;flex-direction:column;">' +
      _SH.row("Data Retention", _SH.select(["30 days", "90 days", "1 year", "Forever"], "privacyRetention", "1 year"), "How long ValleyMind keeps your data") + '</div>') +
    _SH.card("Account Actions", '<div style="display:flex;flex-wrap:wrap;gap:8px;">' +
      _SH.btn("Logout", "confirmLogout()", "rgba(239,68,68,0.15)") +
      _SH.btn("Delete Account", "confirmDeleteAccount()", "rgba(239,68,68,0.15)") + '</div>') +
    _SH.card("Security Notes", '<p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:0;">Your data is stored securely. ValleyMind uses encryption for all communications. Regular security audits are performed.</p>');
}

function confirmDeleteChats() {
  if (confirm("Delete all chat history? This cannot be undone.")) {
    apiFetch("/chat/sessions", { method: "DELETE", credentials: "include", headers: authHeaders() })
      .then(function () { location.reload(); }).catch(function () { alert("Failed to delete chats."); });
  }
}

function confirmClearMemory() {
  if (confirm("Clear all long-term memory? This cannot be undone.")) {
    apiFetch("/api/settings/memory-fields", { method: "PUT", credentials: "include", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({}) })
      .then(function () { alert("Memory cleared."); _showSaved(); }).catch(function () { alert("Failed to clear memory."); });
  }
}

function confirmLogout() { if (confirm("Are you sure you want to log out?")) { logout(); } }
function confirmDeleteAccount() { if (confirm("Permanently delete your account?")) { showComingSoon("Account Deletion"); } }

function exportMyData() {
  Promise.all([
    apiFetch("/api/settings/memory-fields", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }),
    apiFetch("/api/settings/projects", { credentials: "include", headers: authHeaders() }).then(function (r) { return r.json(); }),
  ]).then(function (results) {
    var data = { memory: results[0].fields || {}, projects: results[1].projects || [] };
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a"); a.href = url; a.download = "valleymind-export.json"; a.click();
    URL.revokeObjectURL(url);
  }).catch(function () { alert("Could not export data."); });
}

function clearLocalCache() {
  localStorage.removeItem("valleymind_sessions_cache");
  localStorage.removeItem("valleymind_last_chat_id");
  localStorage.removeItem("pinnedSessions");
  alert("Local cache cleared.");
}

// ══════════════════════════════════════════════════════════════
// LANGUAGE & REGION
// ══════════════════════════════════════════════════════════════

function renderLanguageSection(container) {
  container.innerHTML = _SH.sectionHeader("Language & Region", "Configure your regional preferences");
  settingsApiGet("language").then(function (d) {
    var data = d.data || {};
    container.innerHTML = _SH.sectionHeader("Language & Region", "Configure your regional preferences") +
      _SH.card("Language", '<p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Display Language</p>' + _SH.select(["English", "Spanish", "French", "German", "Portuguese", "Arabic", "Chinese", "Japanese", "Hindi"], "langLanguage", data.language)) +
      _SH.card("Region & Formatting", '<div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Region</p>' + _SH.select(["Nigeria", "United States", "United Kingdom", "Canada", "Australia", "Germany", "France", "Other"], "langRegion", data.region) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Time Zone</p>' + _SH.select(["UTC+1 (WAT)", "UTC+0 (GMT)", "UTC-5 (EST)", "UTC-8 (PST)", "UTC+2 (CEST)", "UTC+8 (SGT)", "UTC+9 (JST)"], "langTimezone", data.timezone) + '</div></div>' +
        '<div style="height:10px;"></div><div style="display:flex;gap:10px;"><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Date Format</p>' + _SH.select(["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"], "langDateFormat", data.date_format) + '</div><div style="flex:1;"><p style="color:#94a3b8;font-size:10px;margin:0 0 4px;">Currency</p>' + _SH.select(["NGN (₦)", "USD ($)", "EUR (€)", "GBP (£)"], "langCurrency", data.currency) + '</div></div>') +
      '<div style="margin-top:4px;">' + _SH.btn("Save", "saveLanguagePrefs()", "rgba(0,212,255,0.8)") + _SH.statusSpan("langStatus") + '</div>';
  }).catch(function () { container.innerHTML = _SH.sectionHeader("Language & Region") + '<p style="color:#64748b;">Could not load language settings.</p>'; });
}

function saveLanguagePrefs() {
  _saveSettingsAndShow("language", {
    language: _getVal("langLanguage"), region: _getVal("langRegion"), timezone: _getVal("langTimezone"),
    date_format: _getVal("langDateFormat"), currency: _getVal("langCurrency"),
  }, "langStatus");
}

// ══════════════════════════════════════════════════════════════
// INTEGRATIONS
// ══════════════════════════════════════════════════════════════

function renderIntegrationsSection(container) {
  var integrations = [
    { name: "Google Drive", desc: "Connect your Google Drive files" },
    { name: "OneDrive", desc: "Access Microsoft OneDrive documents" },
    { name: "Dropbox", desc: "Link your Dropbox storage" },
    { name: "GitHub", desc: "Import code repositories" },
    { name: "Notion", desc: "Sync your Notion workspace" },
    { name: "Calendar", desc: "Connect your calendar for scheduling" },
  ];
  container.innerHTML = _SH.sectionHeader("Integrations", "Connect ValleyMind to your favorite tools") +
    _SH.card("Available Integrations", integrations.map(function (i) {
      return '<div style="display:flex;align-items:center;gap:12px;padding:12px;background:rgba(15,23,42,0.5);border-radius:8px;margin-bottom:6px;"><div style="flex:1;"><span style="color:#e2e8f0;font-size:13px;font-weight:600;">' + i.name + '</span><p style="color:#64748b;font-size:11px;margin:2px 0 0;">' + i.desc + '</p></div><span style="background:rgba(100,116,139,0.15);color:#64748b;font-size:9px;padding:3px 8px;border-radius:4px;text-transform:uppercase;">Coming Soon</span></div>';
    }).join("")) +
    _SH.card("Future Integrations", '<p style="color:#64748b;font-size:12px;margin:0;">More integrations are in development: Slack, Discord, Trello, Figma, and more.</p>');
}

// ══════════════════════════════════════════════════════════════
// EXTENSIONS
// ══════════════════════════════════════════════════════════════

function renderExtensionsSection(container) {
  container.innerHTML = _SH.sectionHeader("Extensions", "Extend ValleyMind with plugins and extensions") +
    _SH.card("Extension Store", '<div style="text-align:center;padding:24px 0;"><p style="color:#e2e8f0;font-size:15px;font-weight:600;margin:12px 0 4px;">Extension Marketplace</p><p style="color:#64748b;font-size:12px;max-width:400px;margin:0 auto;">The ValleyMind Extension Store is coming soon. Developers will be able to create and publish plugins.</p><div style="margin-top:16px;display:flex;justify-content:center;gap:8px;flex-wrap:wrap;">' +
      _SH.badge("Custom LLM Connectors") + _SH.badge("Data Sources") + _SH.badge("Export Formats") + _SH.badge("Workflow Automation") + _SH.badge("Custom Tools") + '</div></div>') +
    _SH.card("Installed Extensions", '<p style="color:#475569;font-size:12px;">No extensions installed yet.</p>');
}

// ══════════════════════════════════════════════════════════════
// USAGE
// ══════════════════════════════════════════════════════════════

function renderUsageSection(container) {
  container.innerHTML = _SH.sectionHeader("Usage", "Your ValleyMind activity overview");
  apiFetch("/api/settings/usage", { credentials: "include", headers: authHeaders() })
    .then(function (r) { return r.json(); }).then(function (d) {
      var u = d.usage || {};
      container.innerHTML = _SH.sectionHeader("Usage", "Your ValleyMind activity overview") +
        _SH.card("Activity Overview", '<div style="display:flex;gap:12px;flex-wrap:wrap;">' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:10px;padding:14px;flex:1;min-width:120px;"><p style="color:#94a3b8;font-size:10px;text-transform:uppercase;">Chat Sessions</p><p style="color:#e2e8f0;font-size:22px;font-weight:700;">' + (u.chat_sessions || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:10px;padding:14px;flex:1;min-width:120px;"><p style="color:#94a3b8;font-size:10px;text-transform:uppercase;">Messages</p><p style="color:#e2e8f0;font-size:22px;font-weight:700;">' + (u.chat_messages || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:10px;padding:14px;flex:1;min-width:120px;"><p style="color:#94a3b8;font-size:10px;text-transform:uppercase;">Images</p><p style="color:#e2e8f0;font-size:22px;font-weight:700;">' + (u.images_generated || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:10px;padding:14px;flex:1;min-width:120px;"><p style="color:#94a3b8;font-size:10px;text-transform:uppercase;">Memory</p><p style="color:#e2e8f0;font-size:22px;font-weight:700;">' + (u.memory_entries || 0) + '</p></div>' +
          '<div style="background:rgba(15,23,42,0.5);border-radius:10px;padding:14px;flex:1;min-width:120px;"><p style="color:#94a3b8;font-size:10px;text-transform:uppercase;">Storage</p><p style="color:#e2e8f0;font-size:22px;font-weight:700;">' + (u.storage_mb || 0) + ' MB</p></div></div>') +
        _SH.card("Recent Sessions", '<div style="max-height:300px;overflow-y:auto;">' +
          ((u.sessions || []).length > 0 ? u.sessions.slice(0, 20).map(function (s) {
            return '<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:#e2e8f0;font-size:11px;">' + (s.title || "Untitled") + '</span><span style="color:#475569;font-size:10px;">' + _pluralize(s.message_count || 0, "msg") + '</span></div>';
          }).join("") : '<p style="color:#475569;font-size:12px;">No sessions yet.</p>') + '</div>');
    }).catch(function () { container.innerHTML = _SH.sectionHeader("Usage") + '<p style="color:#64748b;">Could not load usage data.</p>'; });
}

// ── Open/Close Settings ──────────────────────────────────────

window.openSettings = function () {
  var overlay = document.getElementById("settingsOverlay");
  if (overlay) overlay.style.display = "flex";
  buildSettingsNav();
  switchSettingsSection("account");
};

window.closeSettings = function () {
  var overlay = document.getElementById("settingsOverlay");
  if (overlay) overlay.style.display = "none";
};
