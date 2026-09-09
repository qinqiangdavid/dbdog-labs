#!/usr/bin/env bash
# evidence-chain 批次一键入口(mac/Linux):先自检,通过才跑。用法:run-batch.sh batch.md [--force] [--only ID1,ID2]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ $# -ge 1 ] || { echo "用法: run-batch.sh 批次文件.md [--force] [--only ID1,ID2]"; exit 2; }
PY=$(command -v python3 || command -v python)
"$PY" "$HERE/scripts/batch.py" "$1" --check || { echo "自检未过,按上面的提示修好再跑"; exit 1; }
exec "$PY" "$HERE/scripts/batch.py" "$@"
