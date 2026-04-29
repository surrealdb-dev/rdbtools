# SurrealDB RocksDB Option Mapping

`configs/surrealdb-defaults.generated.json` is generated from SurrealDB's `core/src/kvs/rocksdb/cnf.rs`. It records every discovered field, the SurrealDB config key, the environment variable name, the default expression, and whether the option can be exercised with `db_bench`.

## Direct `db_bench` Coverage

These settings map to current `rdbtools` executor flags:

- `rocksdb_jobs_count` -> `MAX_BACKGROUND_JOBS`
- `rocksdb_block_size` -> `BLOCK_SIZE`
- `rocksdb_target_file_size_base` -> `TARGET_FILE_SIZE_BASE_MB`
- `rocksdb_file_compaction_trigger` -> `LEVEL0_FILE_NUM_COMPACTION_TRIGGER`
- `rocksdb_compaction_readahead_size` -> `COMPACTION_READAHEAD_SIZE`
- `rocksdb_max_concurrent_subcompactions` -> `SUBCOMPACTIONS`
- `rocksdb_compaction_style` -> `COMPACTION_STYLE`
- `rocksdb_enable_blob_files` -> `COMPACTION_STYLE=blob`
- `rocksdb_min_blob_size` -> `MIN_BLOB_SIZE`
- `rocksdb_blob_file_size` -> `BLOB_FILE_SIZE`
- `rocksdb_blob_compression_type` -> `BLOB_COMPRESSION_TYPE`
- `rocksdb_blob_gc_age_cutoff` -> `BLOB_GC_AGE_CUTOFF`
- `rocksdb_blob_gc_force_threshold` -> `BLOB_GC_FORCE_THRESHOLD`
- `rocksdb_block_cache_size` -> `CACHE_SIZE`
- `rocksdb_write_buffer_size` -> `WRITE_BUFFER_SIZE_MB`

## `db_bench` Coverage Requiring Small Executor Additions

These are tracked as `db_bench-needed` in generated metadata:

- `rocksdb_max_open_files`
- `rocksdb_target_file_size_multiplier`
- `rocksdb_enable_blob_gc`
- `rocksdb_blob_compaction_readahead_size`
- `rocksdb_max_write_buffer_number`
- `rocksdb_min_write_buffer_number_to_merge`
- auto-readahead block-table settings

## SurrealDB Workload Required

These settings require a SurrealDB-level workload because raw `db_bench` does not model SurrealDB transaction behavior or key layout:

- `datastore_versioned`, `datastore_retention`, `datastore_sync`
- grouped commit settings
- prefix extractor, whole-key filtering, and memtable prefix bloom ratio
- inline scan threshold
- SST max allowed space usage
- deletion factory policy
- WAL retention and pipelined-write behavior when tied to SurrealDB writes

## Refresh Command

```bash
scripts/gen-surrealdb-rocksdb-defaults.sh \
  --surrealdb-root /Users/kfarhan/workspace/surrealdb/surrealdb-private/surrealdb \
  --output configs/surrealdb-defaults.generated.json
```
