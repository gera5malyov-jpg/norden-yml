import io
import json
import math
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageOps

SKUS = [
    "SAMS-531863",
    "SAMS-530067",
    "SAMS-531578",
    "RED-00-00011489",
    "SAMS-531829",
    "SAMS-532542",
    "SAMS-640419",
    "SAMS-532489",
]

ROOT = Path("ozon/image_3x4_replace_batch")
MANIFEST = ROOT / "manifest.json"
BATCH_REPORT = ROOT / "batch_report.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "gera5malyov-jpg/norden-yml")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
CLIENT_ID = os.environ.get("OZON_CLIENT_ID", "").strip()
API_KEY = os.environ.get("OZON_API_KEY", "").strip()

if not CLIENT_ID or not API_KEY:
    raise SystemExit("Missing OZON_CLIENT_ID/OZON_API_KEY")

api = requests.Session()
api.headers.update({
    "Client-Id": CLIENT_ID,
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Megapolis-Ozon-Replace-3x4/1.0",
})


def post(path, body):
    r = api.post("https://api-seller.ozon.ru" + path, json=body, timeout=90)
    try:
        data = r.json() if r.content else {}
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(
            f"Ozon HTTP {r.status_code} {path}: "
            f"{json.dumps(data, ensure_ascii=False)[:5000]}"
        )
    return data


def normalize_urls(value):
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for x in value:
        if isinstance(x, dict):
            x = x.get("url") or x.get("file_name") or x.get("src")
        if isinstance(x, str):
            x = x.strip()
            if x.startswith(("https://", "http://")) and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def get_item(offer_id):
    data = post("/v3/product/info/list", {"offer_id": [offer_id]})
    items = data.get("items") or []
    if len(items) != 1:
        raise RuntimeError(f"Expected 1 Ozon item for {offer_id}, got {len(items)}")
    return items[0]


def ordered_gallery(item):
    primary = normalize_urls(item.get("primary_image"))
    photos = normalize_urls(item.get("images"))
    out = []
    seen = set()
    for u in primary + photos:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, primary


def fetch_image(url):
    r = requests.get(
        url,
        timeout=120,
        allow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 Megapolis-Ozon-ImageCheck/1.0"},
    )
    r.raise_for_status()
    if not r.content:
        raise RuntimeError(f"Empty image response: {url}")
    return r.content


def image_dimensions_from_bytes(raw):
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        return im.size


def image_dimensions(url):
    return image_dimensions_from_bytes(fetch_image(url))


def raw_url(relpath):
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{relpath.as_posix()}"


