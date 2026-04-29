# Machine-Aware SurrealDB RocksDB Tuning

`rdb-tune.sh` is the SurrealDB-oriented entry point for generating bounded RocksDB tuning plans from the current machine shape. It derives a baseline from SurrealDB's effective RocksDB configuration, writes ordinary rdbtools specs, and delegates actual benchmark execution to the existing runner.

## Commands

Detect the host facts used by the formulas:

```bash
scripts/rdb-tune.sh detect --json
```

Inspect the derived SurrealDB baseline:

```bash
scripts/rdb-tune.sh defaults \
  --surrealdb /Users/kfarhan/workspace/surrealdb/surrealdb-private \
  --rust-rocksdb /Users/kfarhan/workspace/surrealdb/rust-rocksdb \
  --binary 10.6.clang \
  --db-dir /data/m/rx \
  --json
```

Generate a smoke tuning plan without running `db_bench`:

```bash
scripts/rdb-tune.sh plan \
  --binary 10.6.clang \
  --db-dir /data/m/rx \
  --workload lsm-write-heavy \
  --budget smoke \
  --objective balanced \
  --surrealdb /Users/kfarhan/workspace/surrealdb/surrealdb-private \
  --rust-rocksdb /Users/kfarhan/workspace/surrealdb/rust-rocksdb \
  --dry-run-only
```

Run a generated plan:

```bash
scripts/rdb-tune.sh run --plan runs/tuning/<tuning_id>/plan.json
```

Rank completed results:

```bash
scripts/rdb-tune.sh rank --run runs/tuning/<tuning_id> --objective balanced
```

## What Gets Generated

Each tuning plan writes:

- `machine.json`: CPU, memory, cgroup, disk, and filesystem facts.
- `defaults.json`: SurrealDB config defaults, applied RocksDB options, rust-rocksdb default provenance, db_bench baseline env, and warnings.
- `plan.json`: selected workload, budget, specs, source versions, and warnings.
- `specs/*.json`: ordinary rdbtools specs with inline baseline env and `execution.clean_env=true`.
- `dry-run.json`: final resolved commands and phase envs.
- `recommendations.json` and `recommendations.md`: ranking output after results exist.

## Baseline Rules

The baseline is not copied from legacy `rdbbench` machine profiles. It is derived from:

- `cnf.rs` for memory/CPU tiered defaults such as write buffer size, max write buffers, target file size base, compaction readahead, BlobDB defaults, cache size, WAL retention, and auto readahead.
- `mod.rs`, `memory_manager.rs`, `commit_coordinator.rs`, and `background_flusher.rs` for applied options such as table layout, pinning, compression ladder, WAL flush behavior, and shared cache/memory-manager behavior.
- rust-rocksdb and vendored RocksDB metadata for options that are not explicitly set by SurrealDB.

Known caveats are kept in `defaults.json` and `dry-run.json`. Important ones include grouped commit, SurrealDB's custom prefix extractor, multi-CF behavior, shared cache/write-buffer manager behavior, and per-level compression when the selected `db_bench` binary cannot model it exactly.

## Budgets

- `smoke`: at most 8 variants, short phase durations, meant for command validation.
- `standard`: at most 40 variants, staged one-family-at-a-time screening.
- `exhaustive`: up to 200 variants unless `--max-runs` is provided.

## Self-Test

The ranking path can be checked without running RocksDB:

```bash
scripts/rdb-tune-selftest.sh
```
