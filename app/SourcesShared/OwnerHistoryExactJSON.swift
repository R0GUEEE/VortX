import Foundation

/// Exact JSON value slices. Native source numbers never pass through NSNumber/Double/Int64.
/// Parsing is cold/source-transition work; a retained ownerHistory slice is reused verbatim.
enum OwnerHistoryExactJSON {
    enum Failure: Error { case malformed, excessive, missing, contradictory }
    static let maximumBytes = 32 * 1024 * 1024
    struct Object: Sendable {
        private(set) var fields: [String: Data]
        init(_ bytes: Data) throws {
            var parser = Parser(bytes)
            fields = try parser.objectFields()
            try parser.end()
        }
        fileprivate init(fields: [String: Data]) { self.fields = fields }
        subscript(_ key: String) -> Data? { fields[key] }
        mutating func set(_ key: String, _ value: Data) { fields[key] = value }
        mutating func remove(_ key: String) { fields.removeValue(forKey: key) }
        func required(_ key: String) throws -> Data {
            guard let bytes = fields[key] else { throw Failure.missing }; return bytes
        }
        func text(_ key: String) throws -> String {
            try JSONDecoder().decode(String.self, from: required(key))
        }
        func encoded() throws -> Data {
            var result = Data([123])
            for (offset, key) in fields.keys.sorted().enumerated() {
                if offset != 0 { result.append(44) }
                result.append(try JSONEncoder().encode(key)); result.append(58)
                result.append(fields[key]!)
            }
            result.append(125)
            guard result.count <= maximumBytes else { throw Failure.excessive }
            return result
        }
    }
    static func array(_ bytes: Data) throws -> [Data] {
        var p = Parser(bytes); let values = try p.arrayValues(); try p.end(); return values
    }
    static func string(_ value: String) throws -> Data { try JSONEncoder().encode(value) }
    static func sameText(_ a: String, _ b: String) -> Bool { Data(a.utf8) == Data(b.utf8) }
    /// Structural comparison without decoding numeric payloads. Use only for small leases or
    /// cold snapshots: this intentionally walks its input. Number tokens, including -0.0, stay
    /// exact; keys/strings are decoded and emitted deterministically, arrays keep order.
    static func canonical(_ bytes: Data) throws -> Data {
        var parser = Parser(bytes)
        parser.whitespace(); let start = parser.at
        try parser.value(); let end = parser.at; try parser.end()
        let value = Data(parser.bytes[start..<end])
        switch value.first {
        case 123:
            var object = try Object(value)
            for (key, field) in object.fields { object.set(key, try canonical(field)) }
            return try object.encoded()
        case 91:
            var result = Data([91])
            for (i, item) in try array(value).enumerated() {
                if i != 0 { result.append(44) }; result.append(try canonical(item))
            }
            result.append(93); return result
        case 34: return try string(JSONDecoder().decode(String.self, from: value))
        default: return value
        }
    }

    private struct Parser {
        let bytes: [UInt8]
        var at = 0
        var depth = 0
        init(_ input: Data) { bytes = Array(input) }
        mutating func whitespace() { while at < bytes.count && [9, 10, 13, 32].contains(bytes[at]) { at += 1 } }
        mutating func end() throws {
            whitespace()
            guard bytes.count <= maximumBytes, at == bytes.count else { throw Failure.malformed }
        }
        mutating func take(_ expected: UInt8) throws {
            whitespace(); guard at < bytes.count, bytes[at] == expected else { throw Failure.malformed }; at += 1
        }
        mutating func objectFields() throws -> [String: Data] {
            guard bytes.count <= maximumBytes, depth < 128 else { throw Failure.excessive }
            depth += 1; defer { depth -= 1 }; try take(123)
            var fields: [String: Data] = [:]
            whitespace()
            if at < bytes.count, bytes[at] == 125 { at += 1; return fields }
            while true {
                whitespace(); let begin = at; try quoted()
                let name = try JSONDecoder().decode(String.self, from: Data(bytes[begin..<at]))
                // Also rejects canonically equivalent duplicate Unicode keys; do not silently
                // collapse them through Swift Dictionary equality.
                guard fields[name] == nil else { throw Failure.malformed }
                try take(58); whitespace(); let valueBegin = at; try value()
                fields[name] = Data(bytes[valueBegin..<at])
                whitespace(); guard at < bytes.count else { throw Failure.malformed }
                if bytes[at] == 125 { at += 1; return fields }
                try take(44)
            }
        }
        mutating func arrayValues() throws -> [Data] {
            guard depth < 128 else { throw Failure.excessive }
            depth += 1; defer { depth -= 1 }; try take(91)
            var result: [Data] = []; whitespace()
            if at < bytes.count, bytes[at] == 93 { at += 1; return result }
            while true {
                whitespace(); let begin = at; try value(); result.append(Data(bytes[begin..<at]))
                whitespace(); guard at < bytes.count else { throw Failure.malformed }
                if bytes[at] == 93 { at += 1; return result }; try take(44)
            }
        }
        mutating func quoted() throws {
            try take(34); let start = at - 1
            while at < bytes.count {
                let byte = bytes[at]; at += 1
                if byte == 34 {
                    _ = try JSONDecoder().decode(String.self, from: Data(bytes[start..<at])); return
                }
                guard byte >= 32 else { throw Failure.malformed }
                if byte == 92 {
                    guard at < bytes.count else { throw Failure.malformed }
                    let escaped = bytes[at]; at += 1
                    if escaped == 117 {
                        guard at + 4 <= bytes.count, bytes[at..<(at + 4)].allSatisfy({
                            (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0)
                        }) else { throw Failure.malformed }
                        at += 4
                    } else if ![34, 47, 92, 98, 102, 110, 114, 116].contains(escaped) { throw Failure.malformed }
                }
            }
            throw Failure.malformed
        }
        mutating func value() throws {
            whitespace(); guard at < bytes.count else { throw Failure.malformed }
            switch bytes[at] {
            case 123: _ = try objectFields()
            case 91: _ = try arrayValues()
            case 34: try quoted()
            case 116: try literal("true")
            case 102: try literal("false")
            case 110: try literal("null")
            default: try number()
            }
        }
        mutating func literal(_ text: String) throws {
            let expected = Array(text.utf8)
            guard at + expected.count <= bytes.count,
                  Array(bytes[at..<(at + expected.count)]) == expected else { throw Failure.malformed }
            at += expected.count
        }
        mutating func number() throws {
            if at < bytes.count, bytes[at] == 45 { at += 1 }
            guard at < bytes.count else { throw Failure.malformed }
            if bytes[at] == 48 { at += 1 }
            else {
                guard (49...57).contains(bytes[at]) else { throw Failure.malformed }
                while at < bytes.count, (48...57).contains(bytes[at]) { at += 1 }
            }
            if at < bytes.count, bytes[at] == 46 {
                at += 1; let start = at
                while at < bytes.count, (48...57).contains(bytes[at]) { at += 1 }
                guard at > start else { throw Failure.malformed }
            }
            if at < bytes.count, bytes[at] == 69 || bytes[at] == 101 {
                at += 1
                if at < bytes.count, bytes[at] == 43 || bytes[at] == 45 { at += 1 }
                let start = at
                while at < bytes.count, (48...57).contains(bytes[at]) { at += 1 }
                guard at > start else { throw Failure.malformed }
            }
        }
    }
}
