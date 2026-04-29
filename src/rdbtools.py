#!/usr/bin/env python3
"""Shared implementation for the initial rdbtools scripts."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class RdbtoolsError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def as_path(path: str | Path, base: Path = ROOT) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return base / path


def env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return text or "value"


def merge_env(*maps: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in maps:
        merged.update(item or {})
    if str(merged.get("USE_O_DIRECT", "0")) == "1" and "BYTES_PER_SYNC" not in merged:
        merged["BYTES_PER_SYNC"] = 0
    return merged


def named_profile(kind: str, name: str) -> dict[str, Any]:
    direct = ROOT / kind / f"{name}.json"
    if direct.exists():
        return load_json(direct)
    grouped = ROOT / kind / "profiles.json"
    if grouped.exists():
        profiles = load_json(grouped)
        if name in profiles:
            profile = profiles[name]
            profile.setdefault("name", name)
            return profile
    raise RdbtoolsError(f"Unknown {kind.rstrip('s')} profile: {name}")


def load_pipeline(spec: dict[str, Any]) -> dict[str, Any]:
    pipeline = spec.get("pipeline", "mark-rx2")
    if isinstance(pipeline, dict):
        return pipeline
    path = ROOT / "pipelines" / f"{pipeline}.json"
    if not path.exists():
        raise RdbtoolsError(f"Unknown pipeline: {pipeline}")
    return load_json(path)


def option_catalog() -> dict[str, dict[str, Any]]:
    data = load_json(ROOT / "catalog" / "options.json")
    return {item["name"]: item for item in data.get("options", [])}


def validate_spec(spec: dict[str, Any], *, require_binaries: bool = False) -> list[str]:
    required = ["name", "binaries", "db_dir", "output_dir", "machine", "workloads", "baseline"]
    errors = [f"Missing required field: {key}" for key in required if key not in spec]
    if errors:
        return errors

    catalog = option_catalog()
    known = set(catalog)

    def check_env(scope: str, env: dict[str, Any]) -> None:
        if "DURATION" in env and "WRITES" in env:
            if str(env.get("DURATION", "0")) not in ("", "0") and str(env.get("WRITES", "0")) not in ("", "0"):
                errors.append(f"{scope}: DURATION and WRITES should not both be non-zero")
        if "CACHE_INDEX_AND_FILTER_BLOCKS" in env and str(env["CACHE_INDEX_AND_FILTER_BLOCKS"]) not in ("0", "1"):
            errors.append(f"{scope}: CACHE_INDEX_AND_FILTER_BLOCKS must be 0 or 1")
        for key in env:
            if key.isupper() and key not in known and not key.endswith("_EXPR"):
                errors.append(f"{scope}: unknown option {key}; add it to catalog/options.json if intentional")

    check_env("baseline", spec.get("baseline", {}))

    machine = spec.get("machine")
    if isinstance(machine, str):
        named_profile("machines", machine)
    elif not isinstance(machine, dict):
        errors.append("machine must be a profile name or object")

    for workload in spec.get("workloads", []):
        if isinstance(workload, str):
            named_profile("workloads", workload)
        elif isinstance(workload, dict):
            check_env(f"workload:{workload.get('name', '<inline>')}", workload.get("env", {}))
        else:
            errors.append("workloads entries must be profile names or objects")

    for sweep in spec.get("sweeps", []):
        mode = sweep.get("mode")
        if mode not in {"one_at_a_time", "matrix", "cases"}:
            errors.append(f"sweep {sweep.get('name')}: mode must be one_at_a_time, matrix, or cases")
        if mode == "cases":
            cases = sweep.get("cases", [])
            if not isinstance(cases, list) or not cases:
                errors.append(f"sweep {sweep.get('name')}: cases must have at least one entry")
            for idx, case in enumerate(cases):
                updates = case.get("updates", {}) if isinstance(case, dict) else {}
                if not updates:
                    errors.append(f"sweep {sweep.get('name')}: case {idx} must define updates")
                check_env(f"sweep:{sweep.get('name')}:case:{case.get('id', idx) if isinstance(case, dict) else idx}", updates)
        else:
            params = sweep.get("params", {})
            if not isinstance(params, dict) or not params:
                errors.append(f"sweep {sweep.get('name')}: params must have at least one entry")
            for param, values in sweep.get("params", {}).items():
                if param not in known:
                    errors.append(f"sweep {sweep.get('name')}: unknown option {param}")
                if not isinstance(values, list) or not values:
                    errors.append(f"sweep {sweep.get('name')}: {param} must have at least one value")

    try:
        load_pipeline(spec)
    except RdbtoolsError as exc:
        errors.append(str(exc))

    if require_binaries:
        for binary in spec.get("binaries", []):
            try:
                resolved = resolve_binary(spec, binary)
            except RdbtoolsError as exc:
                errors.append(str(exc))
                continue
            if not resolved.path.exists():
                errors.append(f"binary {resolved.label} not found at {resolved.path}")

    return errors


@dataclass(frozen=True)
class Binary:
    label: str
    path: Path
    metadata: dict[str, Any]


@dataclass
class PlannedRun:
    run_id: str
    binary: Binary
    workload: str
    variant: dict[str, Any]
    env: dict[str, Any]
    output_dir: Path
    phases: list[dict[str, Any]]
    warnings: list[str]


@dataclass
class DbBenchCommand:
    binary: Path
    benchmark_names: list[str]
    flags: list[tuple[str, Any | None]]
    ignored_dynamic_flags: list[str]

    def argv(self) -> list[str]:
        cmd = [str(self.binary), f"--benchmarks={','.join(self.benchmark_names)}"]
        for flag, value in self.flags:
            if value is None:
                cmd.append(flag)
            else:
                cmd.append(f"{flag}={value}")
        return cmd

    def normalized_flags(self) -> dict[str, str]:
        flags = {"benchmarks": ",".join(self.benchmark_names)}
        for flag, value in self.flags:
            key = flag.removeprefix("--")
            flags[key] = "true" if value is None else str(value)
        return flags


def resolve_binary(spec: dict[str, Any], binary: Any) -> Binary:
    if isinstance(binary, dict):
        label = binary["label"]
        path = as_path(binary["path"], ROOT)
        metadata = {key: value for key, value in binary.items() if key not in {"label", "path"}}
        return Binary(label=label, path=path, metadata=metadata)

    label = str(binary)
    candidates: list[Path] = []
    if spec.get("binary_dir"):
        candidates.append(as_path(spec["binary_dir"], ROOT) / f"db_bench.{label}")
        candidates.append(as_path(spec["binary_dir"], ROOT) / label)
    candidates.extend([ROOT / "bin" / f"db_bench.{label}", ROOT / f"db_bench.{label}", Path.cwd() / f"db_bench.{label}"])
    for candidate in candidates:
        if candidate.exists():
            return Binary(label=label, path=candidate, metadata={})
    return Binary(label=label, path=candidates[0], metadata={})


def sweep_variants(spec: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [{"run_id": "baseline", "sweep_name": "baseline", "mode": "baseline", "updates": {}}]
    for sweep in spec.get("sweeps", []):
        name = sweep["name"]
        params = sweep.get("params", {})
        if sweep["mode"] == "one_at_a_time":
            for param, values in params.items():
                for value in values:
                    variants.append(
                        {
                            "run_id": f"{name}__{param}_{slug(value)}",
                            "sweep_name": name,
                            "mode": "one_at_a_time",
                            "variable": param,
                            "value": value,
                            "updates": {param: value},
                        }
                    )
        elif sweep["mode"] == "matrix":
            keys = list(params)
            for values in itertools.product(*(params[key] for key in keys)):
                updates = dict(zip(keys, values, strict=True))
                run_id = name + "__" + "__".join(f"{key}_{slug(value)}" for key, value in updates.items())
                variants.append(
                    {
                        "run_id": run_id,
                        "sweep_name": name,
                        "mode": "matrix",
                        "updates": updates,
                    }
                )
        else:
            for idx, case in enumerate(sweep.get("cases", [])):
                case_id = case.get("id") or f"case_{idx + 1}"
                updates = case.get("updates", {})
                variants.append(
                    {
                        "run_id": f"{name}__{slug(case_id)}",
                        "sweep_name": name,
                        "mode": "cases",
                        "case_id": case_id,
                        "updates": updates,
                    }
                )
    return variants


def resolve_workload(workload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(workload, str):
        return named_profile("workloads", workload)
    return workload


def resolve_machine(machine: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(machine, str):
        return named_profile("machines", machine)
    return machine


def phase_env(base_env: dict[str, Any], phase: dict[str, Any]) -> dict[str, Any]:
    env = merge_env(base_env, phase.get("env", {}))
    if "WRITES_EXPR" in env:
        if env["WRITES_EXPR"] == "NUM_KEYS / NUM_THREADS / 10":
            num_keys = int(env.get("NUM_KEYS", 0))
            threads = max(int(env.get("NUM_THREADS", 1)), 1)
            env["WRITES"] = max(num_keys // threads // 10, 1)
            env.pop("WRITES_EXPR", None)
    if phase.get("rate_limited") and "DURATION" not in env:
        env["DURATION"] = env.get("DURATION_RW", 0)
    if not phase.get("rate_limited") and phase.get("job") in {"readrandom", "fwdrange", "revrange", "multireadrandom"}:
        env["DURATION"] = env.get("DURATION_RO", env.get("DURATION", 0))
    return env


def expand_plan(spec: dict[str, Any], *, only_run_id: str | None = None, smoke: bool | None = None) -> list[PlannedRun]:
    errors = validate_spec(spec)
    if errors:
        raise RdbtoolsError("\n".join(errors))

    pipeline = load_pipeline(spec)
    smoke = bool(spec.get("execution", {}).get("smoke", False)) if smoke is None else smoke
    phases = [phase for phase in pipeline["phases"] if not (smoke and phase.get("full_only"))]
    machine = resolve_machine(spec["machine"])
    machine_env = machine.get("env", machine if isinstance(machine, dict) else {})
    output_root = as_path(spec["output_dir"], ROOT)
    binaries = [resolve_binary(spec, binary) for binary in spec["binaries"]]
    variants = sweep_variants(spec)
    warnings = [str(item) for item in spec.get("warnings", [])]
    planned: list[PlannedRun] = []

    for binary in binaries:
        for workload_item in spec["workloads"]:
            workload = resolve_workload(workload_item)
            workload_name = workload.get("name") or str(workload_item)
            workload_env = workload.get("env", workload if isinstance(workload, dict) else {})
            for variant in variants:
                if only_run_id and variant["run_id"] != only_run_id:
                    continue
                env = merge_env(machine_env, workload_env, spec.get("baseline", {}), variant["updates"])
                env.setdefault("DB_BENCH_NO_SYNC", 1)
                env["DB_DIR"] = spec["db_dir"]
                if spec.get("wal_dir"):
                    env["WAL_DIR"] = spec["wal_dir"]
                env["JOB_ID"] = variant["run_id"]
                env["OUTPUT_DIR"] = str(output_root / variant["run_id"] / binary.label / workload_name)
                run_phases = [{**phase, "resolved_env": phase_env(env, phase)} for phase in phases]
                planned.append(
                    PlannedRun(
                        run_id=variant["run_id"],
                        binary=binary,
                        workload=workload_name,
                        variant=variant,
                        env=env,
                        output_dir=Path(env["OUTPUT_DIR"]),
                        phases=run_phases,
                        warnings=warnings,
                    )
                )
    return planned


def print_plan(planned: list[PlannedRun], *, json_output: bool = False) -> None:
    if json_output:
        payload = [
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
                        "name": p["name"],
                        "job": p["job"],
                        "env": p["resolved_env"],
                        "cmd": db_bench_command(
                            run.binary.path,
                            p,
                            p["resolved_env"],
                            run.output_dir,
                        ),
                        "db_bench_flags": db_bench_command_model(
                            run.binary.path,
                            p,
                            p["resolved_env"],
                            run.output_dir,
                        ).normalized_flags(),
                        "ignored_dynamic_flags": db_bench_command_model(
                            run.binary.path,
                            p,
                            p["resolved_env"],
                            run.output_dir,
                        ).ignored_dynamic_flags,
                    }
                    for p in run.phases
                ],
            }
            for run in planned
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(f"Resolved runs: {len(planned)}")
    print(f"Resolved phases: {sum(len(run.phases) for run in planned)}")
    for run in planned:
        print(f"- {run.run_id} | binary={run.binary.label} | workload={run.workload} | phases={len(run.phases)}")
        print(f"  binary_path={run.binary.path}")
        print(f"  output_dir={run.output_dir}")
        for warning in run.warnings:
            print(f"  warning={warning}")


REPORT_FIELDS = [
    "run_id",
    "binary",
    "workload",
    "phase",
    "job",
    "exit_code",
    "ops_sec",
    "mb_sec",
    "micros_per_op",
    "w_amp",
    "c_wgb",
    "c_mbps",
    "stall_pct",
    "rss_gb",
    "blob_read_gb",
    "blob_write_gb",
    "p50",
    "p95",
    "p99",
    "p99_9",
    "log",
]


def bytes_from_mb(value: Any) -> int:
    return int(float(value) * 1024 * 1024)


KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB


def truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def env_int(env: dict[str, Any], key: str, default: int) -> int:
    return int(float(env.get(key, default)))


def env_float(env: dict[str, Any], key: str, default: float) -> float:
    return float(env.get(key, default))


def env_str(env: dict[str, Any], key: str, default: str) -> str:
    value = env.get(key, default)
    return default if value in ("", None) else str(value)


def bool01(value: Any) -> str:
    return "1" if truthy(value) else "0"


def add_flag(flags: list[tuple[str, Any | None]], flag: str, value: Any | None = None) -> None:
    flags.append((flag, value))


def mark_sync_value(env: dict[str, Any]) -> int:
    return 0 if truthy(env.get("DB_BENCH_NO_SYNC", 1)) else 1


def mark_base_flags(env: dict[str, Any]) -> list[tuple[str, Any | None]]:
    flags: list[tuple[str, Any | None]] = []
    undef_params = (
        "use_blob_cache,blob_file_starting_level,use_shared_block_and_blob_cache,"
        "blob_cache_size,blob_cache_numshardbits,prepopulate_blob_cache,"
        "multiread_batched,cache_low_pri_pool_ratio,cache_type,prepopulate_block_cache"
    )
    add_flag(flags, "--undefok", undef_params)
    add_flag(flags, "--db", env["DB_DIR"])
    add_flag(flags, "--wal_dir", env.get("WAL_DIR", env["DB_DIR"]))
    add_flag(flags, "--num", env_int(env, "NUM_KEYS", 8000000000))
    add_flag(flags, "--key_size", env_int(env, "KEY_SIZE", 20))
    add_flag(flags, "--value_size", env_int(env, "VALUE_SIZE", 400))
    add_flag(flags, "--block_size", env_int(env, "BLOCK_SIZE", 8192))
    if "CACHE_SIZE" in env:
        cache_size = env_int(env, "CACHE_SIZE", 16 * GIB)
    else:
        cache_size = bytes_from_mb(env.get("CACHE_SIZE_MB", 16 * 1024))
    add_flag(flags, "--cache_size", cache_size)
    add_flag(flags, "--cache_numshardbits", env_int(env, "CACHE_NUMSHARDBITS", 6))
    add_flag(flags, "--cache_type", env_str(env, "CACHE_TYPE", "lru_cache"))
    add_flag(flags, "--compression_max_dict_bytes", env_int(env, "COMPRESSION_MAX_DICT_BYTES", 0))
    add_flag(flags, "--compression_ratio", env_float(env, "COMPRESSION_RATIO", 0.5))
    add_flag(flags, "--compression_type", env_str(env, "COMPRESSION_TYPE", "lz4"))

    use_o_direct = "USE_O_DIRECT" in env and str(env.get("USE_O_DIRECT", "")) != "0"
    add_flag(flags, "--bytes_per_sync", 0 if use_o_direct else env_int(env, "BYTES_PER_SYNC", MIB))
    if env_int(env, "CACHE_INDEX_AND_FILTER_BLOCKS", 0) == 1:
        add_flag(flags, "--cache_index_and_filter_blocks", 1)
        add_flag(flags, "--cache_high_pri_pool_ratio", env_float(env, "CACHE_HIGH_PRI_POOL_RATIO", 0.5))
        add_flag(flags, "--cache_low_pri_pool_ratio", env_int(env, "CACHE_LOW_PRI_POOL_RATIO", 0))
    add_flag(flags, "--partition_index_and_filters", bool01(env.get("PARTITION_INDEX_AND_FILTERS", 0)))
    add_flag(flags, "--pin_top_level_index_and_filter", bool01(env.get("PIN_TOP_LEVEL_INDEX_AND_FILTER", 0)))
    add_flag(flags, "--metadata_block_size", env_int(env, "METADATA_BLOCK_SIZE", 16384))
    if use_o_direct:
        add_flag(flags, "--use_direct_reads")
        add_flag(flags, "--use_direct_io_for_flush_and_compaction")
        add_flag(flags, "--prepopulate_block_cache", 1)
    add_flag(flags, "--benchmark_write_rate_limit", bytes_from_mb(env.get("MB_WRITE_PER_SEC", 0)))
    add_flag(flags, "--write_buffer_size", bytes_from_mb(env.get("WRITE_BUFFER_SIZE_MB", 128)))
    add_flag(flags, "--target_file_size_base", bytes_from_mb(env.get("TARGET_FILE_SIZE_BASE_MB", 128)))
    add_flag(flags, "--target_file_size_multiplier", env_int(env, "TARGET_FILE_SIZE_MULTIPLIER", 1))
    add_flag(flags, "--max_bytes_for_level_base", bytes_from_mb(env.get("MAX_BYTES_FOR_LEVEL_BASE_MB", 1024)))
    add_flag(flags, "--verify_checksum", bool01(env.get("VERIFY_CHECKSUM", 1)))
    add_flag(flags, "--delete_obsolete_files_period_micros", env_int(env, "DELETE_OBSOLETE_FILES_PERIOD_MICROS", 60 * MIB))
    add_flag(flags, "--max_bytes_for_level_multiplier", env_int(env, "PER_LEVEL_FANOUT", 8))
    add_flag(flags, "--statistics", env_int(env, "STATISTICS", 0))
    add_flag(flags, "--stats_per_interval", env_int(env, "STATS_PER_INTERVAL", 1))
    add_flag(flags, "--stats_interval_seconds", env_int(env, "STATS_INTERVAL_SECONDS", 20))
    add_flag(flags, "--report_interval_seconds", env_int(env, "REPORT_INTERVAL_SECONDS", 1))
    add_flag(flags, "--histogram", bool01(env.get("HISTOGRAM", 1)))
    add_flag(flags, "--memtablerep", env_str(env, "MEMTABLE_REP", "skip_list"))
    add_flag(flags, "--bloom_bits", env_int(env, "BLOOM_BITS", 10))
    add_flag(flags, "--open_files", env_int(env, "OPEN_FILES", -1))
    add_flag(flags, "--subcompactions", env_int(env, "SUBCOMPACTIONS", 1))
    add_flag(flags, "--compaction_readahead_size", env_int(env, "COMPACTION_READAHEAD_SIZE", 2 * MIB))
    add_flag(flags, "--initial_auto_readahead_size", env_int(env, "INITIAL_AUTO_READAHEAD_SIZE", 8 * KIB))
    add_flag(flags, "--max_auto_readahead_size", env_int(env, "MAX_AUTO_READAHEAD_SIZE", 256 * KIB))
    add_flag(flags, "--num_file_reads_for_auto_readahead", env_int(env, "FILE_READS_FOR_AUTO_READAHEAD", 2))
    add_flag(flags, "--wal_size_limit_MB", env_int(env, "WAL_SIZE_LIMIT_MB", 0))
    add_flag(flags, "--wal_bytes_per_sync", env_int(env, "WAL_BYTES_PER_SYNC", 0))
    add_flag(flags, "--manual_wal_flush", bool01(env.get("MANUAL_WAL_FLUSH", 0)))
    if truthy(env.get("MULTIREAD_BATCHED", 0)):
        add_flag(flags, "--multiread_batched")
    return flags


def mark_style_flags(env: dict[str, Any]) -> list[tuple[str, Any | None]]:
    flags = mark_base_flags(env)
    style = env_str(env, "COMPACTION_STYLE", "leveled")
    if style == "universal":
        add_flag(flags, "--compaction_style", 1)
        add_flag(flags, "--num_levels", env_int(env, "NUM_LEVELS", 40))
        add_flag(flags, "--universal_compression_size_percent", env_int(env, "UNIVERSAL_COMPRESSION_SIZE_PERCENT", -1))
        add_flag(flags, "--pin_l0_filter_and_index_blocks_in_cache", bool01(env.get("PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE", 0)))
        add_flag(flags, "--universal_min_merge_width", env_int(env, "UNIVERSAL_MIN_MERGE_WIDTH", 2))
        add_flag(flags, "--universal_max_merge_width", env_int(env, "UNIVERSAL_MAX_MERGE_WIDTH", 20))
        add_flag(flags, "--universal_size_ratio", env_int(env, "UNIVERSAL_SIZE_RATIO", 1))
        add_flag(flags, "--universal_max_size_amplification_percent", env_int(env, "UNIVERSAL_MAX_SIZE_AMP", 200))
        add_flag(flags, "--universal_allow_trivial_move", bool01(env.get("UNIVERSAL_ALLOW_TRIVIAL_MOVE", 0)))
        return flags

    add_flag(flags, "--compaction_style", 0)
    add_flag(flags, "--num_levels", env_int(env, "NUM_LEVELS", 8))
    add_flag(flags, "--min_level_to_compress", env_int(env, "MIN_LEVEL_TO_COMPRESS", -1))
    add_flag(flags, "--level_compaction_dynamic_level_bytes", str(truthy(env.get("LEVEL_COMPACTION_DYNAMIC_LEVEL_BYTES", 1))).lower())
    add_flag(flags, "--pin_l0_filter_and_index_blocks_in_cache", bool01(env.get("PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE", 1)))
    if style == "blob":
        add_flag(flags, "--enable_blob_files", "true")
        add_flag(flags, "--min_blob_size", env_int(env, "MIN_BLOB_SIZE", 0))
        add_flag(flags, "--blob_file_size", env_int(env, "BLOB_FILE_SIZE", 256 * MIB))
        add_flag(flags, "--blob_compression_type", env_str(env, "BLOB_COMPRESSION_TYPE", env_str(env, "COMPRESSION_TYPE", "zstd")))
        add_flag(flags, "--enable_blob_garbage_collection", "true" if truthy(env.get("ENABLE_BLOB_GC", 1)) else "false")
        add_flag(flags, "--blob_garbage_collection_age_cutoff", env_float(env, "BLOB_GC_AGE_CUTOFF", 0.25))
        add_flag(flags, "--blob_garbage_collection_force_threshold", env_float(env, "BLOB_GC_FORCE_THRESHOLD", 1.0))
        add_flag(flags, "--blob_file_starting_level", env_int(env, "BLOB_FILE_STARTING_LEVEL", 0))
        add_flag(flags, "--use_blob_cache", bool01(env.get("USE_BLOB_CACHE", 1)))
        add_flag(flags, "--use_shared_block_and_blob_cache", bool01(env.get("USE_SHARED_BLOCK_AND_BLOB_CACHE", 1)))
        add_flag(flags, "--blob_cache_size", env_int(env, "BLOB_CACHE_SIZE", 16 * GIB))
        add_flag(flags, "--blob_cache_numshardbits", env_int(env, "BLOB_CACHE_NUMSHARDBITS", 6))
        add_flag(flags, "--prepopulate_blob_cache", bool01(env.get("PREPOPULATE_BLOB_CACHE", 0)))
        add_flag(flags, "--blob_compaction_readahead_size", env_int(env, "BLOB_COMPACTION_READAHEAD_SIZE", 0))
    return flags


def mark_write_flags(env: dict[str, Any]) -> list[tuple[str, Any | None]]:
    flags = [
        ("--level0_file_num_compaction_trigger", env_int(env, "LEVEL0_FILE_NUM_COMPACTION_TRIGGER", 4)),
        ("--level0_slowdown_writes_trigger", env_int(env, "LEVEL0_SLOWDOWN_WRITES_TRIGGER", 20)),
        ("--level0_stop_writes_trigger", env_int(env, "LEVEL0_STOP_WRITES_TRIGGER", 30)),
        ("--max_background_jobs", env_int(env, "MAX_BACKGROUND_JOBS", 16)),
        ("--max_write_buffer_number", env_int(env, "MAX_WRITE_BUFFER_NUMBER", 8)),
        ("--min_write_buffer_number_to_merge", env_int(env, "MIN_WRITE_BUFFER_NUMBER_TO_MERGE", 1)),
    ]
    flags.extend(mark_style_flags(env))
    duration = env_int(env, "DURATION", 0)
    writes = env_int(env, "WRITES", 0)
    if duration > 0:
        add_flag(flags, "--duration", duration)
    if writes > 0:
        add_flag(flags, "--writes", writes)
    return flags


def benchmark_log_path(output_dir: Path, phase: dict[str, Any], env: dict[str, Any]) -> Path:
    job = str(phase.get("job"))
    threads = env_int(env, "NUM_THREADS", 64)
    value_size = env_int(env, "VALUE_SIZE", 400)
    sync = mark_sync_value(env)
    names = {
        "fillseq_disable_wal": f"benchmark_fillseq.wal_disabled.v{value_size}.log",
        "fillseq_enable_wal": f"benchmark_fillseq.wal_enabled.v{value_size}.log",
        "flush_mt_l0": "benchmark_flush_mt_l0.log",
        "flush_mt_wait": "benchmark_flush_mt_wait.log",
        "flush_mt_nowait": "benchmark_flush_mt_nowait.log",
        "waitforcompaction": "benchmark_waitforcompaction.log",
        "readrandom": f"benchmark_readrandom.t{threads}.log",
        "multireadrandom": f"benchmark_multireadrandom.t{threads}.log",
        "fwdrange": f"benchmark_fwdrange.t{threads}.log",
        "revrange": f"benchmark_revrange.t{threads}.log",
        "overwritesome": f"benchmark_overwritesome.t{threads}.s{sync}.log",
        "overwrite": f"benchmark_overwrite.t{threads}.s{sync}.log",
        "overwriteandwait": f"benchmark_overwriteandwait.t{threads}.s{sync}.log",
        "readwhilewriting": f"benchmark_readwhilewriting.t{threads}.log",
        "fwdrangewhilewriting": f"benchmark_fwdrangewhilewriting.t{threads}.log",
        "revrangewhilewriting": f"benchmark_revrangewhilewriting.t{threads}.log",
    }
    return output_dir / names.get(job, f"benchmark_{job}.log")


def seed_value(env: dict[str, Any]) -> int:
    return env_int(env, "SEED", int(time.time()))


def mark_command_model(binary: Path, phase: dict[str, Any], env: dict[str, Any], output_dir: Path) -> DbBenchCommand:
    job = str(phase.get("job"))
    flags = mark_write_flags(env)
    log_path = benchmark_log_path(output_dir, phase, env)
    seed = seed_value(env)
    ignored = [] if "SEED" in env else ["seed"]

    if job == "fillseq_disable_wal":
        benchmarks = ["fillseq", "stats"]
        add_flag(flags, "--min_level_to_compress", 0)
        add_flag(flags, "--use_existing_db", 0)
        add_flag(flags, "--sync", 0)
        add_flag(flags, "--threads", 1)
        add_flag(flags, "--memtablerep", "vector")
        add_flag(flags, "--allow_concurrent_memtable_write", "false")
        add_flag(flags, "--disable_wal", 1)
    elif job == "fillseq_enable_wal":
        benchmarks = ["fillseq", "stats"]
        add_flag(flags, "--min_level_to_compress", 0)
        add_flag(flags, "--use_existing_db", 0)
        add_flag(flags, "--sync", 0)
        add_flag(flags, "--threads", 1)
        add_flag(flags, "--memtablerep", "vector")
        add_flag(flags, "--allow_concurrent_memtable_write", "false")
        add_flag(flags, "--disable_wal", 0)
    elif job == "flush_mt_l0":
        benchmarks = ["levelstats", "flush", "waitforcompaction", "compact0", "waitforcompaction", "memstats", "levelstats", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", 0)
        add_flag(flags, "--threads", 1)
    elif job in {"flush_mt_wait", "flush_mt_nowait", "waitforcompaction"}:
        mapping = {
            "flush_mt_wait": ["levelstats", "flush", "waitforcompaction", "memstats", "levelstats", "stats"],
            "flush_mt_nowait": ["levelstats", "flush", "memstats", "levelstats", "stats"],
            "waitforcompaction": ["levelstats", "flush", "memstats", "levelstats", "waitforcompaction", "memstats", "levelstats", "stats"],
        }
        benchmarks = mapping[job]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", 0)
        add_flag(flags, "--threads", 1)
    elif job in {"fwdrange", "revrange"}:
        benchmarks = ["seekrandom", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--seek_nexts", env_int(env, "NUM_NEXTS_PER_SEEK", 10))
        add_flag(flags, "--reverse_iterator", "true" if job == "revrange" else "false")
    elif job == "multireadrandom":
        benchmarks = ["multireadrandom", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--batch_size", env_int(env, "BATCH_SIZE", 10))
    elif job == "readrandom":
        benchmarks = ["readrandom", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
    elif job in {"overwrite", "overwritesome"}:
        benchmarks = ["overwrite", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", mark_sync_value(env))
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--merge_operator", "put")
    elif job == "overwriteandwait":
        benchmarks = ["overwrite", "flush", "levelstats", "waitforcompaction", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", mark_sync_value(env))
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--merge_operator", "put")
    elif job == "readwhilewriting":
        benchmarks = ["readwhilewriting", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", mark_sync_value(env))
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--merge_operator", "put")
    elif job in {"fwdrangewhilewriting", "revrangewhilewriting"}:
        benchmarks = ["seekrandomwhilewriting", "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--sync", mark_sync_value(env))
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))
        add_flag(flags, "--merge_operator", "put")
        add_flag(flags, "--seek_nexts", env_int(env, "NUM_NEXTS_PER_SEEK", 10))
        add_flag(flags, "--reverse_iterator", "true" if job == "revrangewhilewriting" else "false")
    else:
        benchmarks = [job, "stats"]
        add_flag(flags, "--use_existing_db", 1)
        add_flag(flags, "--threads", env_int(env, "NUM_THREADS", 64))

    add_flag(flags, "--seed", seed)
    if job not in {"flush_mt_l0", "flush_mt_wait", "flush_mt_nowait", "waitforcompaction"}:
        add_flag(flags, "--report_file", f"{log_path}.r.csv")
        ignored.append("report_file")
    return DbBenchCommand(binary=binary, benchmark_names=benchmarks, flags=flags, ignored_dynamic_flags=ignored)


def db_bench_command_model(
    binary: Path,
    phase: dict[str, Any],
    env: dict[str, Any],
    output_dir: Path,
) -> DbBenchCommand:
    return mark_command_model(binary, phase, env, output_dir)


def db_bench_command(
    binary: Path,
    phase: dict[str, Any],
    env: dict[str, Any],
    output_dir: Path,
) -> list[str]:
    return db_bench_command_model(binary, phase, env, output_dir).argv()


def parse_db_bench_metrics(text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        match = re.search(r":\s+([0-9.]+)\s+micros/op\s+([0-9.]+)\s+ops/sec", line)
        if match:
            metrics["micros_per_op"] = float(match.group(1))
            metrics["ops_sec"] = float(match.group(2))
        mb_match = re.search(r"([0-9.]+)\s+MB/s", line)
        if mb_match:
            metrics["mb_sec"] = float(mb_match.group(1))
        for key, patterns in {
            "w_amp": [r"\bw_amp[:=]\s*([0-9.]+)", r"\bwrite_amp(?:lification)?[:=]\s*([0-9.]+)"],
            "c_wgb": [r"\bc_wgb[:=]\s*([0-9.]+)", r"\bcompaction_write_gb[:=]\s*([0-9.]+)"],
            "c_mbps": [r"\bc_mbps[:=]\s*([0-9.]+)", r"\bcompaction_mbps[:=]\s*([0-9.]+)"],
            "stall_pct": [r"\bstall(?:s|_pct|%)?[:=]\s*([0-9.]+)%?"],
            "rss_gb": [r"\brss(?:_gb)?[:=]\s*([0-9.]+)"],
            "blob_read_gb": [r"\bblob_read_gb[:=]\s*([0-9.]+)"],
            "blob_write_gb": [r"\bblob_write_gb[:=]\s*([0-9.]+)"],
            "p50": [r"\bp50[:=]\s*([0-9.]+)", r"\bP50[:=]\s*([0-9.]+)"],
            "p95": [r"\bp95[:=]\s*([0-9.]+)", r"\bP95[:=]\s*([0-9.]+)"],
            "p99": [r"\bp99[:=]\s*([0-9.]+)", r"\bP99[:=]\s*([0-9.]+)"],
            "p99_9": [r"\bp99\.9[:=]\s*([0-9.]+)", r"\bP99\.9[:=]\s*([0-9.]+)"],
        }.items():
            if key in metrics:
                continue
            for pattern in patterns:
                value_match = re.search(pattern, line, re.I)
                if value_match:
                    metrics[key] = float(value_match.group(1))
                    break
    return metrics


def run_command(cmd: list[str], log_path: Path, env: dict[str, str]) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, check=False)
        return proc.returncode


def assert_not_dangerous_db_dir(path: Path) -> None:
    resolved = path.resolve(strict=False)
    blocked = {
        Path("/").resolve(),
        Path.home().resolve(),
        ROOT.resolve(),
    }
    if resolved in blocked:
        raise RdbtoolsError(f"Refusing to use dangerous db_dir: {path}")


def assert_writable_directory(path: Path, label: str, *, create: bool) -> None:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
            target = path
        elif path.exists():
            if not path.is_dir():
                raise RdbtoolsError(f"{label} exists but is not a directory: {path}")
            target = path
        else:
            target = path.parent
            if not target.exists():
                raise RdbtoolsError(f"{label} parent directory does not exist: {target}")
            if not target.is_dir():
                raise RdbtoolsError(f"{label} parent is not a directory: {target}")

        with tempfile.NamedTemporaryFile(prefix=".rdbtools-write-test.", dir=target, delete=True):
            pass
    except PermissionError as exc:
        raise RdbtoolsError(f"No write permission for {label}: {path}") from exc
    except OSError as exc:
        raise RdbtoolsError(f"Cannot write to {label}: {path} ({exc})") from exc


def preflight_paths(planned: list[PlannedRun]) -> None:
    seen_outputs: set[Path] = set()
    seen_db_dirs: set[Path] = set()
    seen_wal_dirs: set[Path] = set()

    for run in planned:
        if not run.binary.path.exists():
            raise RdbtoolsError(f"Missing binary for {run.binary.label}: {run.binary.path}")

        output_dir = run.output_dir.resolve(strict=False)
        if output_dir not in seen_outputs:
            assert_writable_directory(output_dir, "output_dir", create=True)
            seen_outputs.add(output_dir)

        db_dir = Path(str(run.env["DB_DIR"]))
        assert_not_dangerous_db_dir(db_dir)
        resolved_db_dir = db_dir.resolve(strict=False)
        if resolved_db_dir not in seen_db_dirs:
            assert_writable_directory(db_dir, "db_dir", create=False)
            seen_db_dirs.add(resolved_db_dir)

        if run.env.get("WAL_DIR"):
            wal_dir = Path(str(run.env["WAL_DIR"]))
            resolved_wal_dir = wal_dir.resolve(strict=False)
            if resolved_wal_dir not in seen_wal_dirs:
                assert_writable_directory(wal_dir, "wal_dir", create=False)
                seen_wal_dirs.add(resolved_wal_dir)


def run_planned(spec: dict[str, Any], planned: list[PlannedRun], *, resume: bool = False, force: bool = False) -> None:
    execution = spec.get("execution", {})
    clean_env = bool(execution.get("clean_env", False))
    if execution.get("tuner_generated") and not clean_env:
        raise RdbtoolsError("tuner-generated specs require execution.clean_env=true")
    remove_db = bool(execution.get("remove_db_before_version", True))
    cooldown = int(execution.get("cooldown_seconds", 0))
    preflight_paths(planned)
    for run in planned:
        run.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(run.output_dir / "resolved-env.json", run.env)
        write_json(run.output_dir / "variant.json", run.variant)
        report_path = run.output_dir / "report.tsv"
        if force and report_path.exists():
            report_path.unlink()
        if not report_path.exists():
            with report_path.open("w", encoding="utf-8") as fh:
                fh.write("\t".join(REPORT_FIELDS) + "\n")

        if remove_db and Path(str(run.env["DB_DIR"])).exists() and not resume:
            shutil.rmtree(str(run.env["DB_DIR"]))

        for phase in run.phases:
            log_path = benchmark_log_path(run.output_dir, phase, phase["resolved_env"])
            if resume and log_path.exists():
                continue
            env_map = dict(os.environ if not clean_env else {})
            env_map.update({key: env_value(value) for key, value in phase["resolved_env"].items()})
            cmd = db_bench_command(
                run.binary.path,
                phase,
                phase["resolved_env"],
                run.output_dir,
            )
            exit_code = run_command(cmd, log_path, env_map)
            metrics = parse_db_bench_metrics(log_path.read_text(encoding="utf-8", errors="replace"))
            with report_path.open("a", encoding="utf-8") as fh:
                row = {
                    "run_id": run.run_id,
                    "binary": run.binary.label,
                    "workload": run.workload,
                    "phase": phase["name"],
                    "job": phase["job"],
                    "exit_code": str(exit_code),
                    "log": str(log_path),
                }
                row.update({field: str(metrics.get(field, "")) for field in REPORT_FIELDS if field not in row})
                fh.write("\t".join(row.get(field, "") for field in REPORT_FIELDS) + "\n")
            if exit_code != 0:
                raise RdbtoolsError(f"db_bench failed for {run.run_id}/{phase['name']}; see {log_path}")
        if cooldown:
            time.sleep(cooldown)


def collect(run_root: Path) -> Path:
    output = run_root / "aggregate.tsv"
    fields = REPORT_FIELDS
    rows: list[dict[str, str]] = []
    for report in sorted(run_root.glob("**/report.tsv")):
        with report.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                rows.append({field: row.get(field, "") for field in fields})
    with output.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return output


def compare(aggregate: Path, baseline: str, variant: str) -> Path:
    rows = list(csv.DictReader(aggregate.open("r", encoding="utf-8"), delimiter="\t"))
    base = {(r["binary"], r["workload"], r["phase"]): r for r in rows if r["run_id"] == baseline}
    out = aggregate.with_name(f"compare_{baseline}_vs_{variant}.tsv")
    fields = ["binary", "workload", "phase", "metric", "baseline", "variant", "delta_pct"]
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            if row["run_id"] != variant:
                continue
            key = (row["binary"], row["workload"], row["phase"])
            if key not in base:
                continue
            for metric in ["ops_sec", "mb_sec", "micros_per_op"]:
                try:
                    b = float(base[key][metric])
                    v = float(row[metric])
                except (TypeError, ValueError):
                    continue
                delta = "" if b == 0 else ((v - b) / b) * 100
                writer.writerow(
                    {
                        "binary": row["binary"],
                        "workload": row["workload"],
                        "phase": row["phase"],
                        "metric": metric,
                        "baseline": b,
                        "variant": v,
                        "delta_pct": delta,
                    }
                )
    return out


def cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only-run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    spec = load_json(as_path(args.spec, Path.cwd()))
    planned = expand_plan(spec, only_run_id=args.only_run_id, smoke=args.smoke or None)
    print_plan(planned, json_output=args.json)
    if not args.dry_run:
        run_planned(spec, planned, resume=args.resume, force=args.force)
    return 0


def cmd_validate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--require-binaries", action="store_true")
    args = parser.parse_args(argv)
    spec = load_json(as_path(args.spec, Path.cwd()))
    errors = validate_spec(spec, require_binaries=args.require_binaries)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_collect(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root")
    args = parser.parse_args(argv)
    print(collect(as_path(args.run_root, Path.cwd())))
    return 0


def cmd_compare(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("aggregate")
    args = parser.parse_args(argv)
    print(compare(as_path(args.aggregate, Path.cwd()), args.baseline, args.variant))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: rdbtools.py <run|validate|collect|compare> ...", file=sys.stderr)
        return 2
    cmd, argv = sys.argv[1], sys.argv[2:]
    try:
        if cmd == "run":
            return cmd_run(argv)
        if cmd == "validate":
            return cmd_validate(argv)
        if cmd == "collect":
            return cmd_collect(argv)
        if cmd == "compare":
            return cmd_compare(argv)
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    except RdbtoolsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
