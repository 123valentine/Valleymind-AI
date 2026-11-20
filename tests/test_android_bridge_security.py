"""Native-bridge security and compatibility tests.

These are structural checks against the REAL Kotlin/JS sources plus a Node
execution of the injected shim. They do not replace an Android build or device
verification, but they lock in:

  * call-time origin guards on every @JavascriptInterface method,
  * a release-locked production origin,
  * navigation/WebView hardening,
  * the additive-only bridge contract and its manifest,
  * validated overlay-region resizing.

Run with:
  env311\\Scripts\\python.exe -m pytest tests/test_android_bridge_security.py -v
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
NATIVE_CONTRACT_RUNNER = ROOT / "tests" / "_android_native_contract_test.js"

PRODUCTION_HOST = "valleymind-ai-opms.onrender.com"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _kotlin_list(source, name):
    match = re.search(name + r"\s*=\s*listOf\((.*?)\)", source, re.S)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


class OriginLockTests(unittest.TestCase):
    """The release build can only ever trust its one production origin."""

    def test_pure_policy_sources_exist(self):
        for rel in (
            "android/app/src/main/java/ai/valleymind/companion/TrustedOrigin.kt",
            "android/app/src/main/java/ai/valleymind/companion/ServerPolicy.kt",
            "android/app/src/main/java/ai/valleymind/companion/OverlayRegion.kt",
            "android/app/src/main/java/ai/valleymind/companion/BridgeContract.kt",
            "android/app/src/main/java/ai/valleymind/companion/TtsCallback.kt",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_the_release_origin_has_one_source(self):
        # The production host may only live in build.gradle (and deployment
        # manifests/tests). Native code derives it from BuildConfig.
        for rel in (
            "android/app/src/main/java/ai/valleymind/companion/MainActivity.kt",
            "android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt",
            "android/app/src/main/java/ai/valleymind/companion/TrustedOrigin.kt",
            "android/app/src/main/java/ai/valleymind/companion/ServerPolicy.kt",
            "android/app/src/main/res/values/strings.xml",
        ):
            self.assertNotIn(PRODUCTION_HOST, _read(rel), f"{rel} hardcodes the release origin")

    def test_activity_locks_release_builds(self):
        act = _read("android/app/src/main/java/ai/valleymind/companion/MainActivity.kt")
        self.assertIn("ServerPolicy.resolve", act)
        self.assertIn("ServerPolicy.hasStoredOverrideToClear", act)
        self.assertIn("ServerPolicy.mayChangeServer", act)
        self.assertIn("clearBaseUrlOverride", act)
        self.assertIn("visibility = if (ServerPolicy.mayChangeServer", act)

    def test_service_cannot_be_pointed_at_another_origin(self):
        svc = _read("android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt")
        self.assertIn("ServerPolicy.resolve", svc)
        self.assertIn("DEFAULT_BASE_URL", svc)

    def test_backup_is_disabled_and_webview_state_is_excluded(self):
        manifest = _read("android/app/src/main/AndroidManifest.xml")
        self.assertIn('android:allowBackup="false"', manifest)
        for rel in (
            "android/app/src/main/res/xml/backup_rules.xml",
            "android/app/src/main/res/xml/data_extraction_rules.xml",
        ):
            rules = _read(rel)
            self.assertIn('path="app_webview"', rules, f"{rel} does not exclude WebView state")


class BridgeGuardTests(unittest.TestCase):
    """Every privileged call re-checks the current document origin."""

    def test_bridge_checks_trust_for_every_javascript_interface_method(self):
        bridge = _read("android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt")
        methods = re.findall(r"@JavascriptInterface\s+fun\s+(\w+)", bridge)
        self.assertGreaterEqual(len(methods), 16, methods)
        # Each method body contains a guard; count references to be robust to
        # formatting while still failing if a method is left unguarded.
        self.assertGreaterEqual(bridge.count("host.isTrustedPage()"), len(methods), methods)
        self.assertIn("fun isTrustedPage(): Boolean = false", bridge)
        self.assertIn("fun contractJson(): String", bridge)
        self.assertIn("fun setOverlayRegion(widthDp: Double, heightDp: Double): String", bridge)

    def test_activity_blocks_foreign_navigation_and_hardens_the_webview(self):
        act = _read("android/app/src/main/java/ai/valleymind/companion/MainActivity.kt")
        for needle in (
            "override fun shouldOverrideUrlLoading",
            "updateTrustedPage",
            "ServerPolicy.isTrustedPage",
            "openExternal",
            "Intent.ACTION_VIEW",
            "MIXED_CONTENT_NEVER_ALLOW",
            "allowFileAccess = false",
            "allowContentAccess = false",
            "javaScriptCanOpenWindowsAutomatically = false",
            "onShowFileChooser",
        ):
            self.assertIn(needle, act, f"MainActivity is missing {needle}")
        self.assertNotIn("javaScriptCanOpenWindowsAutomatically = true", act)

    def test_overlay_webview_is_hardened_and_trust_is_url_based(self):
        svc = _read("android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt")
        for needle in (
            "trustedPage",
            "override fun isTrustedPage",
            "OverlayPageVerifier.isCompanionPageUrl",
            "MIXED_CONTENT_NEVER_ALLOW",
            "javaScriptCanOpenWindowsAutomatically = false",
            "allowFileAccess = false",
            "allowContentAccess = false",
            "OverlayRegionRules.decide",
            "applyOverlayRegion",
        ):
            self.assertIn(needle, svc, f"CompanionOverlayService is missing {needle}")

    def test_untrusted_subframes_and_permission_requests_are_blocked(self):
        act = _read("android/app/src/main/java/ai/valleymind/companion/MainActivity.kt")
        svc = _read("android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt")
        self.assertIn("if (request?.isForMainFrame != true) return !trusted", act)
        self.assertIn("request.origin", act)
        self.assertIn("trustedPage && ServerPolicy.isTrustedPage", act)
        self.assertIn("request.origin", svc)
        self.assertIn("trustedPage && ServerPolicy.isTrustedPage", svc)

    def test_tts_callback_names_are_validated_before_js_evaluation(self):
        bridge = _read("android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt")
        act = _read("android/app/src/main/java/ai/valleymind/companion/MainActivity.kt")
        svc = _read("android/app/src/main/java/ai/valleymind/companion/CompanionOverlayService.kt")
        helper = _read("android/app/src/main/java/ai/valleymind/companion/TtsCallback.kt")
        registry = _read("android/app/src/main/java/ai/valleymind/companion/NativeTtsRegistry.kt")
        self.assertIn("TtsCallback.isValid", bridge)
        self.assertIn("TtsCallback.doneJs", act)
        self.assertIn("TtsCallback.doneJs", svc)
        self.assertIn("TtsCallback.isValid", registry)
        self.assertIn('Regex("^vmTts[0-9]{1,9}$")', helper)
        self.assertNotIn('"window.__vmTtsDone_" + name', act)
        self.assertNotIn('"window.__vmTtsDone_" + name', svc)

    def test_untrusted_calls_get_inert_payloads(self):
        bridge = _read("android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt")
        self.assertIn("BridgeContract.untrustedJson()", bridge)
        self.assertIn("untrusted-origin", bridge)
        self.assertIn("UNTRUSTED_STATE", bridge)
        self.assertIn("granted", bridge)

    def test_shim_exposes_the_versioned_facade_without_breaking_legacy(self):
        js = _read("android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt")
        for needle in (
            "window.vmCompanionAndroid",
            "window.vmNative",
            "readContract",
            "legacyCapabilities",
            "overlayRegion",
            "setOverlayRegion",
        ):
            self.assertIn(needle, js, f"the shim is missing {needle}")


class ContractManifestTests(unittest.TestCase):
    """The manifest, Kotlin constants and JS sources must agree."""

    def setUp(self):
        self.manifest = json.loads(_read("android/bridge-contract.json"))
        self.bridge = _read("android/app/src/main/java/ai/valleymind/companion/CompanionBridge.kt")
        self.shim = _read("android/app/src/main/java/ai/valleymind/companion/AndroidBridgeJs.kt")
        self.contract_kt = _read("android/app/src/main/java/ai/valleymind/companion/BridgeContract.kt")

    def test_version_matches(self):
        self.assertEqual(2, self.manifest["version"])
        self.assertEqual(2, int(re.search(r"const val VERSION = (\d+)", self.contract_kt).group(1)))
        self.assertEqual("additive-only", self.manifest["compatibility"])

    def test_native_methods_exist_in_the_bridge(self):
        for method in self.manifest["nativeMethods"]:
            self.assertIn(f"fun {method}(", self.bridge, f"bridge is missing {method}")
        self.assertEqual(
            set(_kotlin_list(self.contract_kt, "NATIVE_METHODS")),
            set(self.manifest["nativeMethods"]),
        )

    def test_shim_methods_exist_in_the_injected_js(self):
        for method in self.manifest["shimMethods"]:
            self.assertIn(method, self.shim, f"the shim is missing {method}")
        self.assertEqual(
            set(_kotlin_list(self.contract_kt, "SHIM_METHODS")),
            set(self.manifest["shimMethods"]),
        )

    def test_capabilities_match(self):
        self.assertEqual(
            _kotlin_list(self.contract_kt, "CAPABILITIES"),
            self.manifest["capabilities"],
        )

    def test_events_and_overlay_markers_match(self):
        self.assertEqual(_kotlin_list(self.contract_kt, "EVENTS"), self.manifest["events"])
        self.assertEqual(
            _kotlin_list(self.contract_kt, "OVERLAY_MARKERS"),
            self.manifest["overlayMarkers"],
        )
        html = _read("static/companion_overlay.html")
        for marker in self.manifest["overlayMarkers"]:
            self.assertIn(f'id="{marker}"', html, f"overlay page lost #{marker}")


class OverlayRegionWebTests(unittest.TestCase):
    """The page opts into the compact region only when the shell supports it."""

    def test_helper_exists_and_is_loaded_before_the_overlay_module(self):
        self.assertTrue((ROOT / "static" / "native_bridge.js").is_file())
        html = _read("static/companion_overlay.html")
        helper = html.find("/static/native_bridge.js")
        module = html.find("/static/companion_overlay.js")
        self.assertGreater(helper, -1, "the overlay page does not load native_bridge.js")
        self.assertGreater(module, helper, "the helper must load before the overlay module")

    def test_overlay_requests_a_bounded_region_and_reframes(self):
        js = _read("static/companion_overlay.js")
        self.assertIn("requestCompactOverlayRegion", js)
        self.assertIn("VMNative", js)
        self.assertIn("overlayRegion.supported", js)
        self.assertIn("overlayRegion.set", js)
        self.assertIn("frameFullBody", js)

    @unittest.skipIf(NODE is None, "node is not available")
    def test_native_contract_node_runner_passes(self):
        proc = subprocess.run(
            [NODE, str(NATIVE_CONTRACT_RUNNER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            msg="native contract runner exited non-zero\nstdout:\n%s\nstderr:\n%s"
                % (proc.stdout, proc.stderr),
        )
        summary = json.loads(proc.stdout.splitlines()[-1])
        self.assertEqual(summary["failed"], [], summary)
        self.assertGreater(summary["passed"], 0, "native contract runner asserted nothing")


if __name__ == "__main__":
    unittest.main()
