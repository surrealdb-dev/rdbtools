# Legacy Script Mapping

The implementation treats the existing `rdbbench` shell scripts as methodology, not as runtime dependencies. This file maps each legacy script to its replacement in `rdbtools`.

## Active Inputs

- `bench/rx2/benchmark.sh`: option catalog, `db_bench` flag mapping, per-job reporting shape.
- `bench/rx2/benchmark_compare.sh`: version loop, phase order, per-version directories, raw logs, `summary.tsv`.
- `bench/rx2/x.sh`: machine profile bundles.
- `bench/rx2/x3.sh` and `bench/rx2/x3.blob.sh`: workload profile tokens.
- `bench/rx2/resum*.sh`, `run_resum.sh`, `recmp.sh`: result reparse and summary rebuild behavior.
- `bench/rx2/format_dbb_benchmark.sh`: baseline-relative formatting concept.

## Design Inputs Only

- `bench/rx/perf_cmp*.sh`: pending compaction byte formulas, rate-limit comments, WAL/sync rationale.
- `bench/rocksdb.db_bench/all.sh` and `big.sh`: tuning commentary.
- `bench/blob.bmark/**`: blob matrix experiments and raw FIO characterization ideas.
- `scripts/fio_parse.sh` and `scripts/fio_sync.sh`: future storage-characterization mode.

## Quarantined

- `bench/conf/**`, sysbench, LinkBench, and archive folders are not part of the RocksDB benchmark harness.
- Dead or unreachable branches from `x.sh` are represented as explicit JSON profiles or omitted.
- Older `benchmark.v4.sh`/`benchmark.v5.sh` compatibility forks are not separate executors.

## How This Appears In `rdbtools`

- `machines/profiles.json` replaces machine `case` branches.
- `workloads/profiles.json` replaces workload-token shell wrappers.
- `pipelines/lsm-default.json` preserves the default phase order.
- `catalog/options.json` records the benchmark setting catalog and dependencies.
- `scripts/rdb-run.sh`, `scripts/rdb-collect.sh`, `scripts/rdb-compare.sh`, and `scripts/rdb-summarize.sh` replace the public launcher, reparse, comparison, and per-binary relative-summary layers.
