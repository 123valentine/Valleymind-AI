"""Dedicated 3D Companion Studio card/workspace and chat-overlay regressions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _html() -> str:
    return (ROOT / "index.html").read_text(encoding="utf-8")


def _companion_js() -> str:
    return (ROOT / "static" / "vrm_companion.js").read_text(encoding="utf-8")


def _settings_js() -> str:
    return (ROOT / "static" / "settings.js").read_text(encoding="utf-8")


def test_studio_tabbar_has_companion_card_next_to_massive_editing():
    html = _html()
    tabbar = html[html.index('<nav class="vm-ws-tabbar"'):html.index('data-ws-panel="studio"')]
    editing = tabbar.index('data-ws="editing"')
    companion = tabbar.index('data-ws="companion"')
    assert "3D Companion" in tabbar
    assert "vmWsGo('companion')" in tabbar
    assert 'class="vm-ws-tab' in tabbar
    # No other tab sits between the two cards.
    between = tabbar[editing + len('data-ws="editing"'):companion]
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
    # Both surfaces read/write one state: Settings toggle + workspace button.
    assert '[data-vm-desktop-toggle]' in settings
    assert 'data-vm-desktop-status' in html
    assert '[data-vm-desktop-status]' in settings


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
