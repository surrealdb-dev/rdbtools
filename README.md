# rdbtools

`rdbtools` is a JSON-driven RocksDB benchmark harness inspired by Mark Callaghan's `rdbbench` scripts.

The goal is to make RocksDB configuration experiments repeatable:

1. Build one or more `db_bench` binaries.
2. Describe a baseline config in JSON.
3. Add one-at-a-time or matrix sweeps.
4. Generate a run plan, execute it, collect `report.tsv`, and compare variants.

SurrealDB RocksDB defaults are the first supported config source, but the JSON format is intentionally generic so future RocksDB options can be added without rewriting the runner.

## Start Here

If you are new to Mark Callaghan-style RocksDB benchmarks, start with `docs/getting-started.md`.

The recommended first path is:

1. Read `docs/getting-started.md`.
2. Inspect `scenarios/01-smoke.json` with `scripts/rdb-run.sh --spec scenarios/01-smoke.json --dry-run`.
3. Inspect the bench-machine baseline with `scripts/rdb-run.sh --spec scenarios/02-surrealdb-defaults-c64r120.json --dry-run`.
4. For machine-aware SurrealDB tuning, read `docs/machine-aware-tuning.md`.
5. Read `docs/understanding-benchmark-runs.md` when you want the full model.

Use `scenarios/` for curated entrypoints. Use `machines/`, `workloads/`, `sweeps/`, `pipelines/`, and `catalog/` when you are ready to compose custom experiments.

## Layout

- `scenarios/`: curated first-run and common-question benchmark specs.
- `configs/`: generated and hand-written baseline configs.
- `sweeps/`: reusable sweep definitions.
- `machines/`: machine profiles derived from Mark's `x.sh`.
- `workloads/`: workload profiles derived from Mark's `x3.sh` and `x3.blob.sh`.
- `pipelines/`: benchmark phase order, derived from `benchmark_compare.sh`.
- `catalog/`: option catalog and JSON schema.
- `scripts/`: current executable implementation.
- `src/`: shared Python implementation, intended to become the CLI core.
- `docs/`: methodology and mapping notes.

## Quick Start

Generate a run plan without executing benchmarks:

```bash
scripts/rdb-run.sh --spec scenarios/01-smoke.json --dry-run
```

Generate SurrealDB RocksDB defaults:

```bash
scripts/gen-surrealdb-rocksdb-defaults.sh \
  --surrealdb-root /Users/kfarhan/workspace/surrealdb/surrealdb-private/surrealdb \
  --output configs/surrealdb-defaults.generated.json
```

Generate a machine-aware SurrealDB tuning plan:

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

Validate a spec:

```bash
scripts/rdb-validate.sh --spec scenarios/01-smoke.json
```

Collect, compare, and summarize existing run outputs:

```bash
scripts/rdb-collect.sh runs/blob-file-size
scripts/rdb-compare.sh --baseline baseline --variant blob_file_size__BLOB_FILE_SIZE_16777216 runs/blob-file-size/aggregate.tsv
scripts/rdb-summarize.sh --reference 10.6.clang --metric ops_sec runs/blob-file-size/aggregate.tsv
```

## Notes

Actual benchmark execution expects Linux tooling for full observability (`iostat`, `vmstat`, `numactl`, `perf`) and a `db_bench.<version>` binary or explicit binary path. Scenario `db_dir` values such as `/data/m/rx` are benchmark-machine conventions and must be writable. Real runs check path permissions up front before deleting data or starting benchmark phases. Small BlobDB file sizes may require a higher file descriptor limit, for example `ulimit -n 100000`.
