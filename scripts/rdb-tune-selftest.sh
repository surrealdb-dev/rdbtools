#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$tmp_dir/results"
cat >"$tmp_dir/defaults.json" <<'JSON'
{
  "db_bench_baseline_env": {
    "COMPACTION_READAHEAD_SIZE": 262144,
    "TARGET_FILE_SIZE_BASE_MB": 64,
    "WRITE_BUFFER_SIZE_MB": 128
  },
  "machine": {
    "cpu_count": 4,
    "effective_memory_bytes": 8589934592
  },
  "warnings": []
}
JSON

cat >"$tmp_dir/results/aggregate.tsv" <<'TSV'
run_id	binary	workload	phase	job	exit_code	ops_sec	mb_sec	micros_per_op	w_amp	c_wgb	c_mbps	stall_pct	rss_gb	blob_read_gb	blob_write_gb	p50	p95	p99	p99_9	log
baseline	db_bench	smoke	overwritesome	overwritesome	0	1000	10	1000	4	8	100	2	1						baseline.log
write_buffer_size__WRITE_BUFFER_SIZE_MB_64	db_bench	smoke	overwritesome	overwritesome	0	1100	11	900	3.8	7	110	1.5	1						candidate.log
TSV

python3 "$repo_root/src/rdbtune.py" rank --run "$tmp_dir" --objective balanced >/dev/null
test -s "$tmp_dir/recommendations.json"
test -s "$tmp_dir/recommendations.md"
echo "OK"
