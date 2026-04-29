#!/usr/bin/env python3
"""Machine-aware SurrealDB RocksDB tuning workflow."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import rdbtools


ROOT = Path(__file__).resolve().parents[1]
KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB


class RdbTuneError(RuntimeError):
    pass


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(path: str | Path, base: Path = ROOT) -> Path:
    path = Path(path)
    return path if path.is_absolute() else base / path


def host_memory_bytes() -> int:
    if platform.system() == "Darwin":
        try:
            proc = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                return int(proc.stdout.strip())
        except (OSError, ValueError):
            pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return GIB


def read_positive_int(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


def cgroup_memory_bytes(host_bytes: int) -> tuple[int | None, str | None]:
    candidates = [
        (Path("/sys/fs/cgroup/memory.max"), "cgroup_v2"),
        (Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"), "cgroup_v1"),
    ]
    for path, source in candidates:
        value = read_positive_int(path)
        if value is None:
            continue
        # cgroup v1 often exposes a huge sentinel close to u64::MAX when unlimited.
        if value >= (1 << 60) or value > max(host_bytes * 16, host_bytes + GIB):
            continue
        return value, source
    return None, None


def available_cpu_count() -> int:
    try:
        return max(len(os.sched_getaffinity(0)), 1)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(os.cpu_count() or 1, 1)


def disk_facts(db_dir: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    parent = db_dir if db_dir.exists() and db_dir.is_dir() else db_dir.parent
    facts: dict[str, Any] = {
        "db_dir": str(db_dir),
        "parent": str(parent),
        "parent_exists": parent.exists(),
        "free_bytes": None,
        "total_bytes": None,
        "max_sectors_kb": None,
    }
    if parent.exists():
        usage = shutil.disk_usage(parent)
        facts.update({"free_bytes": usage.free, "total_bytes": usage.total})
    else:
        warnings.append(f"db_dir parent does not exist yet: {parent}")

    # Linux block-device mapping is intentionally best-effort.
    sys_block = Path("/sys/block")
    if sys_block.exists():
        for queue_file in sys_block.glob("*/queue/max_sectors_kb"):
            value = read_positive_int(queue_file)
            if value:
                facts["max_sectors_kb"] = value
                break
        if facts["max_sectors_kb"] is None:
            warnings.append("could not discover /sys/block/*/queue/max_sectors_kb")
    else:
        warnings.append("block device max_sectors_kb is not available on this OS")
    return facts, warnings


def detect_machine(db_dir: Path | None = None) -> dict[str, Any]:
    host = host_memory_bytes()
    if host == 0:
        host = GIB
    cgroup, cgroup_source = cgroup_memory_bytes(host)
    effective = cgroup or host
    warnings: list[str] = []
    disk = None
    if db_dir is not None:
        disk, disk_warnings = disk_facts(db_dir)
        warnings.extend(disk_warnings)
    return {
        "host_memory_bytes": host,
        "effective_memory_bytes": effective,
        "memory_source": cgroup_source or "host",
        "cgroup_memory_bytes": cgroup,
        "cpu_count": available_cpu_count(),
        "platform": platform.platform(),
        "disk": disk,
        "warnings": warnings,
    }


def tiered(mem: int, tiers: list[tuple[int, Any]], fallback: Any) -> Any:
    for limit, value in tiers:
        if mem < limit:
            return value
    return fallback


def git_hash(path: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def cargo_version(path: Path, package: str | None = None) -> str | None:
    cargo = path / "Cargo.toml"
    if not cargo.exists():
        return None
    text = cargo.read_text(encoding="utf-8")
    if package:
        match = re.search(rf"{re.escape(package)}\s*=\s*\{{[^}}]*version\s*=\s*\"([^\"]+)\"", text)
        return match.group(1) if match else None
    match = re.search(r"(?m)^version\s*=\s*\"([^\"]+)\"", text)
    return match.group(1) if match else None


def resolve_binary_path(label_or_path: str) -> Path:
    raw = Path(label_or_path)
    if raw.is_absolute() or raw.exists():
        return raw
    for candidate in [ROOT / "bin" / f"db_bench.{label_or_path}", ROOT / "bin" / label_or_path, ROOT / f"db_bench.{label_or_path}"]:
        if candidate.exists():
            return candidate
    return ROOT / "bin" / f"db_bench.{label_or_path}"


def detect_binary_capabilities(binary: str | None) -> dict[str, Any]:
    if not binary:
        return {"binary": None, "exists": False, "flags": [], "warnings": ["no db_bench binary provided"]}
    path = resolve_binary_path(binary)
    warnings: list[str] = []
    if not path.exists():
        return {"binary": str(path), "exists": False, "flags": [], "warnings": [f"db_bench binary not found: {path}"]}
    try:
        proc = subprocess.run([str(path), "--help"], capture_output=True, text=True, check=False, timeout=10)
        help_text = proc.stdout + "\n" + proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"binary": str(path), "exists": True, "flags": [], "warnings": [f"could not inspect db_bench flags: {exc}"]}
    flags = sorted(set(re.findall(r"--[A-Za-z0-9_]+", help_text)))
    if not flags:
        warnings.append("db_bench --help did not expose parseable flags")
    return {"binary": str(path), "exists": True, "flags": flags, "warnings": warnings}


def has_flag(capabilities: dict[str, Any], flag: str) -> bool:
    flags = set(capabilities.get("flags") or [])
    return flag in flags


def derive_surrealdb_baseline(
    machine: dict[str, Any],
    surrealdb_root: Path,
    rust_rocksdb_root: Path,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mem = int(machine["effective_memory_bytes"])
    cpu = int(machine["cpu_count"])
    capabilities = capabilities or {"flags": []}
    warnings = list(machine.get("warnings", [])) + list(capabilities.get("warnings", []))

    write_buffer_mb = tiered(mem, [(GIB, 32), (16 * GIB, 64)], 128)
    max_write_buffers = tiered(mem, [(4 * GIB, 2), (16 * GIB, 4), (64 * GIB, 8)], 32)
    block_cache_bytes = max(mem // 2 - GIB, 16 * MIB)
    enable_blob_files = mem >= GIB

    config_defaults = {
        "THREAD_COUNT": {"value": cpu, "source": "cnf.rs:RocksDbConfig::default"},
        "MAX_BACKGROUND_JOBS": {"value": min(cpu * 2, max(mem // (128 * MIB), 2)), "source": "cnf.rs:default_jobs_count"},
        "OPEN_FILES": {"value": tiered(mem, [(GIB, 256), (4 * GIB, 512)], 1026), "source": "cnf.rs:default_max_open_files"},
        "BLOCK_SIZE": {"value": 65536, "source": "cnf.rs:RocksDbConfig::default"},
        "WAL_SIZE_LIMIT_MB": {"value": tiered(mem, [(GIB, 32), (16 * GIB, 128)], 0), "source": "cnf.rs:default_wal_size_limit"},
        "TARGET_FILE_SIZE_BASE_MB": {"value": tiered(mem, [(GIB, 8), (4 * GIB, 16), (16 * GIB, 32)], 64), "source": "cnf.rs:default_target_file_size_base"},
        "TARGET_FILE_SIZE_MULTIPLIER": {"value": 2, "source": "cnf.rs:RocksDbConfig::default"},
        "LEVEL0_FILE_NUM_COMPACTION_TRIGGER": {"value": 4, "source": "cnf.rs:RocksDbConfig::default"},
        "COMPACTION_READAHEAD_SIZE": {"value": 256 * KIB, "source": "cnf.rs:default_compaction_readahead_size"},
        "SUBCOMPACTIONS": {"value": max(min(cpu, 4), 1), "source": "cnf.rs:default_max_concurrent_subcompactions"},
        "COMPACTION_STYLE_CONFIG": {"value": "level", "source": "cnf.rs:RocksDbConfig::default"},
        "ENABLE_BLOB_FILES": {"value": 1 if enable_blob_files else 0, "source": "cnf.rs:default_enable_blob_files"},
        "MIN_BLOB_SIZE": {"value": 4096, "source": "cnf.rs:RocksDbConfig::default"},
        "BLOB_FILE_SIZE": {"value": tiered(mem, [(GIB, 16 * MIB), (4 * GIB, 64 * MIB), (16 * GIB, 128 * MIB)], 256 * MIB), "source": "cnf.rs:default_blob_file_size"},
        "BLOB_COMPRESSION_TYPE": {"value": "snappy", "source": "cnf.rs:BlobCompression::default"},
        "ENABLE_BLOB_GC": {"value": 1, "source": "cnf.rs:RocksDbConfig::default"},
        "BLOB_GC_AGE_CUTOFF": {"value": 0.5, "source": "cnf.rs:RocksDbConfig::default"},
        "BLOB_GC_FORCE_THRESHOLD": {"value": 0.5, "source": "cnf.rs:RocksDbConfig::default"},
        "BLOB_COMPACTION_READAHEAD_SIZE": {"value": 0, "source": "cnf.rs:RocksDbConfig::default"},
        "CACHE_SIZE_MB": {"value": block_cache_bytes // MIB, "source": "cnf.rs:default_block_cache_size"},
        "WRITE_BUFFER_SIZE_MB": {"value": write_buffer_mb, "source": "cnf.rs:default_write_buffer_size"},
        "MAX_WRITE_BUFFER_NUMBER": {"value": max_write_buffers, "source": "cnf.rs:default_max_write_buffer_number"},
        "MIN_WRITE_BUFFER_NUMBER_TO_MERGE": {"value": 2, "source": "cnf.rs:RocksDbConfig::default"},
        "INITIAL_AUTO_READAHEAD_SIZE": {"value": 8192, "source": "cnf.rs:RocksDbConfig::default"},
        "MAX_AUTO_READAHEAD_SIZE": {"value": tiered(mem, [(GIB, 512 * KIB), (4 * GIB, MIB)], 4 * MIB), "source": "cnf.rs:default_max_auto_readahead_size"},
        "FILE_READS_FOR_AUTO_READAHEAD": {"value": 2, "source": "cnf.rs:RocksDbConfig::default"},
        "PREFIX_EXTRACTOR_ENABLED": {"value": 1, "source": "cnf.rs:RocksDbConfig::default"},
        "WHOLE_KEY_FILTERING": {"value": 1, "source": "cnf.rs:RocksDbConfig::default"},
        "MEMTABLE_PREFIX_BLOOM_RATIO": {"value": 0.1, "source": "cnf.rs:RocksDbConfig::default"},
        "GROUPED_COMMIT_TIMEOUT_NS": {"value": 5_000_000, "source": "cnf.rs:RocksDbConfig::default"},
        "GROUPED_COMMIT_WAIT_THRESHOLD": {"value": 12, "source": "cnf.rs:RocksDbConfig::default"},
        "GROUPED_COMMIT_MAX_BATCH_SIZE": {"value": tiered(mem, [(GIB, 256), (4 * GIB, 1024)], 4096), "source": "cnf.rs:default_grouped_commit_max_batch_size"},
        "INLINE_SCAN_THRESHOLD": {"value": tiered(mem, [(GIB, 512 * KIB), (4 * GIB, MIB)], 4 * MIB), "source": "cnf.rs:default_inline_scan_threshold"},
        "SST_MAX_ALLOWED_SPACE_USAGE": {"value": 0, "source": "cnf.rs:RocksDbConfig::default"},
    }

    applied_options = {
        "USE_FSYNC": {"value": 0, "source": "mod.rs:Datastore::new"},
        "CREATE_IF_MISSING": {"value": 1, "source": "mod.rs:Datastore::new"},
        "CREATE_MISSING_COLUMN_FAMILIES": {"value": 1, "source": "mod.rs:Datastore::new"},
        "MANUAL_WAL_FLUSH": {"value": 1, "source": "commit_coordinator.rs:CommitCoordinator::configure"},
        "WAL_BYTES_PER_SYNC": {"value": 512 * KIB, "source": "commit_coordinator.rs:CommitCoordinator::configure"},
        "ALLOW_CONCURRENT_MEMTABLE_WRITE": {"value": 1, "source": "mod.rs:Datastore::new"},
        "AVOID_UNNECESSARY_BLOCKING_IO": {"value": 1, "source": "mod.rs:Datastore::new"},
        "ENABLE_WRITE_THREAD_ADAPTIVE_YIELD": {"value": 1, "source": "mod.rs:Datastore::new"},
        "COMPRESSION_PER_LEVEL": {"value": ["none", "lz4", "lz4", "lz4", "lz4", "zstd", "zstd", "zstd"], "source": "mod.rs:apply_cf_level_options"},
        "BOTTOMMOST_COMPRESSION": {"value": "zstd", "source": "mod.rs:apply_cf_level_options"},
        "BOTTOMMOST_ZSTD_MAX_TRAIN_BYTES": {"value": 0, "source": "mod.rs:apply_cf_level_options"},
        "BLOOM_FILTER_BITS_PER_KEY": {"value": 10, "source": "memory_manager.rs:apply_to_cf_options"},
        "BLOOM_FILTER_BLOCK_BASED": {"value": 0, "source": "memory_manager.rs:apply_to_cf_options"},
        "INDEX_TYPE": {"value": "two_level_index_search", "source": "memory_manager.rs:apply_to_cf_options"},
        "PARTITION_INDEX_AND_FILTERS": {"value": 1, "source": "memory_manager.rs:apply_to_cf_options"},
        "METADATA_BLOCK_SIZE": {"value": 4096, "source": "memory_manager.rs:apply_to_cf_options"},
        "PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE": {"value": 1, "source": "memory_manager.rs:apply_to_cf_options"},
        "PIN_TOP_LEVEL_INDEX_AND_FILTER": {"value": 1, "source": "memory_manager.rs:apply_to_cf_options"},
        "BLOCK_CACHE_SHARED_WITH_ROW_CACHE_AND_BLOB_CACHE": {"value": 1, "source": "memory_manager.rs:configure"},
        "WRITE_BUFFER_MANAGER_LIMIT_BYTES": {"value": block_cache_bytes + write_buffer_mb * MIB * max_write_buffers, "source": "memory_manager.rs:configure"},
    }

    baseline_env = {key: item["value"] for key, item in config_defaults.items() if key in rdbtools.option_catalog()}
    baseline_env.update({key: item["value"] for key, item in applied_options.items() if key in rdbtools.option_catalog()})
    baseline_env["COMPACTION_STYLE"] = "blob" if enable_blob_files else "leveled"
    baseline_env["PER_LEVEL_FANOUT"] = 10
    baseline_env["COMPRESSION_TYPE"] = "lz4"
    baseline_env["MIN_LEVEL_TO_COMPRESS"] = 1
    baseline_env.pop("ENABLE_BLOB_FILES", None)

    if not has_flag(capabilities, "--bottommost_compression_type"):
        warnings.append("db_bench does not expose --bottommost_compression_type; SurrealDB bottommost Zstd is approximate")
        baseline_env.pop("BOTTOMMOST_COMPRESSION", None)
    if "CACHE_SIZE" in baseline_env:
        raise RdbTuneError("canonical cache env drift: use CACHE_SIZE_MB, not CACHE_SIZE")
    for flag_key, flag in {
        "MANUAL_WAL_FLUSH": "--manual_wal_flush",
        "WAL_BYTES_PER_SYNC": "--wal_bytes_per_sync",
        "PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE": "--pin_l0_filter_and_index_blocks_in_cache",
        "PIN_TOP_LEVEL_INDEX_AND_FILTER": "--pin_top_level_index_and_filter",
    }.items():
        if flag_key in baseline_env and not has_flag(capabilities, flag):
            warnings.append(f"{flag_key} maps to {flag}, but selected db_bench did not advertise it")

    surreal_crate = cargo_version(surrealdb_root, "rocksdb")
    rust_crate = cargo_version(rust_rocksdb_root)
    if surreal_crate and rust_crate and surreal_crate != rust_crate:
        warnings.append(f"SurrealDB Cargo.toml references surrealdb-rocksdb {surreal_crate}, local rust-rocksdb is {rust_crate}")

    return {
        "machine": machine,
        "config_defaults": config_defaults,
        "applied_options": applied_options,
        "rust_rocksdb_defaults": {
            "source": "rust-rocksdb Options::default and BlockBasedOptions::default call vendored RocksDB C/C++ constructors",
            "rust_rocksdb_root": str(rust_rocksdb_root),
            "rust_rocksdb_version": rust_crate,
            "surrealdb_rocksdb_version": surreal_crate,
            "librocksdb_sys_version": cargo_version(rust_rocksdb_root / "librocksdb-sys"),
        },
        "db_bench_baseline_env": baseline_env,
        "source": {
            "surrealdb_root": str(surrealdb_root),
            "rust_rocksdb_root": str(rust_rocksdb_root),
            "git": {
                "rdbtools": git_hash(ROOT),
                "surrealdb": git_hash(surrealdb_root),
                "rust_rocksdb": git_hash(rust_rocksdb_root),
            },
        },
        "warnings": warnings,
    }


def option_values_around(default: int | float, values: list[Any]) -> list[Any]:
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    if default not in unique:
        unique.append(default)
    return sorted(unique)


def load_tuning_profiles() -> dict[str, Any]:
    path = ROOT / "workloads" / "tuning-profiles.json"
    if path.exists():
        return read_json(path)
    return {
        "lsm-write-heavy": {"env": {"NUM_KEYS": 40000000, "VALUE_SIZE": 400, "NUM_THREADS": 1}},
        "blob-heavy": {"env": {"NUM_KEYS": 10000000, "VALUE_SIZE": 8192, "NUM_THREADS": 1}},
        "scan-heavy": {"env": {"NUM_KEYS": 40000000, "VALUE_SIZE": 400, "NUM_THREADS": 1}},
    }


def build_group_specs(baseline: dict[str, Any], workload: dict[str, Any], budget: str, capabilities: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    max_groups = {
        "smoke": ["baseline", "target_file_size_base", "write_buffer_size", "compaction_readahead"],
        "standard": ["baseline", "target_file_size_base", "write_buffer_size", "compaction_readahead", "background_work", "table_layout", "cache_shape", "blob_shape", "scan_readahead", "wal_behavior"],
        "exhaustive": ["baseline", "target_file_size_base", "write_buffer_size", "lsm_memory_shape", "compaction_readahead", "background_work", "table_layout", "cache_shape", "blob_shape", "scan_readahead", "wal_behavior"],
    }[budget]
    groups: list[tuple[str, list[dict[str, Any]]]] = [("baseline", [])]
    env = baseline
    if budget == "smoke":
        target_values = [64 if env["TARGET_FILE_SIZE_BASE_MB"] != 64 else 32]
        write_values = [128 if env["WRITE_BUFFER_SIZE_MB"] != 128 else 64]
        readahead_values = [2097152 if env["COMPACTION_READAHEAD_SIZE"] != 2097152 else 262144]
    else:
        target_values = option_values_around(env["TARGET_FILE_SIZE_BASE_MB"], [8, 16, 32, 64])
        write_values = option_values_around(env["WRITE_BUFFER_SIZE_MB"], [32, 64, 128])
        readahead_values = option_values_around(env["COMPACTION_READAHEAD_SIZE"], [262144, 491520, 2097152, 8388608])
    all_groups = {
        "target_file_size_base": [{"name": "target_file_size_base", "mode": "one_at_a_time", "params": {"TARGET_FILE_SIZE_BASE_MB": target_values}}],
        "write_buffer_size": [{"name": "write_buffer_size", "mode": "one_at_a_time", "params": {"WRITE_BUFFER_SIZE_MB": write_values}}],
        "lsm_memory_shape": [
            {
                "name": "lsm_memory_shape",
                "mode": "cases",
                "cases": [
                    {"id": "small", "updates": {"WRITE_BUFFER_SIZE_MB": 32, "MAX_WRITE_BUFFER_NUMBER": 4, "TARGET_FILE_SIZE_BASE_MB": 32}},
                    {"id": "default", "updates": {"WRITE_BUFFER_SIZE_MB": env["WRITE_BUFFER_SIZE_MB"], "MAX_WRITE_BUFFER_NUMBER": env["MAX_WRITE_BUFFER_NUMBER"], "TARGET_FILE_SIZE_BASE_MB": env["TARGET_FILE_SIZE_BASE_MB"]}},
                    {"id": "large", "updates": {"WRITE_BUFFER_SIZE_MB": 128, "MAX_WRITE_BUFFER_NUMBER": min(max(env["MAX_WRITE_BUFFER_NUMBER"], 8), 32), "TARGET_FILE_SIZE_BASE_MB": 64}},
                ],
            }
        ],
        "compaction_readahead": [{"name": "compaction_readahead", "mode": "one_at_a_time", "params": {"COMPACTION_READAHEAD_SIZE": readahead_values}}],
        "background_work": [
            {
                "name": "background_work",
                "mode": "cases",
                "cases": [
                    {"id": "less", "updates": {"MAX_BACKGROUND_JOBS": max(2, env["MAX_BACKGROUND_JOBS"] // 2), "SUBCOMPACTIONS": max(1, env["SUBCOMPACTIONS"] // 2)}},
                    {"id": "default", "updates": {"MAX_BACKGROUND_JOBS": env["MAX_BACKGROUND_JOBS"], "SUBCOMPACTIONS": env["SUBCOMPACTIONS"]}},
                    {"id": "more", "updates": {"MAX_BACKGROUND_JOBS": env["MAX_BACKGROUND_JOBS"], "SUBCOMPACTIONS": min(4, env["SUBCOMPACTIONS"] + 1)}},
                ],
            }
        ],
        "table_layout": [
            {
                "name": "table_layout",
                "mode": "cases",
                "cases": [
                    {"id": "surrealdb", "updates": {"BLOCK_SIZE": 65536, "PARTITION_INDEX_AND_FILTERS": 1, "METADATA_BLOCK_SIZE": 4096, "PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE": 1, "PIN_TOP_LEVEL_INDEX_AND_FILTER": 1}},
                    {"id": "legacy_metadata", "updates": {"METADATA_BLOCK_SIZE": 16384}},
                    {"id": "cache_index_filter", "updates": {"CACHE_INDEX_AND_FILTER_BLOCKS": 1}},
                ],
            }
        ],
        "cache_shape": [{"name": "cache_shape", "mode": "one_at_a_time", "params": {"CACHE_SIZE_MB": option_values_around(env["CACHE_SIZE_MB"], [max(1024, env["CACHE_SIZE_MB"] // 2), env["CACHE_SIZE_MB"], env["CACHE_SIZE_MB"] * 2])}}],
        "blob_shape": [
            {
                "name": "blob_shape",
                "mode": "cases",
                "cases": [
                    {"id": "no_blob", "updates": {"COMPACTION_STYLE": "leveled"}},
                    {"id": "surrealdb_blob", "updates": {"COMPACTION_STYLE": "blob", "MIN_BLOB_SIZE": 4096, "BLOB_FILE_SIZE": env["BLOB_FILE_SIZE"], "BLOB_COMPRESSION_TYPE": "snappy", "ENABLE_BLOB_GC": 1, "BLOB_GC_AGE_CUTOFF": 0.5, "BLOB_GC_FORCE_THRESHOLD": 0.5}},
                    {"id": "larger_blob_file", "updates": {"COMPACTION_STYLE": "blob", "BLOB_FILE_SIZE": max(env["BLOB_FILE_SIZE"], 256 * MIB)}},
                ],
            }
        ],
        "scan_readahead": [{"name": "scan_readahead", "mode": "matrix", "params": {"MAX_AUTO_READAHEAD_SIZE": option_values_around(env["MAX_AUTO_READAHEAD_SIZE"], [512 * KIB, MIB, 4 * MIB]), "FILE_READS_FOR_AUTO_READAHEAD": [0, 2]}}],
        "wal_behavior": [{"name": "wal_behavior", "mode": "cases", "cases": [{"id": "surrealdb", "updates": {"WAL_SIZE_LIMIT_MB": env["WAL_SIZE_LIMIT_MB"], "WAL_BYTES_PER_SYNC": 512 * KIB, "MANUAL_WAL_FLUSH": 1}}, {"id": "rocksdb_default_flush", "updates": {"MANUAL_WAL_FLUSH": 0, "WAL_BYTES_PER_SYNC": 0}}]}],
    }
    for name in max_groups:
        if name == "baseline":
            continue
        if name == "scan_readahead" and not workload.get("scan_heavy"):
            continue
        if name == "blob_shape" and not workload.get("blob_meaningful") and int(workload["env"].get("VALUE_SIZE", 0)) < int(env["MIN_BLOB_SIZE"]):
            continue
        groups.append((name, all_groups[name]))
    return groups


def variant_count(sweeps: list[dict[str, Any]]) -> int:
    count = 1
    for sweep in sweeps:
        if sweep["mode"] == "one_at_a_time":
            count += sum(len(values) for values in sweep.get("params", {}).values())
        elif sweep["mode"] == "matrix":
            product = 1
            for values in sweep.get("params", {}).values():
                product *= len(values)
            count += product
        elif sweep["mode"] == "cases":
            count += len(sweep.get("cases", []))
    return count


def tuning_id_for(payload: dict[str, Any]) -> str:
    stable = json.dumps(payload, sort_keys=True, default=str)
    return "tune-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:12]


def make_spec(
    name: str,
    output_dir: Path,
    binary: str,
    db_dir: str,
    baseline_env: dict[str, Any],
    workload: dict[str, Any],
    sweeps: list[dict[str, Any]],
    warnings: list[str],
    smoke: bool,
) -> dict[str, Any]:
    workload = dict(workload)
    workload.setdefault("name", workload.get("id", "tuning-workload"))
    return {
        "name": f"machine-aware-{name}",
        "pipeline": "lsm-default",
        "binaries": [binary],
        "db_dir": db_dir,
        "output_dir": str(output_dir),
        "machine": {"name": "detected-inline", "env": {}},
        "workloads": [workload],
        "baseline": baseline_env,
        "sweeps": sweeps,
        "warnings": warnings,
        "execution": {
            "clean_env": True,
            "cooldown_seconds": 0,
            "remove_db_before_version": True,
            "smoke": smoke,
            "tuner_generated": True,
        },
    }


def dry_run_for_specs(spec_paths: list[Path]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for spec_path in spec_paths:
        spec = read_json(spec_path)
        planned = rdbtools.expand_plan(spec)
        payload.append(
            {
                "spec": str(spec_path),
                "command": f"python3 src/rdbtools.py run --spec {spec_path} --dry-run --json",
                "runs": [
                    {
                        "run_id": run.run_id,
                        "binary": run.binary.label,
                        "binary_path": str(run.binary.path),
                        "workload": run.workload,
                        "output_dir": str(run.output_dir),
                        "variant": run.variant,
                        "env": run.env,
                        "warnings": run.warnings,
                        "phases": [
                            {
                                "name": phase["name"],
                                "job": phase["job"],
                                "env": phase["resolved_env"],
                                "cmd": rdbtools.db_bench_command(
                                    run.binary.path,
                                    phase,
                                    phase["resolved_env"],
                                    run.output_dir,
                                ),
                            }
                            for phase in run.phases
                        ],
                    }
                    for run in planned
                ],
            }
        )
    return payload


def generate_plan(args: argparse.Namespace) -> Path:
    db_dir = resolve_path(args.db_dir, Path.cwd())
    machine = detect_machine(db_dir)
    capabilities = detect_binary_capabilities(args.binary)
    defaults = derive_surrealdb_baseline(machine, resolve_path(args.surrealdb, Path.cwd()), resolve_path(args.rust_rocksdb, Path.cwd()), capabilities)
    profiles = load_tuning_profiles()
    if args.workload not in profiles:
        raise RdbTuneError(f"unknown tuning workload: {args.workload}")
    workload = dict(profiles[args.workload])
    workload.setdefault("name", args.workload)
    baseline_env = defaults["db_bench_baseline_env"]
    plan_fingerprint = {
        "machine": machine,
        "baseline": baseline_env,
        "workload": args.workload,
        "budget": args.budget,
        "objective": args.objective,
        "binary": args.binary,
        "db_dir": str(db_dir),
    }
    tuning_id = tuning_id_for(plan_fingerprint)
    root = ROOT / "runs" / "tuning" / tuning_id
    groups = build_group_specs(baseline_env, workload, args.budget, capabilities)
    max_variants = {"smoke": 8, "standard": 40, "exhaustive": args.max_runs or 200}[args.budget]
    total = sum(variant_count(sweeps) for _, sweeps in groups)
    if total > max_variants:
        raise RdbTuneError(f"generated {total} variants exceeds {args.budget} budget of {max_variants}; reduce groups or use --budget exhaustive")

    spec_paths: list[Path] = []
    for idx, (group_name, sweeps) in enumerate(groups):
        spec_name = f"{idx * 10:03d}-{group_name}"
        spec_path = root / "specs" / f"{spec_name}.json"
        spec = make_spec(
            spec_name,
            root / "results" / spec_name,
            args.binary,
            str(db_dir),
            baseline_env,
            workload,
            sweeps,
            defaults["warnings"],
            args.budget == "smoke",
        )
        write_json(spec_path, spec)
        spec_paths.append(spec_path)

    dry_run = dry_run_for_specs(spec_paths)
    metadata = {
        "tuning_id": tuning_id,
        "created_at": int(time.time()),
        "budget": args.budget,
        "objective": args.objective,
        "workload": args.workload,
        "binary": args.binary,
        "db_dir": str(db_dir),
        "variant_count": total,
        "specs": [str(path) for path in spec_paths],
        "warnings": defaults["warnings"],
        "capabilities": capabilities,
        "source": defaults["source"],
    }
    write_json(root / "machine.json", machine)
    write_json(root / "defaults.json", defaults)
    write_json(root / "plan.json", metadata)
    write_json(root / "dry-run.json", dry_run)
    print(root / "plan.json")
    return root / "plan.json"


def run_plan(plan_path: Path) -> None:
    plan = read_json(plan_path)
    for spec in plan["specs"]:
        spec_path = Path(spec)
        spec_payload = read_json(spec_path)
        planned = rdbtools.expand_plan(spec_payload)
        rdbtools.run_planned(spec_payload, planned)
    aggregate = rdbtools.collect(plan_path.parent / "results")
    target = plan_path.parent / "results" / "aggregate.tsv"
    if aggregate != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(aggregate, target)
    print(target)


def maybe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def normalized_higher(value: float | None, baseline: float | None) -> float:
    if value is None or baseline in (None, 0):
        return 0.0
    return value / baseline


def normalized_lower(value: float | None, baseline: float | None) -> float:
    if value is None or baseline in (None, 0):
        return 0.0
    return baseline / value if value else 0.0


def rank_run(run_root: Path, objective: str) -> dict[str, Any]:
    aggregate = run_root / "results" / "aggregate.tsv"
    if not aggregate.exists():
        aggregate = run_root / "aggregate.tsv"
    if not aggregate.exists():
        aggregate = rdbtools.collect(run_root / "results")
    rows = list(csv.DictReader(aggregate.open("r", encoding="utf-8"), delimiter="\t"))
    baseline_rows = {(r["workload"], r["phase"]): r for r in rows if r["run_id"] == "baseline"}
    ranked: list[dict[str, Any]] = []
    for row in rows:
        if row["run_id"] == "baseline":
            continue
        baseline = baseline_rows.get((row["workload"], row["phase"]))
        if not baseline:
            continue
        throughput = normalized_higher(maybe_float(row.get("ops_sec")), maybe_float(baseline.get("ops_sec")))
        ssd = (normalized_lower(maybe_float(row.get("w_amp")), maybe_float(baseline.get("w_amp"))) + normalized_lower(maybe_float(row.get("c_wgb")), maybe_float(baseline.get("c_wgb")))) / 2
        stall = normalized_lower(maybe_float(row.get("stall_pct")), maybe_float(baseline.get("stall_pct"))) or 1.0
        read = throughput if "read" in row["phase"] or "range" in row["phase"] else 1.0
        if objective == "throughput":
            score = throughput
        elif objective == "ssd_efficiency":
            score = ssd
        elif objective == "scan_heavy":
            score = throughput if "range" in row["phase"] else throughput * 0.25
        elif objective == "blob_heavy":
            score = 0.6 * throughput + 0.4 * ssd
        else:
            score = 0.45 * throughput + 0.30 * ssd + 0.15 * stall + 0.10 * read
        ranked.append(
            {
                "run_id": row["run_id"],
                "workload": row["workload"],
                "phase": row["phase"],
                "score": score,
                "ops_sec": row.get("ops_sec", ""),
                "baseline_ops_sec": baseline.get("ops_sec", ""),
                "needs_confirmation": abs(throughput - 1.0) < 0.05,
                "insufficient_evidence": not row.get("ops_sec"),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    defaults_path = run_root / "defaults.json"
    defaults = read_json(defaults_path) if defaults_path.exists() else {}
    report = {
        "machine": defaults.get("machine"),
        "baseline_env": defaults.get("db_bench_baseline_env"),
        "objective": objective,
        "ranked_candidates": ranked,
        "best_by_group": ranked[:10],
        "ties": [item for item in ranked if ranked and abs(item["score"] - ranked[0]["score"]) < 0.03],
        "warnings": defaults.get("warnings", []),
        "insufficient_evidence": [item for item in ranked if item["insufficient_evidence"]],
        "next_runs": [],
    }
    write_json(run_root / "recommendations.json", report)
    top = ranked[0] if ranked else None
    md = ["# Machine-Aware RocksDB Recommendations", ""]
    if top:
        md.extend(
            [
                f"Top recommendation: `{top['run_id']}` on phase `{top['phase']}`.",
                "",
                f"Score: `{top['score']:.4f}`. Ops/sec: `{top['ops_sec']}` versus baseline `{top['baseline_ops_sec']}`.",
                "",
                "Follow-up confirmation command:",
                "",
                f"```bash\nscripts/rdb-tune.sh rank --run {run_root} --objective {objective}\n```",
            ]
        )
    else:
        md.append("No ranked candidates were available. Check aggregate.tsv for missing metrics.")
    (run_root / "recommendations.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def cmd_detect(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = detect_machine(resolve_path(args.db_dir, Path.cwd()) if args.db_dir else None)
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload)
    return 0


def cmd_defaults(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surrealdb", required=True)
    parser.add_argument("--rust-rocksdb", required=True)
    parser.add_argument("--binary")
    parser.add_argument("--db-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    machine = detect_machine(resolve_path(args.db_dir, Path.cwd()) if args.db_dir else None)
    payload = derive_surrealdb_baseline(machine, resolve_path(args.surrealdb, Path.cwd()), resolve_path(args.rust_rocksdb, Path.cwd()), detect_binary_capabilities(args.binary))
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload)
    return 0


def cmd_plan(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--db-dir", required=True)
    parser.add_argument("--workload", required=True, choices=["lsm-write-heavy", "blob-heavy", "scan-heavy"])
    parser.add_argument("--budget", choices=["smoke", "standard", "exhaustive"], default="standard")
    parser.add_argument("--objective", choices=["throughput", "ssd_efficiency", "balanced", "scan_heavy", "blob_heavy"], default="balanced")
    parser.add_argument("--surrealdb", required=True)
    parser.add_argument("--rust-rocksdb", required=True)
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--dry-run-only", action="store_true")
    args = parser.parse_args(argv)
    generate_plan(args)
    return 0


def cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    run_plan(resolve_path(args.plan, Path.cwd()))
    return 0


def cmd_rank(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--objective", choices=["throughput", "ssd_efficiency", "balanced", "scan_heavy", "blob_heavy"], default="balanced")
    args = parser.parse_args(argv)
    report = rank_run(resolve_path(args.run, Path.cwd()), args.objective)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: rdbtune.py <detect|defaults|plan|run|rank> ...", file=sys.stderr)
        return 2
    cmd, argv = sys.argv[1], sys.argv[2:]
    try:
        if cmd == "detect":
            return cmd_detect(argv)
        if cmd == "defaults":
            return cmd_defaults(argv)
        if cmd == "plan":
            return cmd_plan(argv)
        if cmd == "run":
            return cmd_run(argv)
        if cmd == "rank":
            return cmd_rank(argv)
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    except (RdbTuneError, rdbtools.RdbtoolsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
