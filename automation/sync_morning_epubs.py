#!/usr/bin/env python3
"""Discover new morning-revival volumes and publish them to Cloudflare R2."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


NOTION_API = "https://www.notion.so/api/v3"
NOTION_MASTER_PAGE = os.getenv("NOTION_MASTER_PAGE", "b1935b21-f287-4bc4-a928-cae9385f717d")
CATALOG_URL = os.getenv(
    "MORNING_CATALOG_URL",
    "https://pub-60f079aabd834aaaab067a13b0b82c48.r2.dev/morning-revival-epubs/catalog.json",
)
PUBLIC_BASE = os.getenv(
    "R2_PUBLIC_BASE",
    "https://pub-60f079aabd834aaaab067a13b0b82c48.r2.dev",
).rstrip("/")
R2_PREFIX = os.getenv("R2_PREFIX", "morning-revival-epubs").strip("/")
GENERATED_SOURCES = {"ios-html-export", "stemofjesse-html", "generated-html"}
USER_AGENT = "LordsLibraryMorningSync/1.0 (+https://github.com/klay-ke/lord-library-release)"
NOTION_HEADERS = {
    "User-Agent": USER_AGENT,
    "Content-Type": "application/json",
    "Notion-Client-Version": "23.13.0.0",
}


@dataclass(frozen=True)
class Block:
    id: str
    type: str
    title: str
    source: str
    content: tuple[str, ...]
    parent_id: str


@dataclass(frozen=True)
class Candidate:
    code: str
    year: int
    sequence: int
    event_id: str
    event_title: str
    label: str


def log(message: str) -> None:
    print(message, flush=True)


def request_bytes(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> bytes:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=merged)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError(str(last_error))


def request_json(url: str) -> dict:
    return json.loads(request_bytes(url).decode("utf-8"))


def notion_post(endpoint: str, payload: dict) -> dict:
    return json.loads(
        request_bytes(
            f"{NOTION_API}/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers=NOTION_HEADERS,
        ).decode("utf-8")
    )


def rich_text(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        item[0] for item in value
        if isinstance(item, list) and item and isinstance(item[0], str)
    ).strip()


def load_notion_page(page_id: str) -> dict[str, Block]:
    data = notion_post("loadPageChunk", {
        "pageId": page_id,
        "limit": 100,
        "cursor": {"stack": []},
        "chunkNumber": 0,
        "verticalColumns": False,
    })
    blocks: dict[str, Block] = {}
    for key, wrapper in data.get("recordMap", {}).get("block", {}).items():
        value = wrapper.get("value", {})
        value = value.get("value", value)
        properties = value.get("properties", {})
        block_id = value.get("id", key)
        blocks[block_id] = Block(
            id=block_id,
            type=value.get("type", ""),
            title=rich_text(properties.get("title")),
            source=rich_text(properties.get("source")),
            content=tuple(value.get("content", [])),
            parent_id=value.get("parent_id", ""),
        )
    if page_id not in blocks:
        raise RuntimeError(f"Notion 页面不可读取：{page_id}")
    return blocks


def discover_candidates() -> list[Candidate]:
    master = load_notion_page(NOTION_MASTER_PAGE)
    master_root = master[NOTION_MASTER_PAGE]
    year_pages = [
        master[child]
        for child in master_root.content
        if child in master and master[child].type == "page"
        and re.fullmatch(r"20\d{2}年", master[child].title)
    ]
    candidates: list[Candidate] = []
    for year_page in year_pages:
        year = int(year_page.title[:4])
        page = load_notion_page(year_page.id)
        root = page[year_page.id]
        for child in root.content:
            event = page.get(child)
            if not event or event.type != "page":
                continue
            match = re.match(rf"^{year}[-.](\d{{2}})\s*(.+)$", event.title)
            if not match:
                continue
            sequence = int(match.group(1))
            candidates.append(Candidate(
                code=f"{year}.{sequence:02d}",
                year=year,
                sequence=sequence,
                event_id=event.id,
                event_title=event.title,
                label=match.group(2).strip(),
            ))
    return sorted(candidates, key=lambda item: (item.year, item.sequence))


def discover_latest_candidate() -> Candidate:
    master = load_notion_page(NOTION_MASTER_PAGE)
    master_root = master[NOTION_MASTER_PAGE]
    year_pages = [
        master[child]
        for child in master_root.content
        if child in master and master[child].type == "page"
        and re.fullmatch(r"20\d{2}年", master[child].title)
    ]
    if not year_pages:
        raise RuntimeError("Notion 总页没有年份页面")
    latest_year_page = max(year_pages, key=lambda block: int(block.title[:4]))
    year = int(latest_year_page.title[:4])
    page = load_notion_page(latest_year_page.id)
    root = page[latest_year_page.id]
    events: list[Candidate] = []
    # “最底下”严格采用 Notion content 的显示顺序，不自行按卷号重排。
    for child in root.content:
        event = page.get(child)
        if not event or event.type != "page":
            continue
        match = re.match(rf"^{year}[-.](\d{{2}})\s*(.+)$", event.title)
        if not match:
            continue
        sequence = int(match.group(1))
        events.append(Candidate(
            code=f"{year}.{sequence:02d}",
            year=year,
            sequence=sequence,
            event_id=event.id,
            event_title=event.title,
            label=match.group(2).strip(),
        ))
    if not events:
        raise RuntimeError(f"Notion {year} 年页面没有卷书")
    return events[-1]


def page_topic(blocks: dict[str, Block]) -> str:
    for block in blocks.values():
        match = re.match(r"^(?:总题|主题)\s*[:：]\s*(.+)$", block.title)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def find_resource_page(event: Candidate, blocks: dict[str, Block]) -> Block | None:
    root = blocks[event.event_id]
    children = [blocks[child] for child in root.content if child in blocks]
    return next(
        (block for block in children if block.type == "page" and "资源" in block.title),
        None,
    )


def signed_notion_url(block: Block) -> str:
    if block.source.startswith(("https://", "http://")):
        return block.source
    response = notion_post("getSignedFileUrls", {
        "urls": [{
            "url": block.source,
            "permissionRecord": {"table": "block", "id": block.id},
        }]
    })
    urls = response.get("signedUrls", [])
    if not urls:
        raise RuntimeError(f"Notion 没有返回附件地址：{block.title}")
    return urls[0]


def epub_from_notion(event: Candidate, event_blocks: dict[str, Block]) -> tuple[bytes, str] | None:
    resource = find_resource_page(event, event_blocks)
    if resource is None:
        return None
    blocks = load_notion_page(resource.id)
    files = [block for block in blocks.values() if block.type == "file" and block.source]
    direct = next((block for block in files if block.title.lower().endswith(".epub")), None)
    if direct:
        return request_bytes(signed_notion_url(direct), timeout=240), "notion-epub"
    archive = next((
        block for block in files
        if block.title.lower().endswith(".zip")
        and "epub" in block.title.lower()
        and "pdb" in block.title.lower()
    ), None)
    if archive is None:
        return None
    payload = request_bytes(signed_notion_url(archive), timeout=240)
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        epubs = [
            name for name in bundle.namelist()
            if not name.endswith("/") and name.lower().endswith(".epub")
        ]
        if not epubs:
            raise RuntimeError(f"{archive.title} 内没有 EPUB")
        selected = max(epubs, key=lambda name: bundle.getinfo(name).file_size)
        return bundle.read(selected), "notion-zip"


def stem_page_url(event: Candidate) -> str:
    wiki_id = f"晨兴圣言:{event.year}:{event.code}.{event.label}"
    return "https://stemofjesse.org/doku/doku.php/" + urllib.parse.quote(wiki_id, safe=":")


def stem_direct_epub(source_url: str) -> bytes | None:
    try:
        page = request_bytes(source_url).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if "This topic does not exist yet" in page or "该主题尚不存在" in page:
        return None
    links = re.findall(r"""href=["']([^"']+\.epub(?:\?[^"']*)?)["']""", page, re.I)
    if not links:
        material_url = source_url.rstrip("/") + ":" + urllib.parse.quote("材料下载")
        material = request_bytes(material_url).decode("utf-8", errors="replace")
        links = re.findall(r"""href=["']([^"']+\.epub(?:\?[^"']*)?)["']""", material, re.I)
    if not links:
        return None
    return request_bytes(urllib.parse.urljoin(source_url, html.unescape(links[0])), timeout=240)


