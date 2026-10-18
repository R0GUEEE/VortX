# Watch Stats Swift 6 migration

This standalone package selects Swift 6 and shares the actual production source
through its source symlink. Its private ISO8601 formatters are lock-owned; parsing
retains the original fractional, plain and day-prefix fallback order.

The production file passed strict Swift 6 compilation and eight focused local
source-unit groups, including exact legacy-date and concurrent-call comparisons.
That source unit did not compile this package manifest or run its twelve Swift
Testing cases. Full SwiftPM validation remains open at this incremental milestone.

To run the package suite when allocating a test slot:

```sh
swift test --package-path app/Tests/WatchStatsAggregation --jobs 1
```

This package's language setting does not change the Apple application targets.
Their migration and application/device verification are separate requirements.
