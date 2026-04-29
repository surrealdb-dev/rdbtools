#!/usr/bin/env python3
"""Generate rdbtools JSON metadata from SurrealDB's RocksDB cnf.rs.

This is intentionally a conservative parser for the current `cnf.rs` shape,
not a general Rust parser. Unknown or dynamic defaults are preserved as source
snippets so humans can inspect them instead of silently losing settings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


RDBTOOLS_MAP = {
    "versioned": ("DATASTORE_VERSIONED", None, "surrealdb-only", "surrealdb"),
    "retention": ("DATASTORE_RETENTION", None, "surrealdb-only", "surrealdb"),
    "sync_mode": ("DATASTORE_SYNC", None, "surrealdb-only", "surrealdb"),
    "thread_count": ("ROCKSDB_THREAD_COUNT", "increase_parallelism", "lsm", "surrealdb"),
    "jobs_count": ("MAX_BACKGROUND_JOBS", "--max_background_jobs", "lsm", "db_bench"),
    "max_open_files": ("OPEN_FILES", "--open_files", "lsm", "db_bench"),
    "block_size": ("BLOCK_SIZE", "--block_size", "cache", "db_bench"),
    "wal_size_limit": ("WAL_SIZE_LIMIT_MB", "--wal_size_limit_MB", "wal", "db_bench"),
    "target_file_size_base": ("TARGET_FILE_SIZE_BASE_MB", "--target_file_size_base", "lsm", "db_bench"),
    "target_file_size_multiplier": ("TARGET_FILE_SIZE_MULTIPLIER", "--target_file_size_multiplier", "lsm", "db_bench"),
    "file_compaction_trigger": ("LEVEL0_FILE_NUM_COMPACTION_TRIGGER", "--level0_file_num_compaction_trigger", "lsm", "db_bench"),
    "compaction_readahead_size": ("COMPACTION_READAHEAD_SIZE", "--compaction_readahead_size", "io", "db_bench"),
    "max_concurrent_subcompactions": ("SUBCOMPACTIONS", "--subcompactions", "lsm", "db_bench"),
    "enable_pipelined_writes": ("ENABLE_PIPELINED_WRITES", None, "write-path", "surrealdb"),
    "keep_log_file_num": ("KEEP_LOG_FILE_NUM", None, "ops", "surrealdb"),
    "storage_log_level": ("STORAGE_LOG_LEVEL", None, "ops", "surrealdb"),
    "compaction_style": ("COMPACTION_STYLE", "--compaction_style", "lsm", "db_bench"),
    "deletion_factory_window_size": ("DELETION_FACTORY_WINDOW_SIZE", None, "delete-compaction", "surrealdb"),
    "deletion_factory_delete_count": ("DELETION_FACTORY_DELETE_COUNT", None, "delete-compaction", "surrealdb"),
    "deletion_factory_ratio": ("DELETION_FACTORY_RATIO", None, "delete-compaction", "surrealdb"),
    "enable_blob_files": ("COMPACTION_STYLE", "--enable_blob_files", "blobdb", "db_bench"),
    "min_blob_size": ("MIN_BLOB_SIZE", "--min_blob_size", "blobdb", "db_bench"),
    "blob_file_size": ("BLOB_FILE_SIZE", "--blob_file_size", "blobdb", "db_bench"),
    "blob_compression_type": ("BLOB_COMPRESSION_TYPE", "--blob_compression_type", "blobdb", "db_bench"),
    "enable_blob_gc": ("ENABLE_BLOB_GC", "--enable_blob_garbage_collection", "blobdb", "db_bench"),
    "blob_gc_age_cutoff": ("BLOB_GC_AGE_CUTOFF", "--blob_garbage_collection_age_cutoff", "blobdb", "db_bench"),
    "blob_gc_force_threshold": ("BLOB_GC_FORCE_THRESHOLD", "--blob_garbage_collection_force_threshold", "blobdb", "db_bench"),
    "blob_compaction_readahead_size": ("BLOB_COMPACTION_READAHEAD_SIZE", "--blob_compaction_readahead_size", "blobdb", "db_bench"),
    "block_cache_size": ("CACHE_SIZE_MB", "--cache_size", "cache", "db_bench"),
    "write_buffer_size": ("WRITE_BUFFER_SIZE_MB", "--write_buffer_size", "lsm", "db_bench"),
    "max_write_buffer_number": ("MAX_WRITE_BUFFER_NUMBER", "--max_write_buffer_number", "lsm", "db_bench"),
    "min_write_buffer_number_to_merge": ("MIN_WRITE_BUFFER_NUMBER_TO_MERGE", "--min_write_buffer_number_to_merge", "lsm", "db_bench"),
    "initial_auto_readahead_size": ("INITIAL_AUTO_READAHEAD_SIZE", "--initial_auto_readahead_size", "cache", "db_bench"),
    "max_auto_readahead_size": ("MAX_AUTO_READAHEAD_SIZE", "--max_auto_readahead_size", "cache", "db_bench"),
    "file_reads_for_auto_readahead": ("FILE_READS_FOR_AUTO_READAHEAD", "--num_file_reads_for_auto_readahead", "cache", "db_bench"),
    "whole_key_filtering": ("WHOLE_KEY_FILTERING", "--whole_key_filtering", "cache", "db_bench"),
    "prefix_extractor_enabled": ("ROCKSDB_PREFIX_EXTRACTOR_ENABLED", None, "surrealdb-only", "surrealdb"),
    "memtable_prefix_bloom_ratio": ("ROCKSDB_MEMTABLE_PREFIX_BLOOM_RATIO", None, "surrealdb-only", "surrealdb"),
    "grouped_commit_timeout": ("ROCKSDB_GROUPED_COMMIT_TIMEOUT", None, "surrealdb-only", "surrealdb"),
    "grouped_commit_wait_threshold": ("ROCKSDB_GROUPED_COMMIT_WAIT_THRESHOLD", None, "surrealdb-only", "surrealdb"),
    "grouped_commit_max_batch_size": ("ROCKSDB_GROUPED_COMMIT_MAX_BATCH_SIZE", None, "surrealdb-only", "surrealdb"),
    "inline_scan_threshold": ("ROCKSDB_INLINE_SCAN_THRESHOLD", None, "surrealdb-only", "surrealdb"),
    "sst_max_allowed_space_usage": ("ROCKSDB_SST_MAX_ALLOWED_SPACE_USAGE", None, "surrealdb-only", "surrealdb"),
}


APPLIED_OPTIONS = [
    {
        "name": "MANUAL_WAL_FLUSH",
        "rdbtools_env": "MANUAL_WAL_FLUSH",
        "db_bench_flag": "--manual_wal_flush",
        "category": "wal",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "true when sync=every"},
        "source": "commit_coordinator.rs",
        "description": "SurrealDB's default sync=every path enables manual WAL flushing and groups fsync calls in the commit coordinator.",
    },
    {
        "name": "WAL_BYTES_PER_SYNC",
        "rdbtools_env": "WAL_BYTES_PER_SYNC",
        "db_bench_flag": "--wal_bytes_per_sync",
        "category": "wal",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "512 KiB when sync=every"},
        "source": "commit_coordinator.rs",
        "description": "Datastore::new sets 2 MiB first; CommitCoordinator::configure overrides to 512 KiB for default sync=every.",
    },
    {
        "name": "PARTITION_INDEX_AND_FILTERS",
        "rdbtools_env": "PARTITION_INDEX_AND_FILTERS",
        "db_bench_flag": "--partition_index_and_filters",
        "category": "cache",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "true"},
        "source": "memory_manager.rs",
        "description": "SurrealDB enables partitioned filters with two-level index search.",
    },
    {
        "name": "METADATA_BLOCK_SIZE",
        "rdbtools_env": "METADATA_BLOCK_SIZE",
        "db_bench_flag": "--metadata_block_size",
        "category": "cache",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "4096"},
        "source": "memory_manager.rs",
        "description": "SurrealDB sets partitioned metadata block size to 4096 bytes.",
    },
    {
        "name": "PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE",
        "rdbtools_env": "PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE",
        "db_bench_flag": "--pin_l0_filter_and_index_blocks_in_cache",
        "category": "cache",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "true"},
        "source": "memory_manager.rs",
        "description": "SurrealDB pins L0 filter and index blocks in cache.",
    },
    {
        "name": "PIN_TOP_LEVEL_INDEX_AND_FILTER",
        "rdbtools_env": "PIN_TOP_LEVEL_INDEX_AND_FILTER",
        "db_bench_flag": "--pin_top_level_index_and_filter",
        "category": "cache",
        "executor": "db_bench",
        "default": {"kind": "literal", "expression": "true"},
        "source": "memory_manager.rs",
        "description": "SurrealDB pins top-level index and filter metadata in cache.",
    },
    {
        "name": "COMPRESSION_PER_LEVEL",
        "rdbtools_env": None,
        "db_bench_flag": None,
        "category": "compression",
        "executor": "surrealdb",
        "default": {"kind": "literal", "expression": "[none, lz4, lz4, lz4, lz4, zstd, zstd, zstd]"},
        "source": "mod.rs",
        "description": "SurrealDB applies a per-level compression ladder that vanilla db_bench does not model exactly with a single compression_type flag.",
    },
]


DYNAMIC_DEFAULTS = {
    "default_compaction_readahead_size": "256 KiB",
    "default_block_cache_size": "max(total_memory / 2 - 1 GiB, 16 MiB)",
    "default_write_buffer_size": "< 1 GiB: 32 MiB, < 16 GiB: 64 MiB, otherwise 128 MiB",
    "default_max_write_buffer_number": "< 4 GiB: 2, < 16 GiB: 4, < 64 GiB: 8, otherwise 32",
    "default_max_concurrent_subcompactions": "available_parallelism clamped to [1, 4]",
    "default_jobs_count": "min(cpu_count * 2, max(total_memory / 128 MiB, 2))",
    "default_target_file_size_base": "< 1 GiB: 8 MiB, < 4 GiB: 16 MiB, < 16 GiB: 32 MiB, otherwise 64 MiB",
    "default_grouped_commit_max_batch_size": "< 1 GiB: 256, < 4 GiB: 1024, otherwise 4096",
    "default_keep_log_file_num": "< 1 GiB: 2, < 4 GiB: 5, otherwise 10",
    "default_max_auto_readahead_size": "< 1 GiB: 512 KiB, < 4 GiB: 1 MiB, otherwise 4 MiB",
    "default_inline_scan_threshold": "< 1 GiB: 512 KiB, < 4 GiB: 1 MiB, otherwise 4 MiB",
    "default_max_open_files": "< 1 GiB: 256, < 4 GiB: 512, otherwise 1026",
    "default_enable_blob_files": "TOTAL_SYSTEM_MEMORY >= 1 GiB",
    "default_blob_file_size": "< 1 GiB: 16 MiB, < 4 GiB: 64 MiB, < 16 GiB: 128 MiB, otherwise 256 MiB",
    "default_wal_size_limit": "< 1 GiB: 32 MiB, < 16 GiB: 128 MiB, otherwise 0",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def clean_doc(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*///\s?", "", line)
        if line:
            lines.append(line)
    return " ".join(lines)


def parse_fields(cnf: str) -> dict[str, dict[str, str]]:
    match = re.search(r"pub struct RocksDbConfig\s*\{(?P<body>.*?)\n\}", cnf, re.S)
    if not match:
        raise RuntimeError("Could not find RocksDbConfig struct")
    fields: dict[str, dict[str, str]] = {}
    pending_docs: list[str] = []
    for line in match.group("body").splitlines():
        if line.strip().startswith("///"):
            pending_docs.append(line)
            continue
        field = re.search(r"pub\s+([a-zA-Z0-9_]+):\s*([^,]+),", line)
        if field:
            name = field.group(1)
            fields[name] = {
                "field": name,
                "rust_type": field.group(2).strip(),
                "description": clean_doc("\n".join(pending_docs)),
            }
            pending_docs = []
        elif line.strip():
            pending_docs = []
    return fields


def parse_default_body(cnf: str) -> dict[str, str]:
    match = re.search(r"impl Default for RocksDbConfig\s*\{.*?Self\s*\{(?P<body>.*?)\n\s*\}\n\s*\}\n\}", cnf, re.S)
    if not match:
        raise RuntimeError("Could not find RocksDbConfig default body")
    defaults: dict[str, str] = {}
    for line in match.group("body").splitlines():
        match_line = re.search(r"^\s*([a-zA-Z0-9_]+):\s*(.*),\s*$", line)
        if match_line:
            defaults[match_line.group(1)] = match_line.group(2).strip()
    return defaults


def parse_parse_keys(cnf: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    pattern = re.compile(r'\.parse_key(?:_bool|_with)?\(\s*"([^"]+)"\s*,\s*&mut self\.([a-zA-Z0-9_]+)', re.S)
    for key, field in pattern.findall(cnf):
        keys[field] = key
    keys["sync_mode"] = "datastore_sync"
    return keys


def default_payload(expr: str) -> dict[str, Any]:
    fn = re.match(r"(default_[a-zA-Z0-9_]+)\(\)", expr)
    if fn:
        name = fn.group(1)
        return {"kind": "dynamic", "expression": DYNAMIC_DEFAULTS.get(name, name)}
    return {"kind": "literal", "expression": expr}


def build_options(cnf: str) -> list[dict[str, Any]]:
    fields = parse_fields(cnf)
    defaults = parse_default_body(cnf)
    parse_keys = parse_parse_keys(cnf)
    options: list[dict[str, Any]] = []
    for field, meta in fields.items():
        surreal_key = parse_keys.get(field)
        mapping = RDBTOOLS_MAP.get(field)
        item: dict[str, Any] = {
            "surrealdb_key": surreal_key,
            "surrealdb_env": f"SURREAL_{surreal_key.upper()}" if surreal_key else None,
            "field": field,
            "rust_type": meta["rust_type"],
            "description": meta["description"],
            "default": default_payload(defaults[field]) if field in defaults else {"kind": "unresolved"},
        }
        if mapping:
            item.update(
                {
                    "rdbtools_env": mapping[0],
                    "db_bench_flag": mapping[1],
                    "category": mapping[2],
                    "executor": mapping[3],
                }
            )
        else:
            item.update(
                {
                    "rdbtools_env": None,
                    "db_bench_flag": None,
                    "category": "unmapped",
                    "executor": "unknown",
                }
            )
        if field.startswith("blob_") or field in {"enable_blob_files", "enable_blob_gc", "min_blob_size"}:
            item["depends_on"] = {"COMPACTION_STYLE": "blob"}
        options.append(item)
    return options


def generate(surrealdb_root: Path) -> dict[str, Any]:
    rocksdb_dir = surrealdb_root / "core/src/kvs/rocksdb"
    cnf_path = rocksdb_dir / "cnf.rs"
    mod_path = rocksdb_dir / "mod.rs"
    memory_path = rocksdb_dir / "memory_manager.rs"
    cnf = read(cnf_path)
    payload = {
        "source": {
            "kind": "surrealdb-rocksdb-cnf",
            "repo": str(surrealdb_root),
            "files": [
                "core/src/kvs/rocksdb/cnf.rs",
                "core/src/kvs/rocksdb/mod.rs",
                "core/src/kvs/rocksdb/memory_manager.rs",
            ],
        },
        "options": build_options(cnf) + APPLIED_OPTIONS,
        "warnings": [],
    }
    for path in [mod_path, memory_path]:
        if not path.exists():
            payload["warnings"].append(f"Missing application-site file: {path}")
    unknown = [item["field"] for item in payload["options"] if item["category"] == "unmapped"]
    if unknown:
        payload["warnings"].append(f"Unmapped fields: {', '.join(unknown)}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surrealdb-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = generate(Path(args.surrealdb_root))
    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
    if payload["warnings"]:
        for warning in payload["warnings"]:
            print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
