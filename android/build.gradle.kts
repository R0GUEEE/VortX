// Plugin versions are centralized in gradle/libs.versions.toml.
// AGP 9 supplies built-in Kotlin; the separately versioned Compose plugin matches Kotlin 2.4.21.
// Do not also apply org.jetbrains.kotlin.android to either Android module.
plugins {
    alias(libs.plugins.android.application) apply false
    // :mpv-seam (the W1-B source-built libmpv JNI seam) is an Android library module; same AGP pin.
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.compose) apply false
}

// One native ABI contract for every Android module. The app, Rust cargo-ndk tasks, source-built
// libmpv seam, CI toolchain installer, and APK verification gates must all carry this same set.
// armeabi-v7a is required by 32-bit Fire TV hardware; x86 stays intentionally unsupported.
extra["vortxAndroidAbis"] = listOf("arm64-v8a", "armeabi-v7a", "x86_64")