def generate_from_stem(event: Candidate, title: str, source_url: str, output: Path) -> bytes:
    generator = Path(__file__).resolve().parent / "morning_epub" / "morning_epub.py"
    destination = output / "generated"
    command = [
        sys.executable, str(generator),
        "--source-url", source_url,
        "--year", str(event.year),
        "--id", f"晨兴圣言:{event.year}:{event.code}.{event.label}",
        "--title", title,
        "--output", str(destination),
    ]
    subprocess.run(command, check=True)
    generated = list((destination / "epub" / str(event.year)).glob("*.epub"))
    if len(generated) != 1:
        raise RuntimeError(f"HTML 转换结果不是一本 EPUB：{generated}")
    return generated[0].read_bytes()


def validate_epub(payload: bytes) -> dict:
    if len(payload) < 1024:
        raise RuntimeError("EPUB 文件过小")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("EPUB CRC 校验失败")
        names = archive.namelist()
        if "META-INF/container.xml" not in names:
            raise RuntimeError("EPUB 缺少 META-INF/container.xml")
        opfs = [name for name in names if name.lower().endswith(".opf")]
        pages = [
            name for name in names
            if name.lower().endswith((".xhtml", ".html", ".htm"))
        ]
        if not opfs or not pages:
            raise RuntimeError("EPUB 缺少 OPF 或正文")
        readable = 0
        for name in pages:
            text = archive.read(name).decode("utf-8", errors="ignore")
            plain = re.sub(r"<[^>]+>", "", text)
            if len(html.unescape(plain).strip()) >= 20:
                readable += 1
        if readable == 0:
            raise RuntimeError("EPUB 没有可读正文")
        return {"pageCount": len(pages), "readablePageCount": readable}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_catalog() -> dict:
    catalog = request_json(CATALOG_URL)
    if catalog.get("schemaVersion") != 1 or not isinstance(catalog.get("years"), list):
        raise RuntimeError("线上 catalog.json 格式不受支持")
    return catalog


