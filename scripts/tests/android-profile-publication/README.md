# Android profile publication regression

Run `bash scripts/test-android-profile-publication.sh`. Set `VORTX_TEST_JAVA` if the required Java runtime is not on PATH. Cached Kotlin 2.2.10 and coroutines 1.10.2 are required; this host probe deliberately does not claim current Android dependency composition.

The checker requires byte-identical current-source copies of the publication boundary, access constructor/read, synchronized Session ownership method and complete Continue Watching gate/gateway. It also checks normal publication, pending migration capture, native add routing and both production account-admission constructors. Refresh copied snippets only after reviewing a deliberate source change.

Nine runtime cases exercise the actual boundaries with synthetic session payload, auth/lifecycle admission and store reducer collaborators. They cover concurrent Session/auth ordering, held publication monitors, missing admission, stale projection/runtime/epoch, return identity and exact cancellation/fatal/ordinary failures. Threads are daemonized with bounded latches/joins, and the runner limits JVM processors, memory, compiler workers and priority.

This is not a full preference/migration reducer, secure provider vault, persisted account/key, JNI, Android composition or physical-device test. A passing result does not reproduce or resolve a customer crash by itself.
