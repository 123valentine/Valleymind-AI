// ── ValleyMind Guided First-Time Experience ────────────────────────────
// Each feature shows a small modern walkthrough the first time it is opened.
// Flags (seenStudioGuide, seenTemplateGuide, ...) are stored per-browser in
// localStorage so a guide never repeats — unless the user resets tutorials
// in Settings or replays one from the Help & Learning Center.

(function () {
    "use strict";

    var GUIDE_FLAG_PREFIX = "vm_guide_seen_";

    var GUIDES = [
        { key: "chat", title: "AI Chat", icon: "smart_toy",
          desc: "This is your intelligent AI assistant. Ask questions, generate ideas, write content, solve problems, and much more." },
        { key: "studio", title: "Studio", icon: "clapperboard",
          desc: "Welcome to Studio. Here you can edit videos, images, audio, use templates, add stickers, effects, transitions, and export professional content." },
        { key: "templates", title: "Templates", icon: "layout-template",
          desc: "Browse professional templates. Choose one, upload your own media, and ValleyMind AI will automatically generate a polished project." },
        { key: "assets", title: "Assets", icon: "folder-open",
          desc: "Store your personal stickers, GIFs, music, sounds, videos, images, fonts, and reusable creative assets." },
        { key: "projects", title: "Projects", icon: "projector",
          desc: "All of your saved work lives here. Resume editing at any time." },
        { key: "image", title: "AI Image Generator", icon: "image",
          desc: "Describe what you want and generate high-quality AI images." },
        { key: "video", title: "AI Video Generator", icon: "clapperboard",
          desc: "Create AI-powered videos from text, images, or templates." },
        { key: "website", title: "Website Builder", icon: "sparkles",
          desc: "Build complete websites using AI with little or no coding." },
    ];

    var activePopup = null;
    var pendingQueue = [];

    function flagName(key) {
        return GUIDE_FLAG_PREFIX + key;
    }

    function guideByKey(key) {
        for (var i = 0; i < GUIDES.length; i++) if (GUIDES[i].key === key) return GUIDES[i];
        return null;
    }

    function vmGuideSeen(key) {
        try { return localStorage.getItem(flagName(key)) === "1"; } catch (e) { return false; }
    }

    function vmGuideMarkSeen(key) {
        try { localStorage.setItem(flagName(key), "1"); } catch (e) { /* private mode */ }
    }

    function vmGuideReset() {
        try {
            GUIDES.forEach(function (g) { localStorage.removeItem(flagName(g.key)); });
        } catch (e) { /* non-fatal */ }
        return GUIDES.length;
    }

    // Show the guide for a feature the first time it is opened.
    function vmGuideMaybeShow(key) {
        if (vmGuideSeen(key)) return false;
        vmGuideMarkSeen(key);
        return vmGuideForce(key);
    }

    // Show the guide regardless of the seen flag (Help Center replay).
    function vmGuideForce(key) {
        var g = guideByKey(key);
        if (!g) return false;
        enqueueGuide(g);
        return true;
    }

    function enqueueGuide(g) {
        pendingQueue.push(g);
        if (!activePopup) showNextGuide();
    }

    function showNextGuide() {
        if (activePopup || pendingQueue.length === 0) return;
        var g = pendingQueue.shift();
        activePopup = g.key;
        renderPopup(g);
    }

    function dismissPopup() {
        if (activePopup) {
            var pop = document.getElementById("vmGuidePopup");
            if (pop) {
                pop.style.opacity = "0";
                pop.style.transform = "translate(-50%, 14px)";
                setTimeout(function () { if (pop.parentNode) pop.parentNode.removeChild(pop); }, 240);
            }
        }
        activePopup = null;
        setTimeout(showNextGuide, 80);
    }

    function renderPopup(g) {
        var existing = document.getElementById("vmGuidePopup");
        if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

        var pop = document.createElement("div");
        pop.id = "vmGuidePopup";
        pop.style.cssText = [
            "position:fixed;left:50%;bottom:24px;transform:translate(-50%,0);z-index:999997;",
            "width:min(430px,calc(100vw - 32px));",
            "background:rgba(11,19,28,0.98);border:1px solid rgba(0,212,255,0.3);border-radius:16px;",
            "padding:18px 18px 14px;box-shadow:0 18px 50px rgba(0,0,0,0.6),0 0 24px rgba(0,212,255,0.12);",
            "opacity:0;transition:opacity 0.24s ease,transform 0.24s ease;font-family:'Inter',sans-serif;",
        ].join("");
        pop.innerHTML =
            '<div style="display:flex;align-items:flex-start;gap:12px;">' +
                '<div style="width:40px;height:40px;border-radius:12px;background:rgba(0,212,255,0.12);border:1px solid rgba(0,212,255,0.25);display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#00d4ff;">' +
                    '<span class="material-icons" style="font-size:20px;">' + (g.icon || "info") + '</span>' +
                '</div>' +
                '<div style="flex:1;min-width:0;">' +
                    '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px;">' +
                        '<span style="color:#f1f5f9;font-size:14px;font-weight:800;font-family:\'Space Grotesk\',sans-serif;letter-spacing:0.02em;">' + g.title + '</span>' +
                        '<span style="background:rgba(0,212,255,0.14);color:#00d4ff;font-size:9px;text-transform:uppercase;letter-spacing:0.14em;padding:2px 7px;border-radius:999px;font-weight:700;flex-shrink:0;">First look</span>' +
                    '</div>' +
                    '<p style="color:#94a3b8;font-size:12.5px;line-height:1.55;margin:0;">' + g.desc + '</p>' +
                '</div>' +
            '</div>' +
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:14px;gap:8px;">' +
                '<button type="button" onclick="vmHelpCenter.open()" style="background:none;border:none;color:#64748b;font-size:11px;cursor:pointer;font-family:\'Inter\',sans-serif;text-decoration:underline;">Learn more</button>' +
                '<button type="button" onclick="dismissPopup()" style="border:none;border-radius:10px;background:#00d4ff;color:#003642;font-weight:800;padding:8px 20px;cursor:pointer;text-transform:uppercase;letter-spacing:0.08em;font-family:\'Space Grotesk\',sans-serif;font-size:11px;">Got it</button>' +
            '</div>';
        document.body.appendChild(pop);
        requestAnimationFrame(function () {
            pop.style.opacity = "1";
            pop.style.transform = "translate(-50%,0)";
        });
        // Auto-dismiss if the user ignores it.
        setTimeout(function () {
            if (activePopup === g.key) dismissPopup();
        }, 16000);
    }

    window.vmGuideSeen = vmGuideSeen;
    window.vmGuideMarkSeen = vmGuideMarkSeen;
    window.vmGuideReset = vmGuideReset;
    window.vmGuideMaybeShow = vmGuideMaybeShow;
    window.vmGuideForce = vmGuideForce;
    window.dismissPopup = dismissPopup;
    window.VM_GUIDES = GUIDES;

    // ── Help & Learning Center ─────────────────────────────────────────

    var HELP_ARTICLES = [
        { id: "chat", title: "Getting started with AI Chat", html:
            "<p>Chat is ValleyMind's intelligent assistant. Ask questions, brainstorm, write, code or solve problems in plain language.</p>" +
            "<ul><li>Press <b>Enter</b> to send, <b>Shift+Enter</b> for a new line.</li>" +
            "<li>Use the <b>+</b> button to attach an image or video for analysis.</li>" +
            "<li>Click the persona chip (Marcus) to switch voices at the Round Table.</li></ul>" },
        { id: "studio", title: "Studio — your creative suite", html:
            "<p>Studio is the home of your creative tools: video, photo, AI generation, templates, assets and projects.</p>" +
            "<ul><li>Use the tabs at the top of Studio to switch tools.</li>" +
            "<li><b>AutoCut / Start Production</b> turns any idea into a finished video with the AI crew.</li>" +
            "<li>Enable <b>Test mode</b> to preview the pipeline with free placeholder clips.</li></ul>" },
        { id: "templates", title: "Working with Templates", html:
            "<p>Templates give you a professional starting point.</p>" +
            "<ul><li>Open the <b>Templates</b> tab and pick a category.</li>" +
            "<li>Click <b>Use Template</b> — ValleyMind fills in the idea and starts production.</li></ul>" },
        { id: "assets", title: "Managing your Assets", html:
            "<p>Assets stores stickers, GIFs, music, sounds, videos, images, fonts and reusable creative files.</p>" +
            "<p>Your generated images and videos appear here automatically, and your uploads are saved so you can reuse them in any project.</p>" },
        { id: "projects", title: "Your Projects", html:
            "<p>Every saved project lives in the Projects tab. Resume editing any time — nothing is lost when you close the app.</p>" },
        { id: "image", title: "AI Image Generator", html:
            "<p>Describe any image and ValleyMind generates it for you.</p>" +
            "<ul><li>Open the <b>AI</b> tab in Studio, then <b>Text &rarr; Image</b>.</li>" +
            "<li>Generated images are saved to your Media Library automatically.</li></ul>" },
        { id: "video", title: "AI Video Generator", html:
            "<p>Create AI-powered videos from text, images or templates through the Studio pipeline.</p>" +
            "<ul><li>Write a prompt in the Studio bar and press <b>Start Production</b>.</li>" +
            "<li>Pick a length (30s&ndash;2.5min); scene count follows automatically.</li></ul>" },
        { id: "website", title: "Website Builder (AI Builder)", html:
            "<p>Describe an idea and the AI Builder produces a complete, production-ready project &mdash; specification, codebase, live preview and download.</p>" },
        { id: "memory", title: "Memory & Personalization", html:
            "<p>ValleyMind remembers what you tell it. Open <b>Settings</b> to set your interests, goals, AI preferences, theme and more.</p>" +
            "<p>Everything you save is written into your long-term memory, so recommendations and answers get more personal over time.</p>" },
        { id: "settings", title: "Settings explained", html:
            "<p>The Settings page is your personalization center: account information, security, connected accounts, interests, goals, memory, AI preferences, theme, accessibility, language, notifications, privacy, data &amp; storage, subscription, export and account deletion.</p>" },
    ];

    var HELP_STATE = { tab: "tutorials", query: "" };

    function helpFilteredGuides() {
        var q = HELP_STATE.query.toLowerCase();
        return (window.VM_GUIDES || []).filter(function (g) {
            if (!q) return true;
            return (g.title + " " + g.desc).toLowerCase().indexOf(q) !== -1;
        });
    }

    function helpFilteredArticles() {
        var q = HELP_STATE.query.toLowerCase();
        return HELP_ARTICLES.filter(function (a) {
            if (!q) return true;
            return (a.title + " " + a.html).toLowerCase().indexOf(q) !== -1;
        });
    }

    function helpSubmit(kind) {
        var key = kind === "bug" ? "helpBug" : kind === "request" ? "helpRequest" : "helpSupport";
        var box = document.getElementById(key);
        var status = document.getElementById(key + "Status");
        var text = box ? box.value.trim() : "";
        if (!text) {
            if (status) { status.textContent = "Please write a short message first."; status.style.color = "#f59e0b"; }
            return;
        }
        var label = kind === "bug" ? "[Bug report] " : kind === "request" ? "[Feature request] " : "[Support] ";
        apiFetch("/suggestions", {
            method: "POST",
            credentials: "include",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ text: label + text })
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.status === "success") {
                if (box) box.value = "";
                if (status) { status.textContent = "Thanks — your " + kind + " has been sent to the ValleyMind team."; status.style.color = "#22c55e"; }
            } else {
                if (status) { status.textContent = d.message || "Could not send. Try again."; status.style.color = "#ef4444"; }
            }
        }).catch(function () {
            if (status) { status.textContent = "Connection error. Please try again."; status.style.color = "#ef4444"; }
        });
    }

    function helpNav(tab) {
        HELP_STATE.tab = tab;
        var tabs = document.querySelectorAll(".vm-help-tab");
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle("active", tabs[i].getAttribute("data-tab") === tab);
        }
        helpRender();
    }

    function helpSearch(q) {
        HELP_STATE.query = q || "";
        helpRender();
    }

    function helpRender() {
        var body = document.getElementById("vmHelpBody");
        if (!body) return;
        var q = HELP_STATE.query;
        var searchBar = '<input id="vmHelpSearch" type="text" placeholder="Search tutorials and documentation..." value="' +
            q.replace(/"/g, "&quot;") + '" oninput="vmHelpSearch(this.value)" style="width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:11px 14px;color:#e2e8f0;font-size:13px;outline:none;font-family:\'Inter\',sans-serif;box-sizing:border-box;margin-bottom:16px;" placeholder="Search tutorials and documentation...">';

        if (HELP_STATE.tab === "tutorials") {
            var guides = helpFilteredGuides();
            var listHtml = guides.length ? guides.map(function (g) {
                var seen = window.vmGuideSeen(g.key);
                return '<div style="display:flex;align-items:center;gap:12px;padding:12px 14px;background:rgba(15,23,42,0.5);border:1px solid rgba(255,255,255,0.06);border-radius:12px;margin-bottom:8px;">' +
                    '<div style="width:36px;height:36px;border-radius:10px;background:rgba(0,212,255,0.1);display:flex;align-items:center;justify-content:center;color:#00d4ff;flex-shrink:0;"><span class="material-icons" style="font-size:18px;">' + (g.icon || "info") + '</span></div>' +
                    '<div style="flex:1;min-width:0;"><span style="color:#e2e8f0;font-size:13px;font-weight:700;display:block;">' + g.title + '</span>' +
                    '<span style="color:#64748b;font-size:11px;">' + (seen ? "Watched" : "Not watched yet") + '</span></div>' +
                    '<button type="button" onclick="vmGuideForce(\'' + g.key + '\');vmHelpCenter.close()" style="border:1px solid rgba(0,212,255,0.35);background:rgba(0,212,255,0.1);color:#00d4ff;border-radius:8px;padding:6px 14px;cursor:pointer;font-size:11px;font-weight:700;font-family:\'Inter\',sans-serif;">Replay</button>' +
                    '</div>';
            }).join("") : '<p style="color:#64748b;font-size:12px;text-align:center;padding:18px 0;">No tutorials match your search.</p>';

            body.innerHTML = searchBar +
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
                    '<span style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;">Feature walkthroughs</span>' +
                    '<button type="button" onclick="vmGuideReset();helpRender()" style="background:none;border:1px solid rgba(255,255,255,0.12);color:#94a3b8;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:10px;font-family:\'Inter\',sans-serif;">Reset all tutorials</button>' +
                '</div>' + listHtml;
        } else if (HELP_STATE.tab === "docs") {
            var articles = helpFilteredArticles();
            var docsHtml = articles.length ? articles.map(function (a) {
                return '<div style="background:rgba(15,23,42,0.5);border:1px solid rgba(255,255,255,0.06);border-radius:12px;margin-bottom:8px;overflow:hidden;">' +
                    '<button type="button" onclick="vmHelpToggleDoc(this)" style="width:100%;display:flex;align-items:center;justify-content:space-between;padding:13px 14px;background:none;border:none;color:#e2e8f0;font-size:13px;font-weight:600;cursor:pointer;text-align:left;font-family:\'Inter\',sans-serif;">' +
                        '<span>' + a.title + '</span><span class="vm-help-doc-caret" style="color:#00d4ff;transition:transform 0.2s;">&#9662;</span>' +
                    '</button>' +
                    '<div class="vm-help-doc-body" style="display:none;padding:0 14px 14px;color:#94a3b8;font-size:12.5px;line-height:1.6;">' + a.html + '</div>' +
                    '</div>';
            }).join("") : '<p style="color:#64748b;font-size:12px;text-align:center;padding:18px 0;">No documentation matches your search.</p>';

            body.innerHTML = searchBar +
                '<div style="margin-bottom:12px;"><span style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.12em;">Documentation</span></div>' + docsHtml;
        } else {
            // Support / Bugs / Requests
            var titles = {
                support: "Contact Support",
                bug: "Report a Bug",
                request: "Submit a Feature Request"
            };
            var labels = {
                support: "How can we help? Tell us what you need.",
                bug: "What went wrong? Include steps to reproduce it.",
                request: "What would make ValleyMind better for you?"
            };
            var ids = { support: "helpSupport", bug: "helpBug", request: "helpRequest" };
            var kinds = { support: "support", bug: "bug", request: "request" };
            body.innerHTML =
                '<h3 style="color:#f1f5f9;font-size:15px;font-weight:700;margin:0 0 6px;">' + titles[HELP_STATE.tab] + '</h3>' +
                '<p style="color:#64748b;font-size:12px;margin:0 0 12px;">' + labels[HELP_STATE.tab] + '</p>' +
                '<textarea id="' + ids[HELP_STATE.tab] + '" rows="5" placeholder="Write your message..." style="width:100%;background:rgba(15,23,42,0.85);border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:12px 14px;color:#e2e8f0;font-size:13px;outline:none;resize:vertical;box-sizing:border-box;font-family:\'Inter\',sans-serif;"></textarea>' +
                '<div style="margin-top:10px;display:flex;align-items:center;gap:10px;">' +
                    '<button type="button" onclick="vmHelpSubmit(\'' + kinds[HELP_STATE.tab] + '\')" style="border:none;border-radius:10px;background:#00d4ff;color:#003642;font-weight:800;padding:10px 22px;cursor:pointer;text-transform:uppercase;letter-spacing:0.08em;font-family:\'Space Grotesk\',sans-serif;font-size:11px;">Send</button>' +
                    '<span id="' + ids[HELP_STATE.tab] + 'Status" style="font-size:11px;color:#94a3b8;"></span>' +
                '</div>';
        }
        if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
        if (typeof window.applyIconFallback === "function") window.applyIconFallback();
    }

    function helpOpen() {
        var overlay = document.getElementById("vmHelpOverlay");
        if (!overlay) return;
        overlay.style.display = "flex";
        HELP_STATE.tab = HELP_STATE.tab || "tutorials";
        HELP_STATE.query = "";
        var tabs = document.querySelectorAll(".vm-help-tab");
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle("active", tabs[i].getAttribute("data-tab") === HELP_STATE.tab);
        }
        helpRender();
    }

    function helpClose() {
        var overlay = document.getElementById("vmHelpOverlay");
        if (overlay) overlay.style.display = "none";
    }

    window.vmHelpCenter = { open: helpOpen, close: helpClose, tab: helpNav };
    window.vmHelpSubmit = helpSubmit;
    window.vmHelpSearch = helpSearch;
    window.vmHelpToggleDoc = function (btn) {
        var body = btn.parentNode.querySelector(".vm-help-doc-body");
        var caret = btn.querySelector(".vm-help-doc-caret");
        if (body) {
            var open = body.style.display !== "none";
            body.style.display = open ? "none" : "block";
            if (caret) caret.style.transform = open ? "" : "rotate(180deg)";
        }
    };
})();
