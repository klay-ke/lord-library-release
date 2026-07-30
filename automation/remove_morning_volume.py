#!/usr/bin/env python3
"""One-off maintenance helper for removing a volume from R2 and catalog.json."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os

from sync_morning_epubs import (
    R2_PREFIX,
    finalize_catalog,
    load_catalog,
    r2_client,
    verify_public_object,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    args = parser.parse_args()

    catalog = load_catalog()
    removed = None
    for group in catalog["years"]:
        kept = []
        for book in group.get("books", []):
            if book.get("code") == args.code:
                removed = book
            else:
                kept.append(book)
        group["books"] = kept
        group["count"] = len(kept)
    catalog["years"] = [group for group in catalog["years"] if group["books"]]
    if removed is None:
        raise RuntimeError(f"catalog 中没有 {args.code}")

    client = r2_client()
    bucket = os.environ["R2_BUCKET"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    original = load_catalog()
    original_bytes = (
        json.dumps(original, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    client.put_object(
        Bucket=bucket,
        Key=f"{R2_PREFIX}/catalog-history/catalog-before-remove-{args.code}-{stamp}.json",
        Body=original_bytes,
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=31536000, immutable",
    )
    client.delete_object(Bucket=bucket, Key=removed["path"])

    catalog_bytes = finalize_catalog(catalog)
    client.put_object(
        Bucket=bucket,
        Key=f"{R2_PREFIX}/catalog.json",
        Body=catalog_bytes,
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=60, must-revalidate",
    )
    verify_public_object(f"{R2_PREFIX}/catalog.json", catalog_bytes)
    print(f"已删除 {args.code}: {removed['path']}", flush=True)
    print(f"catalog hash={catalog['contentHash']}", flush=True)


if __name__ == "__main__":
    main()
