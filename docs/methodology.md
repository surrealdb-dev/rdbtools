# Methodology

`rdbtools` keeps the useful parts of Mark Callaghan's `bench/rx2` workflow while replacing positional shell entrypoints with JSON specs.

## Preserved Patterns

- A single resolved environment bundle is used for every logical benchmark run.
- Each RocksDB version runs through the same deterministic phase order.
- Every run has a stable `run_id`, raw logs, `resolved-env.json`, `variant.json`, and `report.tsv`.
- `summary.tsv` compatibility is represented by `scripts/rdb-collect.sh`, which creates long-form `aggregate.tsv`.
- `scripts/rdb-summarize.sh` then pivots `aggregate.tsv` into a per-binary relative-metric matrix.
- Rate-limited mixed read/write phases are separate from load and no-limit phases.
- `NUM_KEYS` can be workload-coupled: workload profiles declare `key_count_source: nk_mem` or `nk_io`, and the spec supplies `nk_mem`/`nk_io`. A baseline `NUM_KEYS` still wins if present.
- Per-phase Linux observability can be captured next to `benchmark_<phase>.log` when `execution.capture_io_stats` is true.
- Machine and workload profiles are explicit JSON data rather than `case` branches.

## Phase Order

The default `lsm-default` pipeline is derived from `bench/rx2/benchmark_compare.sh`:

1. `fillseq_disable_wal`
2. `revrange` drain
3. optional read-only tests: `readrandom`, `fwdrange`, `multireadrandom`
4. `overwritesome`
5. `flush_mt_l0`
6. optional mixed read/write tests: `revrangewhilewriting`, `fwdrangewhilewriting`, `readwhilewriting`
7. `overwriteandwait`

Use `--smoke` to skip phases marked `full_only`.

## What Is Deliberately Not Carried Forward

- No hidden hardcoded `/data/m/rx`; specs must choose `db_dir`.
- No hidden long sleeps; cooldown is an explicit execution setting.
- No public 12-argument shell interface.
- No runtime dependency on archive DBMS scripts or `bench/conf/**`.
- No legacy RocksDB compatibility branches unless the current binary requires feature detection.

## Runtime Caveats

Long matrix sweeps can explode quickly. Always start with:

```bash
scripts/rdb-run.sh --spec scenarios/01-smoke.json --dry-run
```

Small BlobDB file sizes can create many files and may require:

```bash
ulimit -n 100000
```
