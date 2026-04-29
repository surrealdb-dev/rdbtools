# Profile Glossary

Profiles are named bundles of benchmark settings. They exist so a spec can say `workloads: ["iobuf"]` or `machine: "c64r120"` instead of repeating many low-level `db_bench` options.

## Workload Profiles

### `byrx`

Memory-sized RocksDB profile with buffered IO and no compression.

Use it when you want a cache-friendly workload that is not dominated by storage reads.

### `byos`

OS-cache style profile with buffered IO and no compression.

Use it when you want to compare behavior with less emphasis on RocksDB block-cache residency.

### `iobuf`

IO-sized buffered profile, commonly with `lz4`.

Use it for larger datasets where buffered filesystem IO matters.

### `iodir`

IO-sized direct-IO profile, commonly with `lz4`.

Use it when you want to bypass the OS page cache for reads and flush/compaction IO.

### `iodir_none`

Direct-IO profile with no compression.

Use it to isolate storage and LSM behavior without compression CPU cost.

### `iodir_lz4`

Direct-IO profile with `lz4` compression.

This is equivalent to the common `iodir` compression choice, but named explicitly for compression sweeps.

### `iodir_zstd`

Direct-IO profile with `zstd` compression.

Use it to test stronger compression tradeoffs.

### `iodir_nnl`

Direct-IO profile with no top-level compression and `lz4` bottommost compression.

The suffix means: none / none-or-disabled top-level intent plus `lz4` bottommost compression, following Mark's naming shorthand.

### `iodir_nnz`

Direct-IO profile with no top-level compression and `zstd` bottommost compression.

Use it when the hot levels should avoid compression but colder data should compress more.

### `iodir_nlz`

Direct-IO profile with `lz4` top-level compression and `zstd` bottommost compression.

This matches Mark's `x3.sh` `iodir_nlz` workload token.

### `blob-iobuf`

Buffered BlobDB workload with larger values and BlobDB GC defaults.

Use it for BlobDB settings such as `BLOB_FILE_SIZE`, `MIN_BLOB_SIZE`, blob compression, and blob GC thresholds.

### `blob-iodir`

Direct-IO BlobDB workload with larger values and BlobDB GC defaults.

Use it when BlobDB behavior needs to be tested with direct IO.

## Machine Profiles

Machine profiles bundle memory/cache/LSM/background-job settings for a hardware class. They are not exact hardware detection; they are benchmark presets.

Examples:

- `c8r16`: 8 CPU / 16 GiB class.
- `c16r64`: 16 CPU / 64 GiB class.
- `c48r128`: 48 CPU / 128 GiB class.
- `c64r120`: 64 CPU / 120 GiB bench machine.

Machine profiles are useful defaults, but the final run environment is resolved in this order:

```text
machine profile -> workload profile -> baseline config -> sweep variant
```

Later layers override earlier layers. Check each run's `resolved-env.json` when in doubt.

## Scenario Versus Profile

A profile is a reusable building block.

A scenario is a complete benchmark spec for a question, for example:

- "Can this binary run a smoke benchmark?"
- "What do SurrealDB defaults do on the 64-core / 120 GiB bench machine?"
- "How does blob file size affect BlobDB?"
- "How does compaction readahead interact with IO mode?"

New users should start with scenarios and treat profiles as advanced details.
