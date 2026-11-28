"""Dedicated 3D Companion Studio stage, real voice/gesture controls, and no
embedded chat/call box. Also locks the untouched Companion Outside App contract.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _html() -> str:
    return (ROOT / "index.html").read_text(encoding="utf-8")


def _companion_js() -> str:
    return (ROOT / "static" / "vrm_companion.js").read_text(encoding="utf-8")


def _settings_js() -> str:
    return (ROOT / "static" / "settings.js").read_text(encoding="utf-8")


def _face_js() -> str:
    return (ROOT / "vrm_preview" / "face.js").read_text(encoding="utf-8")


def test_studio_tabbar_has_companion_card_after_music_studio():
    html = _html()
    tabbar = html[html.index('<nav class="vm-ws-tabbar"'):html.index('data-ws-panel="studio"')]
    music = tabbar.index('data-ws="music"')
    companion = tabbar.index('data-ws="companion"')
    assert "3D Companion" in tabbar
    assert "vmWsGo('companion')" in tabbar
    assert 'class="vm-ws-tab' in tabbar
    assert 'data-ws="editing"' not in tabbar
    # No other tab sits between the two cards.
    between = tabbar[music + len('data-ws="music"'):companion]
    assert 'data-ws="' not in between


def test_companion_workspace_panel_lives_inside_studio_overlay():
    html = _html()
    start = html.index('<div id="studioOverlay">')
    end = html.index("</div><!-- /Studio overlay -->")
    inside = html[start:end]
    assert 'data-ws-panel="companion"' in inside
    assert 'id="vmWsPanelCompanion"' in inside
    assert 'id="vmCompanionStage"' in inside
    assert "Voice + lip-sync" in inside
    assert "vmCompanionStageAnimation(" in inside


def test_vm_ws_go_mounts_and_unmounts_companion_stage():
    html = _html()
    block = html[html.index("function vmWsGo("):]
    assert 'ws === "companion"' in block
    assert "vmCompanionMountWorkspace()" in block
    assert "vmCompanionUnmountWorkspace()" in block
    assert "window.vmCompanionWorkspaceActive" in html
    # The existing shared node is reused, not cloned into a second engine.
    assert 'stage.appendChild(avatar)' in html
    assert 'avatar.classList.add("vm-companion-staged")' in html


def test_embedded_chat_call_box_is_gone_from_every_surface():
    html, js, settings = _html(), _companion_js(), _settings_js()
    removed = (
        'id="vmVrmCompanionChat"', 'id="vmVrmCompanionLog"', 'id="vmVrmCompanionInput"',
        'id="vmVrmCompanionMicBtn"', 'id="vmVrmCompanionCallBtn"',
        'id="vmVrmCompanionEndBtn"', 'id="vmVrmCompanionMuteBtn"',
        'id="vmVrmCompanionScreenBtn"', 'id="vmVrmCompanionSend"',
        'vmvrm-chat', 'vm-companion-chat-open', 'vm-companion-call-active',
    )
    for marker in removed:
        for name, source in (("index", html), ("vrm_companion", js), ("settings", settings)):
            assert marker not in source, f"{name} still carries embedded box marker {marker}"
    # Tapping/stage-tapping cannot resurrect it.
    for marker in ("toggleChat", "collapseChat", "syncChatClearance", "chatOpen"):
        assert marker not in js
        assert marker not in settings
    assert "Tapping the character does nothing" in js
    assert "vmCompanionOpenChat" not in settings
    assert "Open chat" not in settings


def test_six_stage_controls_drive_real_vrm_handlers():
    html, js = _html(), _companion_js()
    for marker in (
        'id="vmCompanionStageVoiceBtn"', 'onclick="vmCompanionStageVoice()"',
        'id="vmCompanionStageMuteBtn"', 'onclick="vmCompanionStageMute()"',
        "vmCompanionStageAnimation('waving')", "vmCompanionStageAnimation('nodding')",
        "vmCompanionStageAnimation('pointing')", "vmCompanionStageAnimation('thinking')",
    ):
        assert marker in html
    voice_edge = html[html.index("function vmCompanionStageVoice()"):html.index("function vmCompanionStageMute()")]
    mute_edge = html[html.index("function vmCompanionStageMute()"):html.index("function vmCompanionStageAnimation(")]
    anim_edge = html[html.index("function vmCompanionStageAnimation("):]
    assert "startStageVoice" in voice_edge
    assert "toggleMute" in mute_edge
    assert ".gesture(" in anim_edge
    assert "function startStageVoice()" in js
    assert "startStageVoice: startStageVoice" in js
    assert "function gesture(name)" in js
    assert "gesture: gesture" in js
    assert "GESTURE_STATUS[raw]" in js
    # Both stage buttons expose a real pressed state, kept in sync by the module.
    assert "function syncStageControls()" in js
    assert "vmCompanionStageVoiceBtn" in js
    assert "vmCompanionStageMuteBtn" in js
    assert "aria-pressed" in js
    assert '"active"' in js


def test_stage_voice_reuses_one_pipeline_and_lip_sync_channel():
    js, face = _companion_js(), _face_js()
    start = js[js.index("function startStageVoice()"):js.index("// ── one-switch auto-wake")]
    for call in ("wireVoice()", "wireNativeSpeech()", "maybePromptAccess()",
                 "nativeAsrAvailable()", "startCall()", "startVoiceSession()"):
        assert call in start, f"stage voice skipped {call}"
    assert "new SpeechRecognition" not in js
    assert "speechSynthesis.speak" not in js
    assert js.count("new Companion(") == 1
    assert js.count("VMCloudVoice.speak(") == 1
    # Real TTS amplitude drives aa/oh/ou through the existing face channel.
    assert "setLevelListener(function (level)" in js
    assert "engine.face.setTalkLevel(level)" in js
    assert "engine.face.setSpeaking(true)" in js
    for morph in ("aa", "ou", "oh"):
        assert f'm.setValue("{morph}", open *' in face
    assert "if (!this.speaking || v <= 0.01)" in face
    # Mute silences both audio owners and closes the mouth gate.
    mute = js[js.index("function toggleMute()"):js.index("function startVoiceSession()")]
    assert "window.VMCloudVoice.stop()" in mute
    assert "stopNativeSpeech()" in mute
    assert "cancelGestureFallback(true)" in mute
    assert "syncControls()" in mute


def test_gestures_use_real_clips_with_immediate_procedural_fallback():
    js = _companion_js()
    assert "const GESTURE_STATUS = {" in js
    assert "const GESTURE_FALLBACKS = {" in js
    for behaviour in ("waving", "nodding", "pointing", "thinking"):
        assert behaviour + ": {" in js
        assert f'"{behaviour}"' in (ROOT / "vrm_preview" / "clips.json").read_text(encoding="utf-8")
    for helper in (
        "function humanBoneNode(", "function gestureClipReady(", "function applyGestureFrame(",
        "function startGestureFallback(", "function playVrmGesture(",
        "function releaseGestureFallback(", "function cancelGestureFallback(",
    ):
        assert helper in js
    assert "engine.trigger(behaviour)" in js
    assert "releaseGestureFallback()" in js
    for lifecycle in ("function stop()", "function hide()", "function cleanup()"):
        block = js[js.index(lifecycle):js.index(lifecycle) + 1400]
        assert "cancelGestureFallback(true)" in block, f"{lifecycle} leaks a bone fallback"


def test_transform_drag_has_no_important_position_or_transform_conflict():
    html = _html()
    staged = html[html.index('#vmVrmCompanion.vm-companion-staged {'):
                  html.index('.vm-companion-stage {', html.index('#vmVrmCompanion.vm-companion-staged {'))]
    assert 'transform: translate3d(var(--vm-stage-x, 0px), var(--vm-stage-y, 0px), 0)' in staged
    assert '!important' not in staged.split('transform: translate3d', 1)[1].split(';', 1)[0]
    # The ID+class rule must beat #vmVrmCompanion's fixed positioning without
    # !important, otherwise the staged avatar stays out of flow.
    staged_compact = staged.replace(' ', '')
    assert 'position:relative;' in staged_compact
    assert 'position:relative!important' not in staged_compact
    assert 'top:auto!important' not in staged_compact
    assert 'left:auto!important' not in staged_compact
    layout = html[html.index('.vm-companion-staged {'):html.index('#vmVrmCompanion.vm-companion-staged {')]
    compact = layout.replace(' ', '')
    assert 'position:relative;' in compact
    assert 'position:relative!important' not in compact
    assert 'top:auto!important' not in compact
    assert 'left:auto!important' not in compact
    base = html[html.index('#vmVrmCompanion {'):html.index('#vmVrmCompanionCanvas {')]
    assert 'position: fixed;' in base
    assert 'position: fixed !important' not in base


def test_pointer_and_touch_drag_are_both_bound_and_non_passive():
    js = _companion_js()
    drag = js[js.index("function wireDrag()"):]
    for event in ("pointerdown", "pointermove", "pointerup", "pointercancel",
                  "touchstart", "touchmove", "touchend", "touchcancel"):
        assert f'"{event}"' in drag, f"wireDrag does not bind {event}"
    assert "{ passive: false }" in drag
    assert "e.preventDefault()" in drag
    assert 'el.addEventListener("touchstart", beginTouch, passiveFalse)' in drag
    assert 'window.addEventListener("touchmove", moveTouch, passiveFalse)' in drag
    assert "const base = stageBaseRect(el)" in drag
    assert "clampStageOffset(" in drag


def test_workspace_outside_app_button_uses_settings_handler_and_state():
    html = _html()
    settings = _settings_js()
    assert 'id="vmCompanionOutsideBtn"' in html
    assert 'data-vm-desktop-toggle="true"' in html
    assert "vmCompanionToggleOutsideFromWorkspace()" in html
    assert "Companion Outside App" in html
    assert "function vmCompanionToggleOutsideFromWorkspace()" in settings
    toggle_fn = settings.split("function vmCompanionToggleOutsideFromWorkspace()", 1)[1].split("\n}", 1)[0]
    assert "vmCompanionSetDesktopVisible(on)" in toggle_fn
    assert "collapseChat" not in toggle_fn
    # Both surfaces read/write one state: Settings toggle + workspace button.
    assert '[data-vm-desktop-toggle]' in settings
    assert 'data-vm-desktop-status' in html
    assert '[data-vm-desktop-status]' in settings
    # Detection and fallback copy are unchanged by the stage refactor.
    assert "data-vm-desktop-state" in settings
    assert 'aria-pressed="false"' in html
    assert "This browser or installed PWA can\\u2019t float the character over other apps. That needs the ValleyMind Android app (system overlay)." in settings
    assert "iOS doesn\\u2019t allow a floating overlay from a browser or PWA. This needs the ValleyMind iOS app." in settings
    assert '"Not running \\u2014 " + VM_DESKTOP_SETUP_HINT' in settings


def test_chat_and_general_studio_never_auto_mount_overlay():
    html = _html()
    js = _companion_js()
    # The auth-time shell dispatch is gated to the dedicated workspace.
    dispatch = html[html.index("window.vmVrmCompanionDispatch()") - 500:
                    html.index("window.vmVrmCompanionDispatch()") + 80]
    assert "vmCompanionWorkspaceActive()" in dispatch
    # The module's own auto-show and dispatch obey the same gate; show() itself
    # refuses to render outside the stage.
    auto_show = js.split("function maybeAutoShow()", 1)[1].split("// ── window hooks", 1)[0]
    assert "companionWorkspaceActive()" in auto_show
    dispatch_js = js.split("window.vmVrmCompanionDispatch = function ()", 1)[1].split("window.vmVrmCompanion = {", 1)[0]
    assert "companionWorkspaceActive()" in dispatch_js
    show = js.split("async function show()", 1)[1].split("function hide()", 1)[0]
    assert "companionWorkspaceActive()" in show
    # Still exactly one engine/body: the workspace reuses the overlay node.
    assert js.count("new Companion(") == 1


def test_studio_hero_subtitle_has_no_encoding_artifact():
    html = _html()
    assert "Your AI creative suite — cut, edit, generate and publish from one place." in html
    assert "Your AI creative suite €” cut" not in html
