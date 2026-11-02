#!/bin/bash
# Roothinks source maintenance contract
# 目的：R7 端到端驗收 runner。研究者完整旅程分成四段，每段都用全新的合成站（study_coresearch_fixture.py）：
#   1. 共研旅程（study_coresearch_browser.cjs）：雙語閱讀、選文、AI 解釋與批判（模型替身）、證據筆記、協作、
#      Manuscript 段落出處、引文插入、匯出、教授審閱範圍。
#   2. 雙語閱讀介面（reading_ui_r7_browser.cjs）：公式、圖表、引文、缺譯、四種螢幕寬度。
#   3. 多篇比較矩陣（study_matrix_r7_browser.cjs）：4 篇論文、引文核對、未報告、不可比較、舊紀錄。
#   4. 匯入與權限（r7_acceptance_import_permissions.cjs）：文獻庫匯入、負向控制、外人與教授、研究者自主。
# 用法：PY=<python> VENDOR=<vendor 目錄> NODE_PATH=<playwright node_modules> test/integration_e2e/r7_acceptance.sh [輸出目錄]
# 邊界：只連 127.0.0.1；合成資料在新的暫存目錄；不呼叫任何模型；不接正式資料。
set -u
cd "$(dirname "$0")/../.." || exit 2
PY=${PY:-python}
VENDOR=${VENDOR:-}
PORT=${R7_PORT:-8941}
OUT=${1:-output/playwright/r7-acceptance-$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
FX=""
ROOT=""

start_fixture() {
    env -u DATABASE_URL -u SECRET_KEY -u FERNET_KEY -u NLTK_DATA STUDY_FIXTURE_PORT=$PORT \
        "$PY" test/integration_e2e/study_coresearch_fixture.py > "$OUT/fixture-$1.out" 2> "$OUT/fixture-$1.err" &
    FX=$!
    for _ in $(seq 1 90); do
        curl -s -o /dev/null "http://127.0.0.1:$PORT/" && break
        sleep 1
    done
    ROOT=$("$PY" -c "import json,sys; print(next((json.loads(l).get('root','') for l in open(sys.argv[1]) if l.startswith('{')), ''))" "$OUT/fixture-$1.out" 2>/dev/null)
}

stop_fixture() {
    [ -n "$FX" ] && kill "$FX" 2>/dev/null && wait "$FX" 2>/dev/null
    FX=""
    [ -n "$ROOT" ] && case "$ROOT" in /tmp/roothinks-study-browser-*) rm -rf "$ROOT" ;; esac
    ROOT=""
}
trap stop_fixture EXIT

declare -A RESULT
run_segment() {
    local name=$1; shift
    start_fixture "$name"
    env STUDY_FIXTURE_PORT=$PORT STUDY_FIXTURE_ROOT="$ROOT" I12_VENDOR_DIR="$VENDOR" "$@" > "$OUT/$name.log" 2>&1
    RESULT[$name]=$?
    stop_fixture
    echo "$name exit=${RESULT[$name]}"
}

run_segment coresearch env STUDY_PHASE=after node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_coresearch_browser.cjs
run_segment reading env R7_PHASE=after R7_OUT="$OUT/reading" node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/reading_ui_r7_browser.cjs
run_segment matrix env MATRIX_PHASE=after MATRIX_OUT="$OUT/matrix" node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/study_matrix_r7_browser.cjs
run_segment import_permissions env R7_SHOTS_DIR="$OUT/import" node --require ./test/integration_e2e/vendor-route.cjs test/integration_e2e/r7_acceptance_import_permissions.cjs "$OUT/import/import-permissions.json"

failed=0
for k in coresearch reading matrix import_permissions; do [ "${RESULT[$k]}" = "0" ] || failed=1; done
echo "{\"coresearch\": ${RESULT[coresearch]}, \"reading\": ${RESULT[reading]}, \"matrix\": ${RESULT[matrix]}, \"import_permissions\": ${RESULT[import_permissions]}}" > "$OUT/exit-codes.json"
exit $failed
