#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --spec SPEC --run-id RUN_ID [--resume] [--force]" >&2
}

spec=""
run_id=""
args=()
while (($#)); do
  case "$1" in
    --spec)
      spec="${2:-}"
      shift 2
      ;;
    --run-id|--only-run-id)
      run_id="${2:-}"
      shift 2
      ;;
    --resume|--force|--smoke)
      args+=("$1")
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$spec" || -z "$run_id" ]]; then
  usage
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$repo_root/src/rdbtools.py" run --spec "$spec" --only-run-id "$run_id" "${args[@]}"
