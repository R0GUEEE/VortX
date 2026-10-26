import Foundation
import Darwin
import Libmpv
import os

// Compile with the literal production cancellation/transport declarations and actual Timing and
// Argument declarations. This finite node fixture does not emulate a core, network or renderer.
private indirect enum NodeSpec {
    case integer(Int64), number(Double), flag(Bool), array([NodeSpec]), map([(String, NodeSpec)])
}
private final class NodeArena {
    private var strings: [UnsafeMutablePointer<CChar>] = []
    private var lists: [UnsafeMutablePointer<mpv_node_list>] = []
    private var values: [(UnsafeMutablePointer<mpv_node>, Int)] = []
    private var keys: [(UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>, Int)] = []
    func node(_ spec: NodeSpec) -> mpv_node {
        var node = mpv_node()
        switch spec {
        case .integer(let value): node.format = MPV_FORMAT_INT64; node.u.int64 = value
        case .number(let value): node.format = MPV_FORMAT_DOUBLE; node.u.double_ = value
        case .flag(let value): node.format = MPV_FORMAT_FLAG; node.u.flag = value ? 1 : 0
        case .array(let rows):
            node.format = MPV_FORMAT_NODE_ARRAY
            node.u.list = list(rows, names: nil)
        case .map(let rows):
            node.format = MPV_FORMAT_NODE_MAP
            node.u.list = list(rows.map(\.1), names: rows.map(\.0))
        }
        return node
    }
    private func list(_ rows: [NodeSpec], names: [String]?) -> UnsafeMutablePointer<mpv_node_list> {
        let buffer = UnsafeMutablePointer<mpv_node>.allocate(capacity: rows.count)
        for (index, row) in rows.enumerated() { buffer.advanced(by: index).initialize(to: node(row)) }
        values.append((buffer, rows.count))
        var keyBuffer: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?
        if let names {
            let allocated = UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>.allocate(capacity: names.count)
            for (index, name) in names.enumerated() {
                let value = strdup(name)!
                strings.append(value); allocated.advanced(by: index).initialize(to: value)
            }
            keys.append((allocated, names.count)); keyBuffer = allocated
        }
        let list = UnsafeMutablePointer<mpv_node_list>.allocate(capacity: 1)
        list.initialize(to: .init(num: Int32(rows.count), values: buffer, keys: keyBuffer))
        lists.append(list); return list
    }
    deinit {
        for value in strings { free(value) }
        for (pointer, count) in keys { pointer.deinitialize(count: count); pointer.deallocate() }
        for (pointer, count) in values { pointer.deinitialize(count: count); pointer.deallocate() }
        for pointer in lists { pointer.deinitialize(count: 1); pointer.deallocate() }
    }
}

