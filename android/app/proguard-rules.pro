# Keep the JS bridge interface names (they are called reflectively from JS).
-keepclassmembers class ai.valleymind.companion.CompanionBridge {
    public *;
}
-keep class ai.valleymind.companion.CompanionBridge
