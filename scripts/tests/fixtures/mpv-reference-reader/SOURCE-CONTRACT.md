# Genuine finite-reference regression source

These literal C sources preserve the existing controlled-loopback regression.
They are source enrollment only. There is no standalone build or runtime gate in
this directory, and placing them does not certify a native component or SDK.

The main has 39 expected outcome labels: seven actual owned-text corpus checks,
one real-client/global readiness check, 21 HTTP framing/entity cases and ten real
demux cases. It is a case count, not an assertion count. All prior assertions,
ordinary failure checks, body-poll observations and cleanup order are unchanged.

## Source-relative inputs

Keep these files together and add this directory as an include directory.
Compile five test translation units: http_fixture_adapter.c,
tcp_fixture_adapter.c, mpv_fixture_adapter.c, genuine_reader_fixture.c and
fixture-owned-text-ties.c. The other three files are their literal headers.

The HTTP and TCP adapters must consume the actual matching FFmpeg source files.
They have no fallback source path. With the FFmpeg source root on the compiler
include path, the source-relative macro values are:

    -DGENUINE_HTTP_SOURCE='"libavformat/http.c"'
    -DGENUINE_TCP_SOURCE='"libavformat/tcp.c"'

Supply only the HTTP macro to its adapter and the TCP macro to its adapter.
Force-include native_cue_fixture_decl.h for fixture-owned-text-ties.c so its
exported test helper has the same declared signature as the main. Add the real
MPV source/include/build directories and FFmpeg source/build directories from the
matching producer's own compile metadata. Do not copy generated configuration
headers, invent a replacement URLContext/AVIO/MPContext layout, or use host
pkg-config fallback to make an unrelated component compile.

The HTTP adapter includes the complete HTTP translation unit, delegates its real
reference-read body and reinstalls its fixture assertions after all headers.
The TCP adapter includes the complete TCP translation unit and delegates the real
network wait. The MPV adapter calls genuine demux request/consumer APIs; it does
not include or replace a production MPV translation unit. No production test
API or fake transport implementation is needed.

## Required matched-producer enrollment

A future maintained native producer must first supply a complete compatible host
build of the exact finite-reader FFmpeg and MPV source profile, its generated
headers and its actual dependency/link graph. This is an internal POSIX native
test: an opaque app SDK, stripped shared library, mobile framework or AAR alone
does not supply the required internal symbols and layout evidence. A host result
does not establish Apple mobile or Android device acceptance.

Starting from fresh matched static libraries, build the five fixture TUs with
the corresponding real compiler flags, SDK, OS floor, feature flags and headers.
Create a libavformat derivative omitting exactly the original HTTP and TCP
object owners, then add the two fresh whole-TU adapters. Keep the other matching
production AVIO/network/TLS/codec objects. The three remaining fixture TUs link
against the matching complete libmpv and genuine codec/converter/libass
dependencies; they require no fabricated MPV manager or replacement main.

Before execution, retain the exact compile commands and consumed headers,
verify unchanged archive payloads, and require a strict link with one owner for
each replaced HTTP/TCP symbol and each test helper. Inspect the complete actual
trace/map and loaded dependency graph. Unknown flags, objects, layout inputs or
fallback libraries are a hold, not an ordering or undefined-symbol waiver.
Historical locally composed archives and receipts cannot certify a new build.

Only after that producer-side build/ownership gate may an operator allocate a
finite single-worker run under a reviewed loopback-only sandbox. The fixture
creates actual ephemeral loopback sockets, validates the received request and
served entity, uses real media mux/demux/decoder code, and joins every worker,
producer and server. Success requires all 39 exact expected labels, zero FAIL
lines, the single normal RESULT and conserved inputs; a timeout is failure.
No arbitrary executable/stdout manifest is accepted as native-build proof here.

The existing INITIAL_REQUEST_ONLY and second-take rejection remain. Real
external destinations/TLS handshakes, public command/OSD lifecycle, Swift and
account admission, full matched platform packages and end-to-end automatic
alignment are separate gates. This source enrollment adds none of those claims.