@main private enum MPVRemoteReferenceTransportTests {
    private static let capture = MPVRemoteReferenceTransport.Capture(intent: 8, entry: 41, sid: 2, stream: 37, origin: 100)
    private static func fields() -> [(String, NodeSpec)] {
        let starts: [Int64] = [1_000, 1_000, 2_000, 3_000, 4_000, 5_000, 6_000, 7_000]
        return [("version", .integer(1)), ("grant", .integer(11)),
            ("intent-generation", .integer(8)), ("entry-id", .integer(41)), ("sid", .integer(2)),
            ("ff-index", .integer(37)), ("demux-origin", .number(100)), ("primary-visible", .flag(true)),
            ("source-generation", .integer(7)), ("response-generation", .integer(9)),
            ("source-epoch", .integer(10)), ("demux-epoch", .integer(12)), ("origin", .number(100)),
            ("prefix-start-ms", .integer(0)), ("prefix-end-ms", .integer(1_080_000)),
            ("cues", .array(starts.map { .array([.integer($0), .integer($0 + 300_000)]) }))]
    }
    private static func decode(_ fields: [(String, NodeSpec)], _ operation: MPVRemoteReferenceTransport.Operation? = nil)
        -> MPVRemoteReferenceTransport.Reply {
        let arena = NodeArena()
        return MPVRemoteReferenceTransport.decode(arena.node(.map(fields)), operation: operation ?? .read(capture))
    }
    private static func refused(_ fields: [(String, NodeSpec)]) {
        guard case .unconfirmed = decode(fields) else { preconditionFailure("Malformed reference receipt was accepted") }
    }
    private static func run() async {
        guard case .read(let original) = decode(fields()) else { preconditionFailure("Valid receipt refused") }
        precondition(original.grant == 11 && original.cues.count == 8)
        precondition(original.cues[0].startMs == original.cues[1].startMs)
        precondition(original.cues[0].endMs - original.cues[0].startMs == 300_000)
        precondition(original.capture == capture && original.response == 9 && original.demuxEpoch == 12)
        for count in [1, 7] {
            var shortPrefix = fields()
            shortPrefix[15].1 = .array((0..<count).map { .array([.integer(Int64($0)), .integer(Int64($0 + 1))]) })
            guard case .read(let retained) = decode(shortPrefix) else { preconditionFailure("Valid short-prefix grant lost") }
            precondition(retained.grant == original.grant && retained.cues.count == count)
            // Transport retains exact cleanup authority; the genuine query's >=8 evidence
            // guard separately refuses alignment and caller revokes this known grant.
        }
        for name in ["version", "grant", "entry-id", "sid", "source-generation", "source-epoch", "demux-epoch"] {
            var changed = fields()
            let index = changed.firstIndex { $0.0 == name }!
            changed[index].1 = .integer(0)
            refused(changed)
        }
        var changed = fields(); changed[2].1 = .integer(9); refused(changed) // Original intent ABA fence.
        changed = fields(); changed[6].1 = .number(.nan); refused(changed)
        changed = fields(); changed[7].1 = .integer(1); refused(changed) // No numeric boolean coercion.
        changed = fields(); changed[13].1 = .integer(1); refused(changed) // Missing original prefix.
        changed = fields(); changed[14].1 = .integer(1_079_999); refused(changed)
        changed = fields(); changed[15].1 = .array([]); refused(changed)
        changed = fields(); changed[15].1 = .array((0..<8).map { .array([.integer(Int64(8 - $0)), .integer(50)]) }); refused(changed)
        changed = fields(); changed[15].1 = .array((0..<8).map { .array([.integer(Int64($0)), .integer(1_080_001)]) }); refused(changed)
        changed = fields(); changed[1].0 = "version"; refused(changed)
        changed = fields(); changed.append(("unexpected", .integer(1))); refused(changed)
        let ready: [(String, NodeSpec)] = [("version", .integer(1)), ("grant", .integer(11)), ("ticket", .integer(19))]
        guard case .prepared(let grant, let ticket) = decode(ready, .prepare(original)) else { preconditionFailure("Prepare receipt refused") }
        precondition(grant == 11 && ticket == 19)
        var wrong = ready; wrong[1].1 = .integer(12)
        guard case .unconfirmed = decode(wrong, .prepare(original)) else { preconditionFailure("Wrong grant accepted") }
        let committed = ready + [("committed", .flag(true))]
        guard case .committed = decode(committed, .commit(original, ticket: 19)) else { preconditionFailure("Commit receipt refused") }
        guard case .unconfirmed = decode(committed, .commit(original, ticket: 20)) else { preconditionFailure("Wrong ticket accepted") }

        let events = OSAllocatedUnfairLock(initialState: [Int]())
        let cancel = MPVRemoteReferenceCancellation()
        cancel.install(1) { events.withLock { $0.append(1) }; cancel.clear(1) } // Reentrant clear must not hold its lock.
        cancel.install(2) { events.withLock { $0.append(2) } }
        cancel.clear(2)
        cancel.cancel(); cancel.cancel()
        precondition(cancel.isCancelled && events.withLock { $0 } == [1])
        cancel.install(3) { events.withLock { $0.append(3) } }
        precondition(events.withLock { $0 } == [1, 3])
        let a1 = MPVRemoteReferenceCancellation(), a2 = MPVRemoteReferenceCancellation(), a3 = MPVRemoteReferenceCancellation()
        a2.setPredecessor(a1); a3.setPredecessor(a2)
        a2.cancel(); a3.complete(); a3.complete(); a2.complete()
        // Completion is an explicit terminal action, distinct from cancellation. No timeout is
        // interpreted as success; all three real production latches must eventually close.
        a1.complete()
        await a3.waitForCompletion(); await a2.waitForCompletion(); await a1.waitForCompletion()
        print("PASS remote scalar receipt conservation and original cancellation callback ownership; controlled nodes only")
    }
    static func main() async { await Task.detached(priority: .utility) { await run() }.value }
}
