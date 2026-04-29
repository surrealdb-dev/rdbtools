# Getting Started

This is the shortest path to understanding and safely using `rdbtools`.

`rdbtools` has many JSON files because it separates benchmark concerns that Mark Callaghan's shell scripts mixed together: machine profiles, workload profiles, sweeps, phase pipelines, and option catalogs. New users should start with `scenarios/`, not with those lower-level files.

## 1. Build Or Copy `db_bench`

Build `db_bench` from the RocksDB tree you want to test, usually:

```bash
cd /Users/kfarhan/workspace/surrealdb/rocksdb
DISABLE_WARNING_AS_ERROR=1 DEBUG_LEVEL=0 make -j"$(nproc)" db_bench
```

Copy it into `rdbtools/bin/` using the label you want in results:

```bash
cp /Users/kfarhan/workspace/surrealdb/rocksdb/db_bench \
  /Users/kfarhan/workspace/surrealdb/rdbtools/bin/db_bench.10.6.clang
```

The label `10.6.clang` is only an example. It can be a RocksDB version, compiler label, git hash, or any name that helps you compare binaries.

## 2. Start With A Dry Run

From the `rdbtools` repo:

```bash
scripts/rdb-run.sh --spec scenarios/01-smoke.json --dry-run
```

Read the output before running anything. Check:

- The resolved binary path.
- The DB directory.
- The output directory.
- The number of concrete runs.
- The number of phases.

Dry-run is the safety check. It does not run `db_bench` or remove data.

`db_dir` is where RocksDB writes the temporary benchmark database. The scenarios use `/data/m/rx` by convention because benchmark machines usually mount the target storage device under `/data`. It is not a RocksDB requirement, but it should be the disk you intend to benchmark.

## 3. Run A Smoke Plan

After the dry run looks right, run the smoke scenario:

```bash
scripts/rdb-run.sh --spec scenarios/01-smoke.json
```

This is intentionally small. It checks that the binary can run, output is written, and reports can be collected.

## 4. Inspect The 64-Core SurrealDB Defaults Scenario

The main bench-machine baseline is:

```bash
scripts/rdb-run.sh --spec scenarios/02-surrealdb-defaults-c64r120.json --dry-run
```

This scenario is for a 64-core / 120 GiB machine and selected workload profiles. It has no sweeps; it is meant to answer, "What do SurrealDB RocksDB defaults do on this machine?"

## 5. Collect Results

After a run completes:

```bash
scripts/rdb-collect.sh runs/surrealdb-defaults-c64r120
```

This writes an `aggregate.tsv` file that combines per-run `report.tsv` files.

## 6. Learn The Building Blocks Later

After scenarios make sense, read:

- `docs/understanding-benchmark-runs.md` for the full run model.
- `docs/profile-glossary.md` for machine and workload profile names.
- `docs/surrealdb-option-mapping.md` for how SurrealDB settings map to `db_bench`.
- `catalog/options.json` only when you need to add or validate a new option.

## Safety Notes

- Do not run a long benchmark until a dry run shows the expected run count and phase count.
- Real runs perform an upfront permission check for `db_dir`, `wal_dir` when configured, and `output_dir` before deleting data or starting benchmark phases.
- `db_dir` may be deleted by real runs when `remove_db_before_version` is true.
- Small BlobDB file sizes can require `ulimit -n 100000`.
- macOS is only suitable for limited dry-run or buffered-IO checks; serious runs should happen on Linux.