def catalog_codes(catalog: dict) -> set[str]:
    return {
        book["code"]
        for year in catalog["years"]
        for book in year.get("books", [])
        if isinstance(book.get("code"), str)
    }


def catalog_book(catalog: dict, code: str) -> dict | None:
    return next((
        book
        for year in catalog["years"]
        for book in year.get("books", [])
        if book.get("code") == code
    ), None)


def encoded_public_url(path: str, digest: str) -> str:
    encoded = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return f"{PUBLIC_BASE}/{encoded}?sha256={digest}"


def r2_client():
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("请安装 requirements.txt") from error
    required = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("缺少 R2 配置：" + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def add_catalog_book(catalog: dict, item: dict) -> None:
    group = next((group for group in catalog["years"] if group["year"] == item["year"]), None)
    if group is None:
        group = {"year": item["year"], "count": 0, "books": []}
        catalog["years"].append(group)
    group["books"] = [
        book for book in group["books"] if book.get("code") != item["code"]
    ] + [item]
    group["books"].sort(key=lambda book: book["sequence"])
    group["count"] = len(group["books"])


def finalize_catalog(catalog: dict) -> bytes:
    catalog["years"].sort(key=lambda group: group["year"])
    canonical = json.dumps(
        catalog["years"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    catalog["generatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    catalog["contentHash"] = hashlib.sha256(canonical).hexdigest()
    catalog["yearCount"] = len(catalog["years"])
    catalog["bookCount"] = sum(group["count"] for group in catalog["years"])
    catalog["yearRange"] = {
        "from": min(group["year"] for group in catalog["years"]),
        "to": max(group["year"] for group in catalog["years"]),
    }
    return (json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def verify_public_object(path: str, expected: bytes) -> None:
    url = f"{PUBLIC_BASE}/" + "/".join(urllib.parse.quote(part) for part in path.split("/"))
    expected_hash = sha256_bytes(expected)
    for attempt in range(6):
        try:
            actual = request_bytes(url, timeout=180)
            if sha256_bytes(actual) == expected_hash:
                return
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    raise RuntimeError(f"R2 公网校验失败：{url}")


def sync(*, dry_run: bool) -> int:
    catalog = load_catalog()
    event = discover_latest_candidate()
    existing = catalog_book(catalog, event.code)
    needs_new = existing is None
    needs_native_upgrade = (
        existing is not None and existing.get("source", "") in GENERATED_SOURCES
    )
    if needs_new:
        log(f"Notion 最新卷：{event.code} {event.label}；catalog 尚未收录")
    elif needs_native_upgrade:
        log(
            f"Notion 最新卷：{event.code} {event.label}；"
            f"catalog 当前为生成版 ({existing.get('source')})，检查原生 EPUB"
        )
    else:
        log(
            f"Notion 最新卷：{event.code} {event.label}；"
            f"catalog 已是原生版 ({existing.get('source', 'unknown')})"
        )
        return 0
    if dry_run:
        action = "新增" if needs_new else "检查并升级原生版"
        log(f"[待处理:{action}] {event.code} {event.label}")
        return 0

    client = r2_client()
    bucket = os.environ["R2_BUCKET"]
    with tempfile.TemporaryDirectory(prefix="morning-sync-") as temporary:
        temp = Path(temporary)
        log(f"[{event.code}] 检查 Notion")
        event_blocks = load_notion_page(event.event_id)
        topic = page_topic(event_blocks)
        title = f"{event.year}年{event.label}" + (f" {topic}" if topic else "")
        result = epub_from_notion(event, event_blocks)
        source_url = f"https://www.notion.so/{event.event_id.replace('-', '')}"
        if result is None:
            source_url = stem_page_url(event)
            log(f"[{event.code}] Notion 无原生 EPUB，检查 Stem of Jesse")
            direct = stem_direct_epub(source_url)
            if direct is not None:
                result = (direct, "stemofjesse-epub")

        # 已有生成版时只在找到原生文件后覆盖；没有原生文件便保留现状。
        if result is None and needs_native_upgrade:
            log(f"[{event.code}] 尚无原生 EPUB，保留当前生成版")
            return 0
        if result is None:
            log(f"[{event.code}] 两处均无原生 EPUB，从 HTML 生成")
            payload = generate_from_stem(event, title, source_url, temp)
            origin = "stemofjesse-html"
        else:
            payload, origin = result

        stats = validate_epub(payload)
        digest = sha256_bytes(payload)
        file_name = (
            existing.get("fileName")
            if existing and existing.get("fileName")
            else f"{event.code}.{event.label}.epub"
        )
        object_path = (
            existing.get("path")
            if existing and existing.get("path")
            else f"{R2_PREFIX}/{event.year}/{file_name}"
        )
        log(f"[{event.code}] 上传 {len(payload):,} bytes ({origin})")
        client.put_object(
            Bucket=bucket,
            Key=object_path,
            Body=payload,
            ContentType="application/epub+zip",
            CacheControl="public, max-age=31536000, immutable",
            Metadata={"sha256": digest, "source": origin},
        )
        verify_public_object(object_path, payload)
        add_catalog_book(catalog, {
            **(existing or {}),
            "id": event.code,
            "code": event.code,
            "year": event.year,
            "sequence": event.sequence,
            "title": title,
            "fileName": file_name,
            "path": object_path,
            "downloadURL": encoded_public_url(object_path, digest),
            "mediaType": "application/epub+zip",
            "sizeBytes": len(payload),
            "sha256": digest,
            "source": origin,
            "sourceURL": source_url,
            **stats,
        })

        catalog_bytes = finalize_catalog(catalog)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history_path = f"{R2_PREFIX}/catalog-history/catalog-{stamp}.json"
        client.put_object(
            Bucket=bucket, Key=history_path, Body=catalog_bytes,
            ContentType="application/json; charset=utf-8",
            CacheControl="public, max-age=31536000, immutable",
        )
        client.put_object(
            Bucket=bucket, Key=f"{R2_PREFIX}/catalog.json", Body=catalog_bytes,
            ContentType="application/json; charset=utf-8",
            CacheControl="public, max-age=60, must-revalidate",
        )
        verify_public_object(f"{R2_PREFIX}/catalog.json", catalog_bytes)
        action = "新增" if needs_new else "原生版升级"
        log(f"发布完成：{action} {event.code}，catalog hash={catalog['contentHash']}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只发现新增卷，不上传")
    args = parser.parse_args()
    sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
