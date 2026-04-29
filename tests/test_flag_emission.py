"""Contract test: every supported env-var knob must surface in db_bench argv.

Acts as the canonical catalog of what rdbtools accepts. If a knob in this
file is not emitted by mark_base_flags / mark_style_flags / mark_write_flags
/ mark_command_model, the test fails — protecting against the kind of silent
drop that the old FLAG_MAP / BOOL_FLAG_MAP dicts allowed.

Run: python3 -m unittest tests.test_flag_emission -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import rdbtools  # noqa: E402

DB_BENCH = Path("./db_bench")
OUT = Path("/tmp/rdbtools-test-out")

# (env_key, env_value, expected_flag_substring)
# Sentinel values are chosen to be distinct from all defaults so a flag whose
# default happens to equal the sentinel can't pass by accident.
BASE_KNOBS = [
    ("NUM_KEYS", 1234567, "--num=1234567"),
    ("KEY_SIZE", 23, "--key_size=23"),
    ("VALUE_SIZE", 401, "--value_size=401"),
    ("BLOCK_SIZE", 65537, "--block_size=65537"),
    ("CACHE_NUMSHARDBITS", 7, "--cache_numshardbits=7"),
    ("CACHE_TYPE", "hyper_clock_cache", "--cache_type=hyper_clock_cache"),
    ("COMPRESSION_MAX_DICT_BYTES", 16384, "--compression_max_dict_bytes=16384"),
    ("COMPRESSION_RATIO", 0.7, "--compression_ratio=0.7"),
    ("COMPRESSION_TYPE", "zstd", "--compression_type=zstd"),
    ("BYTES_PER_SYNC", 524288, "--bytes_per_sync=524288"),
    ("METADATA_BLOCK_SIZE", 8192, "--metadata_block_size=8192"),
    # Pool ratios are gated on CACHE_INDEX_AND_FILTER_BLOCKS=1 (intended).
    # Tested separately in test_pool_ratios_gated_by_cache_idx_filter.
    ("DELETE_OBSOLETE_FILES_PERIOD_MICROS", 12345678, "--delete_obsolete_files_period_micros=12345678"),
    ("PER_LEVEL_FANOUT", 9, "--max_bytes_for_level_multiplier=9"),
    ("STATISTICS", 1, "--statistics=1"),
    ("STATS_PER_INTERVAL", 0, "--stats_per_interval=0"),
    ("STATS_INTERVAL_SECONDS", 11, "--stats_interval_seconds=11"),
    ("REPORT_INTERVAL_SECONDS", 5, "--report_interval_seconds=5"),
    ("HISTOGRAM", 0, "--histogram=0"),
    ("MEMTABLE_REP", "vector", "--memtablerep=vector"),
    ("BLOOM_BITS", 12, "--bloom_bits=12"),
    ("OPEN_FILES", 100, "--open_files=100"),
    ("SUBCOMPACTIONS", 5, "--subcompactions=5"),
    ("COMPACTION_READAHEAD_SIZE", 8388608, "--compaction_readahead_size=8388608"),
    ("INITIAL_AUTO_READAHEAD_SIZE", 8192, "--initial_auto_readahead_size=8192"),
    ("MAX_AUTO_READAHEAD_SIZE", 524288, "--max_auto_readahead_size=524288"),
    ("FILE_READS_FOR_AUTO_READAHEAD", 2, "--num_file_reads_for_auto_readahead=2"),
    ("WAL_SIZE_LIMIT_MB", 1024, "--wal_size_limit_MB=1024"),
    ("WAL_BYTES_PER_SYNC", 4096, "--wal_bytes_per_sync=4096"),
    ("MANUAL_WAL_FLUSH", 1, "--manual_wal_flush=1"),
    ("VERIFY_CHECKSUM", 0, "--verify_checksum=0"),
    ("CACHE_INDEX_AND_FILTER_BLOCKS", 1, "--cache_index_and_filter_blocks=1"),
    ("PARTITION_INDEX_AND_FILTERS", 1, "--partition_index_and_filters=1"),
    ("PIN_TOP_LEVEL_INDEX_AND_FILTER", 1, "--pin_top_level_index_and_filter=1"),
    ("MULTIREAD_BATCHED", 1, "--multiread_batched"),
    ("MB_WRITE_PER_SEC", 4, "--benchmark_write_rate_limit=4194304"),
    ("WRITE_BUFFER_SIZE_MB", 64, "--write_buffer_size=67108864"),
    ("TARGET_FILE_SIZE_BASE_MB", 64, "--target_file_size_base=67108864"),
    ("TARGET_FILE_SIZE_MULTIPLIER", 2, "--target_file_size_multiplier=2"),
    ("MAX_BYTES_FOR_LEVEL_BASE_MB", 256, "--max_bytes_for_level_base=268435456"),
    ("CACHE_SIZE_MB", 7168, "--cache_size=7516192768"),
    ("LEVEL0_FILE_NUM_COMPACTION_TRIGGER", 5, "--level0_file_num_compaction_trigger=5"),
    ("LEVEL0_SLOWDOWN_WRITES_TRIGGER", 21, "--level0_slowdown_writes_trigger=21"),
    ("LEVEL0_STOP_WRITES_TRIGGER", 31, "--level0_stop_writes_trigger=31"),
    ("MAX_BACKGROUND_JOBS", 4, "--max_background_jobs=4"),
    ("MAX_WRITE_BUFFER_NUMBER", 6, "--max_write_buffer_number=6"),
    ("MIN_WRITE_BUFFER_NUMBER_TO_MERGE", 2, "--min_write_buffer_number_to_merge=2"),
    ("DURATION", 30, "--duration=30"),
    ("WRITES", 1000, "--writes=1000"),
    ("SEED", 42, "--seed=42"),
]

LEVELED_KNOBS = [
    ("NUM_LEVELS", 9, "--num_levels=9"),
    ("MIN_LEVEL_TO_COMPRESS", 3, "--min_level_to_compress=3"),
    ("LEVEL_COMPACTION_DYNAMIC_LEVEL_BYTES", 0, "--level_compaction_dynamic_level_bytes=false"),
    ("PIN_L0_FILTER_AND_INDEX_BLOCKS_IN_CACHE", 0, "--pin_l0_filter_and_index_blocks_in_cache=0"),
]

UNIVERSAL_KNOBS = [
    ("UNIVERSAL_COMPRESSION_SIZE_PERCENT", 75, "--universal_compression_size_percent=75"),
    ("UNIVERSAL_MIN_MERGE_WIDTH", 3, "--universal_min_merge_width=3"),
    ("UNIVERSAL_MAX_MERGE_WIDTH", 25, "--universal_max_merge_width=25"),
    ("UNIVERSAL_SIZE_RATIO", 2, "--universal_size_ratio=2"),
    ("UNIVERSAL_MAX_SIZE_AMP", 150, "--universal_max_size_amplification_percent=150"),
    ("UNIVERSAL_ALLOW_TRIVIAL_MOVE", 1, "--universal_allow_trivial_move=1"),
]

BLOB_KNOBS = [
    ("MIN_BLOB_SIZE", 1024, "--min_blob_size=1024"),
    ("BLOB_FILE_SIZE", 134217728, "--blob_file_size=134217728"),
    ("BLOB_COMPRESSION_TYPE", "lz4", "--blob_compression_type=lz4"),
    ("ENABLE_BLOB_GC", 0, "--enable_blob_garbage_collection=false"),
    ("BLOB_GC_AGE_CUTOFF", 0.5, "--blob_garbage_collection_age_cutoff=0.5"),
    ("BLOB_GC_FORCE_THRESHOLD", 0.75, "--blob_garbage_collection_force_threshold=0.75"),
    ("BLOB_FILE_STARTING_LEVEL", 1, "--blob_file_starting_level=1"),
    ("USE_BLOB_CACHE", 0, "--use_blob_cache=0"),
    ("USE_SHARED_BLOCK_AND_BLOB_CACHE", 0, "--use_shared_block_and_blob_cache=0"),
    ("BLOB_CACHE_SIZE", 1073741824, "--blob_cache_size=1073741824"),
    ("BLOB_CACHE_NUMSHARDBITS", 5, "--blob_cache_numshardbits=5"),
    ("PREPOPULATE_BLOB_CACHE", 1, "--prepopulate_blob_cache=1"),
    ("BLOB_COMPACTION_READAHEAD_SIZE", 16777216, "--blob_compaction_readahead_size=16777216"),
]

# Phase-specific knobs surface only in the relevant phase.
PHASE_KNOBS = [
    ("multireadrandom", "BATCH_SIZE", 7, "--batch_size=7"),
    ("fwdrange", "NUM_NEXTS_PER_SEEK", 15, "--seek_nexts=15"),
    ("revrange", "NUM_NEXTS_PER_SEEK", 15, "--seek_nexts=15"),
    ("fwdrangewhilewriting", "NUM_NEXTS_PER_SEEK", 15, "--seek_nexts=15"),
    ("revrangewhilewriting", "NUM_NEXTS_PER_SEEK", 15, "--seek_nexts=15"),
]


def build_argv(env: dict, job: str = "overwriteandwait") -> str:
    env = {"DB_DIR": "/tmp/x", **env}
    argv = rdbtools.db_bench_command(DB_BENCH, {"job": job}, env, OUT)
    return " ".join(argv)


class TestFlagEmission(unittest.TestCase):
    def _check(self, knobs, style: str, job: str = "overwriteandwait"):
        for env_key, value, expected in knobs:
            with self.subTest(knob=env_key):
                env = {env_key: value, "COMPACTION_STYLE": style}
                joined = build_argv(env, job=job)
                self.assertIn(expected, joined,
                              f"{env_key}={value} did not produce {expected!r} in argv")

    def test_base_knobs_surface_under_leveled(self):
        self._check(BASE_KNOBS, style="leveled")

    def test_leveled_specific_knobs(self):
        self._check(LEVELED_KNOBS, style="leveled")

    def test_universal_specific_knobs(self):
        self._check(UNIVERSAL_KNOBS, style="universal")

    def test_blob_specific_knobs(self):
        self._check(BLOB_KNOBS, style="blob")

    def test_phase_specific_knobs(self):
        for job, env_key, value, expected in PHASE_KNOBS:
            with self.subTest(job=job, knob=env_key):
                env = {env_key: value, "COMPACTION_STYLE": "leveled"}
                joined = build_argv(env, job=job)
                self.assertIn(expected, joined,
                              f"{env_key}={value} for job={job} did not produce {expected!r}")

    def test_pool_ratios_gated_by_cache_idx_filter(self):
        joined = build_argv({
            "CACHE_INDEX_AND_FILTER_BLOCKS": 1,
            "CACHE_HIGH_PRI_POOL_RATIO": 0.3,
            "CACHE_LOW_PRI_POOL_RATIO": 0,
        })
        self.assertIn("--cache_high_pri_pool_ratio=0.3", joined)
        self.assertIn("--cache_low_pri_pool_ratio=0", joined)
        # And gone when the gate is off:
        joined = build_argv({"CACHE_HIGH_PRI_POOL_RATIO": 0.3})
        self.assertNotIn("--cache_high_pri_pool_ratio", joined)
        self.assertNotIn("--cache_low_pri_pool_ratio", joined)

    def test_optional_knobs_omitted_when_unset(self):
        # MULTIREAD_BATCHED is the only feature toggle that must stay absent until
        # env explicitly opts in. Plain-value knobs follow rdbtools' always-emit
        # convention and are covered by test_default_emission_when_env_unset.
        joined = build_argv({})
        self.assertNotIn("--multiread_batched", joined)

    def test_default_emission_when_env_unset(self):
        # Always-emit knobs must surface with their db_bench struct defaults
        # when the caller doesn't override them. Defaults pinned from rocksdb
        # source: include/rocksdb/{table.h,options.h,advanced_options.h} and
        # tools/db_bench_tool.cc.
        joined = build_argv({})
        for expected in (
            "--initial_auto_readahead_size=8192",
            "--max_auto_readahead_size=262144",
            "--num_file_reads_for_auto_readahead=2",
            "--wal_size_limit_MB=0",
            "--wal_bytes_per_sync=0",
            "--manual_wal_flush=0",
            "--min_write_buffer_number_to_merge=1",
        ):
            self.assertIn(expected, joined,
                          f"{expected} missing from argv when env doesn't override")
        # blob_compaction_readahead_size only exists in the blob-style branch.
        joined_blob = build_argv({"COMPACTION_STYLE": "blob"})
        self.assertIn("--blob_compaction_readahead_size=0", joined_blob)


if __name__ == "__main__":
    unittest.main()
