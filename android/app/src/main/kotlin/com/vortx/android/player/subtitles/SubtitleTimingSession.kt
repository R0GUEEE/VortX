package com.vortx.android.player.subtitles

/**
 * One source-session's constant subtitle offset, not speech/FPS/drift alignment. The host owns current
 * source/profile/account admission and live engine support. This policy has no Android classes or IO.
 */
internal class SubtitleTimingSession(savedManualSeconds: Double?) {
    private var seeded = SubtitleAutoResync.resolve(savedManualSeconds, null, false).seeded
    private var retired = false

    @Synchronized fun retire() { retired = true }

    /** At most one community application per source scope; a saved/manual decision always wins. */
    fun applyCommunity(
        poolOffsetMs: Int?,
        delaySupported: Boolean,
        admit: ((() -> Boolean) -> Boolean),
        apply: (Double) -> Unit,
    ): Boolean {
        if (!delaySupported) return false // Media3 has no supported live subtitle-delay operation.
        return admit {
            synchronized(this) {
                if (retired || seeded) return@synchronized false
                val resolved = SubtitleAutoResync.resolve(null, poolOffsetMs, alreadySeeded = false)
                if (resolved.source != SubtitleAutoResync.OffsetSource.COMMUNITY) return@synchronized false
                apply(resolved.seconds)
                seeded = true
                true
            }
        }
    }

    /** Zero reset seeds immediately, even though the existing durable memory deliberately forgets zero. */
    fun applyManual(
        seconds: Double,
        delaySupported: Boolean,
        admit: ((() -> Boolean) -> Boolean),
        apply: (Double) -> Unit,
    ): Boolean {
        if (!delaySupported || !SubtitleAutoResync.captureIsValid(seconds)) return false
        return admit {
            synchronized(this) {
                if (retired) return@synchronized false
                val resolved = SubtitleAutoResync.resolve(seconds, null, alreadySeeded = false)
                apply(resolved.seconds)
                seeded = true
                true
            }
        }
    }
}

/** Immutable identity used by both the actual pool request and the completion applying its offset. */
internal data class SubtitlePoolPlaybackQuery(val contentKey: String, val fingerprint: String)

internal fun subtitlePoolPlaybackQuery(
    contentKey: String?,
    durationMs: Long,
    releaseName: String?,
): SubtitlePoolPlaybackQuery? {
    if (contentKey.isNullOrBlank() || durationMs <= 0L) return null
    return SubtitlePoolPlaybackQuery(contentKey, SubtitleReleaseFingerprint.releaseFingerprint(
        durationSecs = durationMs / 1000.0, releaseName = releaseName))
}
