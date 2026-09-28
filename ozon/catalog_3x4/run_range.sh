#!/usr/bin/env bash
set -uo pipefail

: "${START_BATCH:?START_BATCH required}"
: "${END_BATCH:?END_BATCH required}"
: "${OZON_CLIENT_ID:?OZON_CLIENT_ID required}"
: "${OZON_API_KEY:?OZON_API_KEY required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID required}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

REPORT_DIR="ozon/catalog_3x4/range_reports_${START_BATCH}_${END_BATCH}"
rm -rf "$REPORT_DIR"
mkdir -p "$REPORT_DIR"
RANGE_ERROR=0

for BATCH_INDEX in $(seq "$START_BATCH" "$END_BATCH"); do
  export BATCH_INDEX
  export OZON_TEMP_BRANCH="ozon-image-temp-catalog-${GITHUB_RUN_ID}-${BATCH_INDEX}"
  printf '\n===== BATCH %s =====\n' "$BATCH_INDEX"

  BATCH_DIR="ozon/catalog_3x4/run/batch_$(printf '%04d' "$BATCH_INDEX")"
  rm -rf "$BATCH_DIR"

  if ! python ozon/catalog_3x4/process_batch.py prepare "$BATCH_INDEX"; then
    echo "Prepare hard failure for batch $BATCH_INDEX"
    RANGE_ERROR=1
    continue
  fi

  JPG_COUNT="$(find "$BATCH_DIR" -type f -name '*.jpg' | wc -l | tr -d ' ')"
  echo "Batch $BATCH_INDEX converted JPG count: $JPG_COUNT"

  if [ "$JPG_COUNT" != "0" ]; then
    STAGE="$(mktemp -d)"
    find "$BATCH_DIR" -type f -name '*.jpg' -print0 | while IFS= read -r -d '' f; do
      mkdir -p "$STAGE/$(dirname "$f")"
      cp "$f" "$STAGE/$f"
    done

    python - <<'PY' > "$STAGE/.ozon_batch_meta.json"
import json, os, pathlib
idx=int(os.environ["BATCH_INDEX"])
p=pathlib.Path(f"ozon/catalog_3x4/run/batch_{idx:04d}/active.json")
d=json.loads(p.read_text(encoding="utf-8"))
print(json.dumps({
    "batch_index":idx,
    "temp_branch":os.environ["OZON_TEMP_BRANCH"],
    "offers":d.get("active",[]),
},ensure_ascii=False,indent=2))
PY

    (
      cd "$STAGE"
      git init -b temp >/dev/null
      git config user.name "github-actions[bot]"
      git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
      git add -A
      git commit -m "temp: Ozon catalog batch $BATCH_INDEX [skip ci]" >/dev/null
      git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
      git push --force origin "HEAD:refs/heads/$OZON_TEMP_BRANCH"
    )
    rm -rf "$STAGE"
  fi

  if ! python ozon/catalog_3x4/process_batch.py apply "$BATCH_INDEX"; then
    echo "Apply hard failure for batch $BATCH_INDEX"
    RANGE_ERROR=1
  fi

  python ozon/catalog_3x4/process_batch.py cdn-check "$BATCH_INDEX" || true

  MAT="$BATCH_DIR/materialization_report.json"
  STATUS=""
  if [ -f "$MAT" ]; then
    STATUS="$(python -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status",""))' "$MAT")"
  fi

  if [ "$JPG_COUNT" != "0" ] && [ "$STATUS" = "SUCCESS" ]; then
    git push origin --delete "$OZON_TEMP_BRANCH" || true
  elif [ "$JPG_COUNT" != "0" ]; then
    echo "Keeping $OZON_TEMP_BRANCH until Ozon CDN owns all images."
  fi

  python ozon/catalog_3x4/process_batch.py compact "$BATCH_INDEX" || true
  if [ -f "$BATCH_DIR/summary.json" ]; then
    cp "$BATCH_DIR/summary.json" "$REPORT_DIR/batch_$(printf '%04d' "$BATCH_INDEX").json"
  fi

  find "$BATCH_DIR" -type f -name '*.jpg' -delete || true
  rm -rf "$BATCH_DIR"
  echo "===== BATCH $BATCH_INDEX DONE ====="
done

python - <<'PY'
import json, glob, os, pathlib
start=int(os.environ["START_BATCH"])
end=int(os.environ["END_BATCH"])
report_dir=f"ozon/catalog_3x4/range_reports_{start}_{end}"
files=sorted(glob.glob(f"{report_dir}/batch_*.json"))
rows=[json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in files]
out={
    "range_start":start,
    "range_end":end,
    "batches_reported":len(rows),
    "cards_requested":sum(len(x.get("requested") or []) for x in rows),
    "cards_success":sum((x.get("summary") or {}).get("success",0) for x in rows),
    "cards_error":sum((x.get("summary") or {}).get("error",0) for x in rows),
    "images_converted":sum(sum((o.get("converted_count") or 0) for o in (x.get("offers") or [])) for x in rows),
}
pathlib.Path(f"{report_dir}/range_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))
PY

exit "$RANGE_ERROR"
