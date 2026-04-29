# PR 25 RocksDB Defaults

This note maps the RocksDB-related PR 25 default discussions to focused `rdbtools` scenarios. These scenarios intentionally do not change the core `lsm-default` phase order, but their per-run environments are aligned to SurrealDB's RocksDB defaults from `cnf.rs` instead of legacy rx2 wrapper defaults.

For PR 25 parameter studies, use the scenario specs as the single entry point:

```bash
scripts/rdb-run.sh --spec scenarios/06-lsm-write-buffer-shape.json --dry-run --json
```

The JSON dry-run includes the final resolved environment, warnings, and `db_bench` command for each phase. Check that output before running benchmarks; it avoids the legacy `x.sh` problem where hardware profiles can silently override an exported setting.

For the 16 GiB tier used by these focused scenarios, the important SurrealDB-aligned fixed settings are `TARGET_FILE_SIZE_MULTIPLIER=2`, `PER_LEVEL_FANOUT=10`, `BLOCK_SIZE=65536`, `CACHE_SIZE_MB=7168`, and `COMPACTION_READAHEAD_SIZE=262144` from the currently visible `cnf.rs`.

## Pure RocksDB Settings

- `rocksdb_compaction_readahead_size`
  - Status: pure RocksDB.
  - Scenario: `scenarios/04-compaction-readahead.json`.
  - What it tests: compaction read-ahead behavior at 120 KiB, 480 KiB, and 2 MiB.
  - Closest current `rdbbench` equivalent:
    ```bash
    COMPACTION_READAHEAD_SIZE=122880 bash x.sh c8r16 30 30 40000000 1 0 none disable 1 400 0.25 1 10.6.clang
    ```
  - Swap `122880`, `491520`, and `2097152` for the covered values.

- `rocksdb_blob_file_size`
  - Status: pure RocksDB.
  - Scenario: `scenarios/03-blob-file-size.json`.
  - What it tests: BlobDB file-size behavior at 16 MiB, 64 MiB, and 128 MiB.
  - Closest current `rdbbench` equivalent:
    ```bash
    env WRITE_BUFFER_SIZE_MB=16 TARGET_FILE_SIZE_BASE_MB=16 MAX_BYTES_FOR_LEVEL_BASE_MB=64 \
      MAX_BACKGROUND_JOBS=4 NUM_THREADS=1 NUM_KEYS=40000000 VALUE_SIZE=400 \
      DURATION_RW=30 DURATION_RO=30 \
      COMPACTION_STYLE=blob MIN_LEVEL_TO_COMPRESS=3 CACHE_INDEX_AND_FILTER_BLOCKS=1 \
      BLOB_GC_AGE_CUTOFF=0.25 BLOB_GC_FORCE_THRESHOLD=1 \
      BLOB_FILE_SIZE=16777216 \
      bash benchmark_compare.sh /data/m/rx bm.bc.nt1.d0 10.6.clang
    ```
  - Swap `16777216`, `67108864`, and `134217728`.

- `default_target_file_size_base`
  - Status: pure RocksDB.
  - Scenario: `scenarios/05-target-file-size-base.json`.
  - What it tests: whether RAM-scaled target file sizes change file count, compaction fragmentation, and read behavior.
  - Closest current `rdbbench` equivalent: bypass `x.sh` and call `benchmark_compare.sh` directly, because `x.sh c8r16` hardcodes `TARGET_FILE_SIZE_BASE_MB=16`. The stock legacy script also needs `TARGET_FILE_SIZE_MULTIPLIER` passthrough to truly match SurrealDB.
    ```bash
    env WRITE_BUFFER_SIZE_MB=64 TARGET_FILE_SIZE_BASE_MB=16 TARGET_FILE_SIZE_MULTIPLIER=2 \
      MAX_BYTES_FOR_LEVEL_BASE_MB=256 PER_LEVEL_FANOUT=10 \
      MAX_BACKGROUND_JOBS=4 SUBCOMPACTIONS=4 BLOCK_SIZE=65536 CACHE_SIZE_MB=7168 \
      COMPACTION_READAHEAD_SIZE=262144 NUM_THREADS=1 NUM_KEYS=40000000 VALUE_SIZE=400 \
      DURATION_RW=30 DURATION_RO=30 \
      COMPACTION_STYLE=leveled MIN_LEVEL_TO_COMPRESS=3 CACHE_INDEX_AND_FILTER_BLOCKS=1 \
      bash benchmark_compare.sh /data/m/rx bm.lc.nt1.d0.tfs_16m 10.6.clang
    ```
  - Keep `WRITE_BUFFER_SIZE_MB=64`, `TARGET_FILE_SIZE_MULTIPLIER=2`, `MAX_BYTES_FOR_LEVEL_BASE_MB=256`, and `PER_LEVEL_FANOUT=10` fixed while swapping `TARGET_FILE_SIZE_BASE_MB`.

- `default_write_buffer_size`
  - Status: pure RocksDB, but strongly coupled to the rest of the LSM shape.
  - Scenario: `scenarios/06-lsm-write-buffer-shape.json`.
  - What it tests: SurrealDB's dynamic write-buffer tiers, with `TARGET_FILE_SIZE_BASE_MB=64`, `TARGET_FILE_SIZE_MULTIPLIER=2`, `MAX_BYTES_FOR_LEVEL_BASE_MB=256`, and `PER_LEVEL_FANOUT=10` held fixed.
  - Closest current `rdbbench` equivalent:
    ```bash
    env WRITE_BUFFER_SIZE_MB=64 TARGET_FILE_SIZE_BASE_MB=64 TARGET_FILE_SIZE_MULTIPLIER=2 \
      MAX_BYTES_FOR_LEVEL_BASE_MB=256 PER_LEVEL_FANOUT=10 \
      MAX_BACKGROUND_JOBS=4 SUBCOMPACTIONS=4 BLOCK_SIZE=65536 CACHE_SIZE_MB=7168 \
      COMPACTION_READAHEAD_SIZE=262144 NUM_THREADS=1 NUM_KEYS=40000000 VALUE_SIZE=400 \
      DURATION_RW=30 DURATION_RO=30 \
      COMPACTION_STYLE=leveled MIN_LEVEL_TO_COMPRESS=3 CACHE_INDEX_AND_FILTER_BLOCKS=1 \
      bash benchmark_compare.sh /data/m/rx bm.lc.nt1.d0.wbuf_64m 10.6.clang
    ```

