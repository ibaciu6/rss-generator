#!/usr/bin/env python3
"""Probe all 49 feeds: check 3 items each for title/link/poster existence + HTTP reachability + clone detection."""
import sys, re, json
from pathlib import Path
import httpx
import asyncio
from xml.etree import ElementTree as ET

FEEDS_DIR = Path("feeds")
SAMPLE_SIZE = 3
HTTP_TIMEOUT = 5.0

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

async def quick_head(client, url):
    try:
        r = await client.head(url, follow_redirects=True, timeout=HTTP_TIMEOUT)
        return (r.status_code, str(r.url))
    except Exception:
        try:
            r = await client.get(url, follow_redirects=True, timeout=HTTP_TIMEOUT)
            return (r.status_code, str(r.url))
        except Exception as e:
            return (0, str(e)[:80])

def extract_image_urls(desc):
    urls = []
    for m in re.finditer(r'<img[^>]+src="([^"]+)"', desc):
        url = m.group(1).strip()
        if not url or url.startswith("data:") or url.startswith("/themes/"):
            continue
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            continue
        urls.append(url)
    return urls

def parse_feed(path):
    tree = ET.parse(path)
    channel = tree.find(".//channel")
    title = channel.findtext("title", "") if channel is not None else path.stem
    items = []
    for item in tree.findall(".//item"):
        link = item.findtext("link", "") or item.findtext("guid", "")
        ititle = item.findtext("title", "")
        content = item.find(f"{{{CONTENT_NS}}}encoded")
        desc = (content.text if content is not None else item.findtext("description", "")) or ""
        items.append({"title": ititle, "link": link, "description": desc})
    return {"title": title, "items": items}

async def main():
    feeds = sorted(FEEDS_DIR.glob("*.xml"))
    print(f"Scanning {len(feeds)} feeds, {SAMPLE_SIZE} items each...\n")

    results = {}
    clone_pool = {}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for fp in feeds:
            name = fp.stem
            data = parse_feed(fp)
            items = data["items"]
            total = len(items)

            if not items:
                results[name] = {"status": "FAIL", "total": 0, "errors": ["No items"]}
                clone_pool[name] = []
                print(f"  ! {name:40s} EMPTY")
                continue

            sample = items[:min(SAMPLE_SIZE, total)]
            errors = []
            links_ok = 0
            posters_ok = 0
            posters_total = 0

            for item in sample:
                title = item["title"] or ""
                link = item["link"] or ""
                desc = item["description"] or ""

                if not title:
                    errors.append(f"no title: {link[:60]}")
                if not link:
                    errors.append("no link")
                elif link.startswith("/"):
                    errors.append(f"relative link: {link[:60]}")
                else:
                    code, _ = await quick_head(client, link)
                    if code == 0:
                        errors.append(f"link unreachable: {link[:60]}")
                    elif code >= 400:
                        errors.append(f"link HTTP {code}: {link[:60]}")
                    else:
                        links_ok += 1

                if not desc or len(desc) < 10:
                    errors.append(f"empty description: {title[:40] or link[:40]}")
                else:
                    img_urls = extract_image_urls(desc)
                    if not img_urls and "<img" in desc:
                        errors.append(f"empty img src: {title[:40]}")
                    for img_url in img_urls:
                        posters_total += 1
                        code, _ = await quick_head(client, img_url)
                        if code == 0:
                            errors.append(f"poster unreachable: {img_url[:60]}")
                        elif code >= 400:
                            errors.append(f"poster HTTP {code}: {img_url[:60]}")
                        else:
                            posters_ok += 1

            status = "PASS"
            if errors:
                if any("unreachable" in e for e in errors):
                    status = "FAIL"
                else:
                    status = "WARN"

            results[name] = {
                "status": status,
                "total": total,
                "errors": errors,
                "links_ok": links_ok,
                "posters_ok": posters_ok,
                "posters_total": posters_total,
            }

            # Titles for clone detection (strip years)
            titles_clean = []
            for item in items[:10]:
                t = item["title"] or ""
                t = re.sub(r'\s*\(\d{4}\)', '', t).strip()
                titles_clean.append(t)
            clone_pool[name] = titles_clean

            icon = "P" if status == "PASS" else ("W" if status == "WARN" else "F")
            rev = f"{posters_ok}/{posters_total}" if posters_total > 0 else "-"
            print(f"  [{icon}] {name:40s} cnt={total:3d} l={links_ok} p={rev} err={len(errors)}")

    # Summary
    pass_c = sum(1 for r in results.values() if r["status"] == "PASS")
    warn_c = sum(1 for r in results.values() if r["status"] == "WARN")
    fail_c = sum(1 for r in results.values() if r["status"] == "FAIL")
    print(f"\n{'='*70}")
    print(f"PASS={pass_c} WARN={warn_c} FAIL={fail_c}")

    if fail_c > 0 or warn_c > 0:
        print(f"\n--- BROKEN FEEDS ---")
        for name, r in sorted(results.items()):
            if r["status"] in ("FAIL", "WARN"):
                print(f"\n  [{r['status']}] {name} ({r['total']} items)")
                for e in r["errors"][:8]:
                    print(f"      {e}")
                if len(r["errors"]) > 8:
                    print(f"      ...+{len(r['errors'])-8} more")

    # Clone detection
    print(f"\n{'='*70}")
    print("CLONE DETECTION (title-based, first 10 items):")
    clone_found = False
    feed_names = list(clone_pool.keys())
    threshold = 8  # 8/10 titles must match

    for i in range(len(feed_names)):
        for j in range(i+1, len(feed_names)):
            a, b = feed_names[i], feed_names[j]
            ta = clone_pool[a]
            tb = clone_pool[b]
            if not ta or not tb:
                continue
            matches = sum(1 for t in ta if t in tb)
            if matches >= threshold:
                clone_found = True
                print(f"\n  CLONE: {a} <-> {b}")
                print(f"    Matching titles: {matches}/{min(len(ta), len(tb))}")
                for t in ta[:5]:
                    print(f"      - {t[:60]}")
                if len(ta) > 5:
                    print(f"      ...")

    if not clone_found:
        print("\n  No clone sites detected (threshold: 8/10 titles match)")

if __name__ == "__main__":
    asyncio.run(main())
