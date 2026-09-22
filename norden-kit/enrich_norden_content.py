import argparse
import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
SYNC_PATH = BASE / "sync_norden_kit.py"
REPORT_PATH = BASE / "content_enrichment_report.json"

spec = importlib.util.spec_from_file_location("norden_sync", SYNC_PATH)
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=250)
    args = ap.parse_args()

    token = ns.os.environ.get("YANDEX_KIT_TOKEN", "")
    secret = ns.os.environ.get("NORDEN_SECRET", "").strip()
    kit = ns.KitClient(token)

    source, duplicates, source_kind, api_error = ns.load_source(secret, short=False)
    if len(source) < 1000:
        raise RuntimeError(f"Safety stop: unexpectedly small Norden source ({len(source)} products)")

    mapping = ns.load_mapping()
    all_chars, chars_by_title, code_site_id, _article_id = ns.resolve_special_characteristics(kit)

    targets = []
    for article in sorted(mapping.get("variants", {})):
        item = source.get(article)
        if not item:
            continue
        for row in mapping["variants"].get(article) or []:
            vid = ns.s(row.get("variant_id"))
            if vid:
                targets.append((article, vid))

    start = max(0, args.start_index)
    end = min(len(targets), start + max(1, args.batch_size))

    report = {
        "started_at": now_iso(),
        "mode": "content-only",
        "source": source_kind,
        "api_error": api_error,
        "source_products": len(source),
        "source_duplicate_articles": len(set(duplicates)),
        "mapped_targets_total": len(targets),
        "start_index": start,
        "end_index": end,
        "processed": 0,
        "cards_images_filled": 0,
        "cards_images_already_present": 0,
        "cards_without_source_images": 0,
        "image_files_uploaded": 0,
        "image_errors": 0,
        "characteristics_added": 0,
        "cards_characteristics_patched": 0,
        "errors": [],
        "warnings": [],
    }

    for pos in range(start, end):
        article, variant_id = targets[pos]
        item = source[article]
        try:
            current = kit.get_variant(variant_id)

            # Images: only fill cards that currently have no media.
            # This avoids duplicates and never replaces existing user/KIT media.
            existing_media = list(current.get("media") or [])
            if existing_media:
                report["cards_images_already_present"] += 1
            elif item.get("images"):
                media = []
                for url in item["images"][:20]:
                    try:
                        uploaded = kit.upload_image_url(url)
                        fid = ns.s(uploaded.get("id"))
                        if fid:
                            media.append({
                                "type": "IMAGE",
                                "display_sequence": len(media),
                                "image_id": fid,
                            })
                            report["image_files_uploaded"] += 1
                        time.sleep(0.5)
                    except Exception as exc:
                        report["image_errors"] += 1
                        if len(report["warnings"]) < 300:
                            report["warnings"].append(f"{article}: image failed: {str(exc)[:300]}")
                if media:
                    kit.patch_variant(variant_id, {"media": media})
                    verify = kit.get_variant(variant_id)
                    verified = [
                        m for m in (verify.get("media") or [])
                        if isinstance(m, dict) and ns.s(m.get("type")).upper() == "IMAGE"
                    ]
                    if verified:
                        report["cards_images_filled"] += 1
                    else:
                        report["image_errors"] += 1
                        report["warnings"].append(
                            f"{article}: media patch accepted but verification returned no images"
                        )
            else:
                report["cards_without_source_images"] += 1

            # Characteristics: add only IDs that are completely absent.
            # Existing characteristic values are preserved exactly as they are.
            current = kit.get_variant(variant_id)
            existing = list(current.get("characteristics") or [])
            existing_ids = {
                ns.s(c.get("characteristic_id"))
                for c in existing
                if ns.s(c.get("characteristic_id"))
            }
            desired = ns.build_source_characteristics(
                item, kit, all_chars, chars_by_title, code_site_id
            )
            additions = [d for d in desired if d["characteristic_id"] not in existing_ids]
            if additions:
                kit.patch_variant(variant_id, {"characteristics": existing + additions})
                report["characteristics_added"] += len(additions)
                report["cards_characteristics_patched"] += 1

            report["processed"] += 1
            if report["processed"] % 10 == 0:
                REPORT_PATH.write_text(
                    json.dumps({**report, "next_index": pos + 1}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            time.sleep(0.7)
        except Exception as exc:
            report["errors"].append({
                "index": pos,
                "article": article,
                "variant_id": variant_id,
                "message": str(exc)[:800],
            })
            # Continue with other cards; one bad card must not stop the batch.
            time.sleep(1.0)

    report["next_index"] = end
    report["remaining"] = max(0, len(targets) - end)
    report["finished_at"] = now_iso()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