- `default_max_write_buffer_number`
  - Status: pure RocksDB, but not cleanly exposed by the legacy `bench/rx2/x.sh` wrapper.
  - Scenario: not included in the exact legacy-compatible write-buffer-size scenario, because the current `benchmark.sh` hardcodes `--max_write_buffer_number` in multiple command paths.
  - What to test separately: how allowing more live memtables changes stalls, flush pressure, memory use, and LSM shape.
  - Closest current `rdbbench` equivalent: direct `db_bench --max_write_buffer_number=N ...`, or a temporary env passthrough added to `bench/rx2/benchmark.sh`.
  - `rdbtools` maps this as `MAX_WRITE_BUFFER_NUMBER -> --max_write_buffer_number`.

- `default_enable_blob_files` and `default_min_blob_size`
  - Status: pure RocksDB flags, but the usefulness depends on SurrealDB value sizes.
  - Scenario: `scenarios/07-blob-enable-min-size.json`.
  - What it tests: one leveled no-blob control plus SurrealDB's BlobDB defaults for larger values.
  - Closest current `rdbbench` equivalent:
    ```bash
    env WRITE_BUFFER_SIZE_MB=64 TARGET_FILE_SIZE_BASE_MB=64 TARGET_FILE_SIZE_MULTIPLIER=2 \
      MAX_BYTES_FOR_LEVEL_BASE_MB=256 PER_LEVEL_FANOUT=10 \
      MAX_BACKGROUND_JOBS=4 SUBCOMPACTIONS=4 BLOCK_SIZE=65536 CACHE_SIZE_MB=7168 \
      COMPACTION_READAHEAD_SIZE=262144 NUM_THREADS=1 NUM_KEYS=10000000 VALUE_SIZE=8192 \
      DURATION_RW=30 DURATION_RO=30 \
      COMPACTION_STYLE=blob MIN_LEVEL_TO_COMPRESS=3 CACHE_INDEX_AND_FILTER_BLOCKS=1 \
      MIN_BLOB_SIZE=4096 BLOB_FILE_SIZE=268435456 BLOB_COMPRESSION_TYPE=snappy \
      BLOB_GC_AGE_CUTOFF=0.5 BLOB_GC_FORCE_THRESHOLD=0.5 \
      bash benchmark_compare.sh /data/m/rx bm.bc.nt1.d0.blob_surreal 10.6.clang
    ```
  - The leveled baseline uses the same command shape with `COMPACTION_STYLE=leveled` and no BlobDB-specific env.
  - The scenario uses `VALUE_SIZE=8192` so `MIN_BLOB_SIZE=4096` is actually exercised.

- `rocksdb_wal_size_limit`
  - Status: pure RocksDB retention flag for archived obsolete WAL files.
  - Scenario: `scenarios/08-wal-size-limit.json`.
  - What it tests: archived WAL retention and disk footprint at 0, 32 MiB, and 128 MiB.
  - Closest current `rdbbench` equivalent: direct `db_bench --wal_size_limit_MB=N ...`, or add a temporary env passthrough to the legacy runner.
  - This does not test active WAL sync safety or grouped commit behavior.

- `default_max_auto_readahead_size`
  - Status: pure RocksDB iterator read option behavior.
  - Scenario: `scenarios/09-auto-readahead.json`.
  - What it tests: range-scan-heavy phases with 512 KiB, 1 MiB, and 4 MiB max auto-readahead settings, plus trigger threshold values.
  - Closest current `rdbbench` equivalent: direct `db_bench --max_auto_readahead_size=N --num_file_reads_for_auto_readahead=N ...`.
  - Point-lookup-only phases are not enough for this setting; use `fwdrange`, `revrange`, and range-while-writing phases.

## Intentionally Excluded

- `rocksdb_inline_scan_threshold`
  - Status: SurrealDB-only scheduling behavior.
  - Reason excluded: raw `db_bench` cannot model SurrealDB's scan routing decision.

- grouped commit settings
  - Status: SurrealDB-only transaction batching behavior.
  - Reason excluded: raw `db_bench` does not exercise SurrealDB grouped commit semantics.

- prefix extractor and whole-key filtering
  - Status: mixed.
  - Reason excluded for now: correctness and performance depend on SurrealDB's key layout. Add these only when there is a SurrealDB workload harness or a `db_bench` key generator that matches SurrealDB prefixes.

## Running The Scenarios

Always dry-run first:

```bash
scripts/rdb-run.sh --spec scenarios/05-target-file-size-base.json --dry-run
scripts/rdb-run.sh --spec scenarios/06-lsm-write-buffer-shape.json --dry-run
scripts/rdb-run.sh --spec scenarios/07-blob-enable-min-size.json --dry-run
scripts/rdb-run.sh --spec scenarios/08-wal-size-limit.json --dry-run
scripts/rdb-run.sh --spec scenarios/09-auto-readahead.json --dry-run
```

Use `--smoke` when checking expansion and command shape without the full phase list:

```bash
scripts/rdb-run.sh --spec scenarios/09-auto-readahead.json --dry-run --smoke
```
