# SurrealDB Workload Plan

Some SurrealDB RocksDB settings cannot be represented honestly with raw `db_bench`. They should be benchmarked with a future SurrealDB workload executor that reuses the same config catalog and result format.

## Required SurrealDB Workloads

- Grouped commit: write transactions with configurable concurrency, batch size, sync mode, and latency reporting.
- Prefix extractor and whole-key filtering: point lookup and range scan workloads using real SurrealDB key layout.
- Inline scan threshold: scans near and above the byte threshold to measure async executor impact.
- Disk-space restriction: controlled SST growth plus delete/recovery behavior.
- Versioned datastore behavior: versioned writes, retention, GC, and timestamped reads.
- Sync mode and WAL behavior: fsync/fdatasync-sensitive write workloads instead of `DB_BENCH_NO_SYNC=1`.

## Shared Contract

The future SurrealDB executor should emit the same high-level artifacts as `db_bench` runs:

- `run_id`
- binary or build label
- workload name
- config variant
- raw logs
- long-form TSV rows
- baseline-vs-variant comparison output

This keeps `rdbtools compare` useful across both `db_bench` and SurrealDB-native workloads.
