#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

spec="$tmp_dir/spec.json"
out="$tmp_dir/dry-run.json"

cat > "$spec" <<'JSON'
{
  "name": "flag-parity-selftest",
  "binaries": [
    {
      "label": "test",
      "path": "/bin/echo"
    }
  ],
  "db_dir": "/data/m/rx",
  "output_dir": "runs/flag-parity-selftest",
  "machine": {},
  "workloads": [
    {
      "name": "400b",
      "env": {}
    }
  ],
  "pipeline": "lsm-default",
  "baseline": {
    "WRITE_BUFFER_SIZE_MB": 64,
    "TARGET_FILE_SIZE_BASE_MB": 64,
    "TARGET_FILE_SIZE_MULTIPLIER": 2,
    "MAX_BYTES_FOR_LEVEL_BASE_MB": 256,
    "PER_LEVEL_FANOUT": 10,
    "LEVEL0_FILE_NUM_COMPACTION_TRIGGER": 4,
    "MAX_BACKGROUND_JOBS": 4,
    "SUBCOMPACTIONS": 4,
    "BLOCK_SIZE": 65536,
    "CACHE_SIZE_MB": 7168,
    "COMPACTION_READAHEAD_SIZE": 8388608,
    "MIN_LEVEL_TO_COMPRESS": 3,
    "CACHE_INDEX_AND_FILTER_BLOCKS": 1,
    "NUM_THREADS": 1,
    "NUM_KEYS": 40000000,
    "VALUE_SIZE": 400,
    "DURATION_RW": 30,
    "DURATION_RO": 30,
    "SEED": 1,
    "COMPACTION_STYLE": "blob",
    "MIN_BLOB_SIZE": 0,
    "BLOB_FILE_SIZE": 268435456,
    "BLOB_GC_AGE_CUTOFF": 0.5,
    "BLOB_GC_FORCE_THRESHOLD": 0.5
  }
}
JSON

python3 "$repo_root/src/rdbtools.py" run --spec "$spec" --dry-run --json > "$out"

python3 - "$out" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
run = payload[0]
phases = {phase["name"]: phase for phase in run["phases"]}

load = phases["load"]["db_bench_flags"]
assert load["benchmarks"] == "fillseq,stats"
assert load["disable_wal"] == "1"
assert load["threads"] == "1"
assert load["memtablerep"] == "vector"
assert load["allow_concurrent_memtable_write"] == "false"

fwdrange = phases["fwdrange"]["db_bench_flags"]
assert fwdrange["benchmarks"] == "seekrandom,stats"
assert fwdrange["reverse_iterator"] == "false"

revrange = phases["drain_revrange"]["db_bench_flags"]
assert revrange["benchmarks"] == "seekrandom,stats"
assert revrange["reverse_iterator"] == "true"

blob = phases["readrandom"]["db_bench_flags"]
assert blob["enable_blob_files"] == "true"
assert blob["min_blob_size"] == "0"
assert blob["blob_file_size"] == "268435456"
assert blob["use_blob_cache"] == "1"
assert blob["use_shared_block_and_blob_cache"] == "1"
assert blob["blob_cache_size"] == str(16 * 1024 * 1024 * 1024)

common = phases["readrandom"]["db_bench_flags"]
assert common["compression_ratio"] == "0.5"
assert common["verify_checksum"] == "1"
assert common["histogram"] == "1"
assert common["bloom_bits"] == "10"
assert common["open_files"] == "-1"
assert common["target_file_size_multiplier"] == "2"

print("OK")
PY
