package com.vortx.android.engine

import com.vortx.android.profile.NativeProfileGateway

internal data class VortxNativeOwner(val epoch: Long, val profileID: String)
internal data class VortxNativeRead(val owner: VortxNativeOwner, val projected: NativeProfileGateway.Projection)

// Synthetic collaborator only. The real Session has a synchronized read and the owned method below.
internal class VortxNativeSession(
    var owner: VortxNativeOwner,
    private var projected: NativeProfileGateway.Projection,
) {
    @Volatile var beforeReadLock: (() -> Unit)? = null
    fun read(): VortxNativeRead {
        beforeReadLock?.invoke() // Instrumentation announces the attempt before blocking on Session.
        return synchronized(this) { VortxNativeRead(owner, projected) }
    }
    @Synchronized fun replaceProjection(value: NativeProfileGateway.Projection) { projected = value }
    @Synchronized fun accepts(expected: VortxNativeOwner): Boolean = owner == expected

    // Exact actual synchronized owned method; accepts/read payloads remain synthetic collaborators.
    @Synchronized fun <T> owned(owner: VortxNativeOwner, action: () -> T): T {
        check(accepts(owner)) { "Native owner changed" }; return action()
    }
}
