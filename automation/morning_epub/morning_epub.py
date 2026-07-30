#!/usr/bin/env python3
"""Last-resort Stem of Jesse HTML to EPUB converter for the weekly sync."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup


UA = "LordsLibraryMorningSync/1.0"
FONT = (
    'system-ui, "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", '
    '"Droid Sans Fallback", "Microsoft YaHei", sans-serif'
)
CHINESE = "零一二三四五六七八九"


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def export_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}do=export_xhtml"


def chinese_number(number: int) -> str:
    if number < 10:
        return CHINESE[number]
    if number == 10:
        return "十"
    if number < 20:
        return "十" + CHINESE[number - 10]
    tens, ones = divmod(number, 10)
    return CHINESE[tens] + "十" + (CHINESE[ones] if ones else "")


def clean_fragment(source: str) -> str:
    soup = BeautifulSoup(source, "html.parser")
    root = soup.select_one("#dokuwiki__content .page") or soup.select_one(".page") or soup.body or soup
    for node in root.select("script, style, nav, .breadcrumbs, .docInfo, .pageId"):
        node.decompose()
    for link in root.select("a[href]"):
        href = link.get("href", "")
        if href.startswith(("/doku/", "https://stemofjesse.org/doku/")):
            link["href"] = urllib.parse.urljoin("https://stemofjesse.org", href)
    return str(root).replace("**", "")


def page(url: str, title: str, body: str, extra_class: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{html.escape(title)}</title>
<style>
html,body{{margin:0;padding:0;background:#f4f0e4;color:#523f2f;font-family:{FONT};}}
body{{padding:16px;font-size:18px;line-height:1.78;font-weight:500;}}
body *{{font-family:{FONT};}}
h1,h2,h3,h4{{color:#17735e;line-height:1.4;}}
.reader-topic{{color:#7b6e5f;text-align:left;}}
.reader-index-page{{position:fixed;inset:0;padding:0;display:flex;align-items:center;justify-content:center;text-align:center;}}
.reader-index-page h1{{margin:0;padding:0 8%;color:#523f2f;font-size:2rem;line-height:1.75;}}
.catalog-item{{display:flex;gap:1em;border-bottom:1px solid rgba(82,63,47,.12);padding:.75em 0;font-size:.9em;}}
.catalog-item a{{color:#17735e;text-decoration:none;}}
</style>
</head>
<body class="{extra_class}">{body}</body>
</html>"""


def discover(source_url: str) -> tuple[str, list[tuple[str, str]]]:
    source = fetch(source_url)
    soup = BeautifulSoup(source, "html.parser")
    title_node = soup.select_one("#dokuwiki__content h1") or soup.select_one("h1")
    parsed_title = title_node.get_text(" ", strip=True) if title_node else ""
    weeks: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.select("#dokuwiki__content a[data-wiki-id], #dokuwiki__content a.wikilink1"):
        label = link.get_text(" ", strip=True)
        if not re.match(r"^第[一二三四五六七八九十百]+周(?:\s|$)", label):
            continue
        url = urllib.parse.urljoin(source_url, link.get("href", ""))
        if url and url not in seen:
            seen.add(url)
            weeks.append((label, url))
    return parsed_title, weeks


def discover_days(week_url: str) -> list[tuple[str, str]]:
    source = fetch(week_url)
    soup = BeautifulSoup(source, "html.parser")
    days: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in soup.select("#dokuwiki__content a[data-wiki-id], #dokuwiki__content a.wikilink1"):
        label = link.get_text(" ", strip=True)
        match = re.search(r"(周[一二三四五六日])", label)
        if not match:
            continue
        url = urllib.parse.urljoin(week_url, link.get("href", ""))
        if url and url not in seen:
            seen.add(url)
            days.append((match.group(1), url))
    return days[:7]


def package(
    *,
    source_url: str,
    year: str,
    identifier: str,
    requested_title: str,
    destination: Path,
) -> Path:
    parsed_title, weeks = discover(source_url)
    if not weeks:
        raise RuntimeError("Stem of Jesse 页面没有周目录")
    full_title = parsed_title or requested_title
    event_match = re.match(rf"^{year}年(\S+)", full_title)
    cover_title = f"{year}年{event_match.group(1)}" if event_match else requested_title.split(" ", 1)[0]
    chapters: list[tuple[str, str, str]] = []
    chapters.append((
        "title.xhtml", "书名页",
        page(source_url, "书名页", f"<h1>{html.escape(cover_title)}</h1>", "reader-index-page"),
    ))
    catalog_items = "".join(
        f'<div class="catalog-item"><span>{index:02d}</span>'
        f'<a href="week-{index:02d}.xhtml">{html.escape(label)}</a></div>'
        for index, (label, _) in enumerate(weeks, 1)
    )
    chapters.append((
        "catalog.xhtml", "目录",
        page(source_url, "目录", f'<h1 class="reader-topic">总题：{html.escape(full_title)}</h1>{catalog_items}'),
    ))
    for week_index, (week_label, week_url) in enumerate(weeks, 1):
        week_file = f"week-{week_index:02d}.xhtml"
        week_body = clean_fragment(fetch(export_url(week_url)))
        chapters.append((
            week_file, f"第{chinese_number(week_index)}周 纲目",
            page(week_url, week_label, week_body),
        ))
        for day_index, (day_label, day_url) in enumerate(discover_days(week_url), 1):
            day_body = clean_fragment(fetch(export_url(day_url)))
            chapters.append((
                f"w{week_index:02d}-day-{day_index:02d}.xhtml",
                f"第{chinese_number(week_index)}周 {day_label}",
                page(day_url, day_label, day_body),
            ))

    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"{year}.generated.epub"
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, identifier)}"
    modified = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = "\n".join(
        f'<item id="c{i}" href="text/{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _, _) in enumerate(chapters)
    )
    spine = "\n".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    nav = "\n".join(
        f'<li><a href="text/{name}">{html.escape(title)}</a></li>'
        for name, title, _ in chapters
    )
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{book_id}</dc:identifier>
<dc:title>{html.escape(full_title)}</dc:title><dc:language>zh-CN</dc:language>
<dc:source>{html.escape(source_url)}</dc:source>
<meta property="dcterms:modified">{modified}</meta>
</metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{manifest}</manifest>
<spine>{spine}</spine></package>"""
    nav_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目录</title></head><body><nav epub:type="toc"><h1>目录</h1><ol>{nav}</ol></nav></body></html>"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
        for name, _, content in chapters:
            archive.writestr(f"OEBPS/text/{name}", content)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = package(
        source_url=args.source_url,
        year=args.year,
        identifier=args.id,
        requested_title=args.title,
        destination=args.output / "epub" / args.year,
    )
    print(output)


if __name__ == "__main__":
    main()
