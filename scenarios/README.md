# Scenarios

Scenarios are curated benchmark specs for common questions. They sit on top of the same JSON engine used by `configs/`, `workloads/`, `machines/`, `sweeps/`, and `pipelines`, but they are meant to be the first place a new user starts.

Use scenarios when you know the question you want to answer but do not yet want to compose the lower-level profiles by hand.

## Available Scenarios

- `01-smoke.json`: smallest safe starter. One binary, one workload, no sweeps.
- `02-surrealdb-defaults-c64r120.json`: current SurrealDB RocksDB defaults on the 64-core / 120 GiB bench machine.
- `03-blob-file-size.json`: focused BlobDB `BLOB_FILE_SIZE` sweep.
- `04-compaction-readahead.json`: focused `COMPACTION_READAHEAD_SIZE` sweep.
- `05-target-file-size-base.json`: SurrealDB-aligned `TARGET_FILE_SIZE_BASE_MB` sweep for compaction shape and file-count effects.
- `06-lsm-write-buffer-shape.json`: SurrealDB write-buffer tier sweep with fixed target-file and L1 shape.
- `07-blob-enable-min-size.json`: compares a leveled no-blob control with SurrealDB BlobDB defaults.
- `08-wal-size-limit.json`: focused `WAL_SIZE_LIMIT_MB` sweep for archived WAL retention footprint.
- `09-auto-readahead.json`: range-scan-focused auto-readahead sweep.

## PR 25 Defaults

The PR 25 RocksDB-default scenarios are meant for one setting family at a time. These specs encode SurrealDB's `cnf.rs` defaults directly instead of inheriting `x.sh` or `benchmark_compare.sh` defaults:

```bash
scripts/rdb-run.sh --spec scenarios/05-target-file-size-base.json --dry-run
scripts/rdb-run.sh --spec scenarios/06-lsm-write-buffer-shape.json --dry-run
scripts/rdb-run.sh --spec scenarios/07-blob-enable-min-size.json --dry-run
scripts/rdb-run.sh --spec scenarios/08-wal-size-limit.json --dry-run
scripts/rdb-run.sh --spec scenarios/09-auto-readahead.json --dry-run
```

Use JSON dry-runs when checking PR 25 parameter studies:

```bash
scripts/rdb-run.sh --spec scenarios/06-lsm-write-buffer-shape.json --dry-run --json
```

The JSON output includes the resolved environment and final `db_bench` command for each phase. Treat that output as the single source of truth before removing `--dry-run`.

Warnings in the JSON output call out known modeling caveats, such as SurrealDB-only behavior that raw `db_bench` cannot exercise.

Use `docs/pr25-rocksdb-defaults.md` to see which PR comments are pure RocksDB, which are SurrealDB-only, and which scenario maps to each RocksDB setting.

## First Commands

Always inspect the run plan before running benchmarks:

```bash
scripts/rdb-run.sh --spec scenarios/01-smoke.json --dry-run
```

For the 64-core SurrealDB defaults scenario:

```bash
scripts/rdb-run.sh --spec scenarios/02-surrealdb-defaults-c64r120.json --dry-run
```

To run a short structural check without the full phase list:

```bash
scripts/rdb-run.sh --spec scenarios/02-surrealdb-defaults-c64r120.json --dry-run --smoke
```

Remove `--dry-run` only after the binary path, DB directory, run count, phase count, and output directory are correct.