def convert_to_3x4(raw, output_path):
    with Image.open(io.BytesIO(raw)) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if w * 4 == h * 3:
            raise RuntimeError("convert_to_3x4 called for an already 3:4 image")

        if w / h > 3 / 4:
            new_w = w
            new_h = math.ceil(w * 4 / 3)
        else:
            new_h = h
            new_w = math.ceil(h * 3 / 4)

        if new_w * 4 != new_h * 3:
            scale = max(math.ceil(new_w / 3), math.ceil(new_h / 4))
            new_w, new_h = 3 * scale, 4 * scale

        src = im.convert("RGB")
        canvas = Image.new("RGB", (new_w, new_h), "white")
        x = (new_w - w) // 2
        y = (new_h - h) // 2
        canvas.paste(src, (x, y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, "JPEG", quality=95, optimize=True)
        return w, h, new_w, new_h


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prepare_one(offer_id):
    item = get_item(offer_id)
    product_id = int(item.get("id") or item.get("product_id") or 0)
    if not product_id:
        raise RuntimeError(f"No product_id for {offer_id}")

    gallery, primary = ordered_gallery(item)
    if not gallery:
        raise RuntimeError(f"No images found for {offer_id}")
    if not primary:
        raise RuntimeError(f"No primary image returned for {offer_id}")
    if gallery[0] != primary[0]:
        raise RuntimeError(f"Primary image is not first in reconstructed gallery for {offer_id}")
    if len(gallery) > 30:
        raise RuntimeError(f"Safety stop: {offer_id} has {len(gallery)} images (>30)")

    out_dir = ROOT / offer_id
    entries = []
    target_urls = []
    converted_count = 0
    already_count = 0

    for idx, url in enumerate(gallery, 1):
        raw = fetch_image(url)
        w, h = image_dimensions_from_bytes(raw)
        exact = (w * 4 == h * 3)
        entry = {
            "index": idx,
            "source_url": url,
            "source_width": w,
            "source_height": h,
            "already_3x4": exact,
            "was_primary": idx == 1,
        }

        if exact:
            entry["action"] = "kept_original"
            entry["target_url"] = url
            target_urls.append(url)
            already_count += 1
        else:
            rel = out_dir / f"image_{idx:02d}_3x4.jpg"
            sw, sh, nw, nh = convert_to_3x4(raw, rel)
            target = raw_url(rel)
            entry.update({
                "action": "replaced_with_3x4",
                "output_file": rel.as_posix(),
                "output_width": nw,
                "output_height": nh,
                "target_url": target,
            })
            target_urls.append(target)
            converted_count += 1

        entries.append(entry)

    if len(target_urls) != len(gallery):
        raise RuntimeError(f"Internal count mismatch for {offer_id}")
    if len(set(target_urls)) != len(target_urls):
        raise RuntimeError(f"Duplicate target image URLs for {offer_id}")

    color = normalize_urls(item.get("color_image"))
    report = {
        "offer_id": offer_id,
        "product_id": product_id,
        "source_image_count": len(gallery),
        "converted_count": converted_count,
        "already_3x4_count": already_count,
        "source_primary_url": gallery[0],
        "target_primary_url": target_urls[0],
        "source_gallery": gallery,
        "target_gallery": target_urls,
        "color_image": color[0] if color else None,
        "images": entries,
        "prepare_status": "SUCCESS",
    }
    write_json(out_dir / "pre_report.json", report)
    return report


def prepare():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "repo": REPO,
        "branch": BRANCH,
        "offers": {},
    }

    for offer_id in SKUS:
        print(f"PREPARE {offer_id}", flush=True)
        try:
            manifest["offers"][offer_id] = prepare_one(offer_id)
        except Exception as e:
            manifest["offers"][offer_id] = {
                "offer_id": offer_id,
                "prepare_status": "ERROR",
                "error": repr(e),
            }
            write_json(ROOT / offer_id / "pre_report.json", manifest["offers"][offer_id])
            print(f"PREPARE ERROR {offer_id}: {e}", flush=True)

    write_json(MANIFEST, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def check_public_targets(target_urls, source_urls):
    for target, source in zip(target_urls, source_urls):
        if target == source:
            continue
        raw = fetch_image(target)
        w, h = image_dimensions_from_bytes(raw)
        if w * 4 != h * 3:
            raise RuntimeError(f"Converted public image is not 3:4: {target} ({w}x{h})")


def picture_status(product_id):
    data = post("/v2/product/pictures/info", {"product_id": [str(product_id)]})
    rows = data.get("items") or []
    if not rows:
        return {
            "errors": [{"message": "Ozon returned no picture info row"}],
            "primary": [],
            "gallery": [],
            "raw": data,
        }
    row = rows[0]
    p1 = normalize_urls(row.get("primary_photo"))
    p2 = normalize_urls(row.get("photo"))
    final = []
    seen = set()
    for u in p1 + p2:
        if u not in seen:
            seen.add(u)
            final.append(u)
    return {
        "errors": row.get("errors") or [],
        "primary": p1,
        "gallery": final,
        "raw": data,
    }


def verify_dimensions(urls):
    dims = []
    all_3x4 = True
    for idx, u in enumerate(urls, 1):
        w, h = image_dimensions(u)
        ok = (w * 4 == h * 3)
        dims.append({
            "index": idx,
            "url": u,
            "width": w,
            "height": h,
            "is_3x4": ok,
        })
        if not ok:
            all_3x4 = False
    return all_3x4, dims


def apply_one(pre):
    offer_id = pre["offer_id"]
    product_id = int(pre["product_id"])
    source = pre["source_gallery"]
    target = pre["target_gallery"]

    # Safety check immediately before write.
    item = get_item(offer_id)
    current, primary = ordered_gallery(item)
    if current != source:
        raise RuntimeError(
            f"Safety stop: Ozon gallery changed since prepare for {offer_id}. "
            f"Expected {len(source)} images, current {len(current)}."
        )
    if not primary or primary[0] != source[0]:
        raise RuntimeError(f"Safety stop: primary image changed for {offer_id}")

    check_public_targets(target, source)

    import_response = None
    if pre["converted_count"] > 0:
        body = {
            "product_id": product_id,
            "images": target,
        }
        if pre.get("color_image"):
            body["color_image"] = pre["color_image"]

        import_response = post("/v1/product/pictures/import", body)
        pictures = ((import_response.get("result") or {}).get("pictures") or [])
        non_imported = [p for p in pictures if p.get("state") != "imported"]
        if pictures and non_imported:
            raise RuntimeError(
                "Ozon import returned non-imported picture states: "
                + json.dumps(non_imported, ensure_ascii=False)[:4000]
            )

    last = None
    for attempt in range(1, 13):
        if pre["converted_count"] > 0:
            time.sleep(5)
        last = picture_status(product_id)
        if (
            not last["errors"]
            and last["gallery"] == target
            and last["primary"]
            and last["primary"][0] == target[0]
        ):
            break
        print(
            f"VERIFY WAIT {offer_id} attempt={attempt} "
            f"errors={len(last['errors'])} final={len(last['gallery'])}/{len(target)}",
            flush=True,
        )

    if last is None:
        raise RuntimeError("No picture status response")

    all_3x4, final_dims = verify_dimensions(last["gallery"])
    verification = {
        "source_count": len(source),
        "target_count": len(target),
        "final_count": len(last["gallery"]),
        "count_correct": len(last["gallery"]) == len(target),
        "order_preserved": last["gallery"] == target,
        "primary_is_expected_first_image": (
            bool(last["primary"])
            and last["primary"][0] == target[0]
            and bool(last["gallery"])
            and last["gallery"][0] == target[0]
        ),
        "all_images_3x4": all_3x4,
        "ozon_errors": last["errors"],
        "final_dimensions": final_dims,
    }

    ok = all([
        verification["count_correct"],
        verification["order_preserved"],
        verification["primary_is_expected_first_image"],
        verification["all_images_3x4"],
        not verification["ozon_errors"],
    ])

    result = {
        "offer_id": offer_id,
        "product_id": product_id,
        "source_image_count": pre["source_image_count"],
        "converted_count": pre["converted_count"],
        "already_3x4_count": pre["already_3x4_count"],
        "final_image_count": len(last["gallery"]),
        "import_response": import_response,
        "verification": verification,
        "result": "SUCCESS" if ok else "ERROR",
    }

    if not ok:
        raise RuntimeError(
            "Final verification failed: "
            + json.dumps(verification, ensure_ascii=False)[:6000]
        )

    return result


def apply():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    batch = {
        "offers": {},
        "summary": {
            "requested": len(SKUS),
            "success": 0,
            "error": 0,
        },
    }

    for offer_id in SKUS:
        print(f"APPLY {offer_id}", flush=True)
        pre = manifest["offers"].get(offer_id) or {}
        if pre.get("prepare_status") != "SUCCESS":
            result = {
                "offer_id": offer_id,
                "result": "ERROR",
                "error": pre.get("error", "Prepare stage did not succeed"),
                "source_image_count": pre.get("source_image_count"),
                "converted_count": pre.get("converted_count"),
                "already_3x4_count": pre.get("already_3x4_count"),
                "final_image_count": None,
            }
            batch["offers"][offer_id] = result
            write_json(ROOT / offer_id / "final_report.json", result)
            batch["summary"]["error"] += 1
            continue

        try:
            result = apply_one(pre)
            batch["summary"]["success"] += 1
        except Exception as e:
            # Read current state for the report without attempting another write.
            result = {
                "offer_id": offer_id,
                "product_id": pre.get("product_id"),
                "source_image_count": pre.get("source_image_count"),
                "converted_count": pre.get("converted_count"),
                "already_3x4_count": pre.get("already_3x4_count"),
                "final_image_count": None,
                "result": "ERROR",
                "error": repr(e),
            }
            try:
                st = picture_status(int(pre["product_id"]))
                result["final_image_count"] = len(st["gallery"])
                result["ozon_errors"] = st["errors"]
                result["final_gallery"] = st["gallery"]
            except Exception as inspect_error:
                result["inspection_error"] = repr(inspect_error)
            batch["summary"]["error"] += 1
            print(f"APPLY ERROR {offer_id}: {e}", flush=True)

        batch["offers"][offer_id] = result
        write_json(ROOT / offer_id / "final_report.json", result)

    write_json(BATCH_REPORT, batch)
    print(json.dumps(batch, ensure_ascii=False, indent=2))

    if batch["summary"]["error"]:
        raise SystemExit(1)


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "prepare":
        prepare()
    elif stage == "apply":
        apply()
    else:
        raise SystemExit("Usage: replace_images_3x4_batch.py prepare|apply")
