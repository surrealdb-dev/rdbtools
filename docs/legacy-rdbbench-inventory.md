# Legacy rdbbench Inventory

This document records the shell-script consolidation findings used to seed `rdbtools`.

## Script Families

- `bench/rx2/*.sh`: active RocksDB benchmarking family. Representative scripts: `benchmark.sh`, `benchmark_compare.sh`, `x.sh`, `x3.sh`, `x3.blob.sh`, `resum.sh`, `recmp.sh`, `format_dbb_benchmark.sh`.
- `bench/rx/*.sh`: older RocksDB benchmark family. Useful for tuning formulas and historical comments; not used as an executor.
- `bench/rocksdb.db_bench/*.sh`: older direct `db_bench` wrappers. Useful for tuning rationale only.
- `bench/blob.bmark/*.sh`: BlobDB and FIO experiments. Useful for matrix patterns and storage caveats.
- `bench/conf/**/*.sh`: generated DBMS setup/teardown scripts. Not part of the RocksDB harness.
- `bench/sysbench.lua/**`, `bench/run_linkbench/**`, `bench/arc/**`: non-RocksDB or archive workload families, out of scope.
- `scripts/fio_*.sh`: storage-characterization helpers for possible future mode.

## Supported Entrypoints

The supported `rdbtools` entrypoints are:

- `scripts/rdb-run.sh`: plan and run benchmark specs.
- `scripts/rdb-exec-dbb.sh`: execute one resolved `db_bench` run id.
- `scripts/rdb-collect.sh`: collect `report.tsv` files into `aggregate.tsv`.
- `scripts/rdb-compare.sh`: compare baseline and variant rows.
- `scripts/rdb-summarize.sh`: pivot `aggregate.tsv` into a per-binary relative-metric matrix.
- `scripts/gen-surrealdb-rocksdb-defaults.sh`: refresh generated SurrealDB config metadata.
- `scripts/rdb-validate.sh`: validate specs before long runs.

The old `x*.sh` wrappers are replaced by JSON machine, workload, sweep, and pipeline files.

## Shared Helpers

Reusable behavior now lives in `src/rdbtools.py`:

- JSON loading and named profile resolution.
- baseline, one-at-a-time, and matrix expansion.
- binary discovery.
- environment merging and direct-IO normalization.
- phase env derivation.
- `db_bench` command construction.
- raw log, `report.tsv`, collect, and compare handling.

`src/gen_surrealdb_defaults.py` owns SurrealDB-specific parsing so the runner stays generic.

## Compatibility And Migration

- Existing Mark-style result expectations are kept through `report.tsv`, raw `benchmark_*.log`, resolved env files, and long-form aggregate output.
- Existing positional commands should be translated to JSON specs instead of wrapped directly.
- Dry-run output is the compatibility checkpoint before any destructive or long benchmark execution.
- `rdbtools` does not modify or depend on the original `rdbbench` repository at runtime.
