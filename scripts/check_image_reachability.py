#!/usr/bin/env python3
"""Which image hosts can *this* network actually reach?

Answers a question that is easy to get wrong in both directions. A broken image
in the reader is not evidence that the feed is missing it: an SNI-filtering box
on the path will accept the TCP connection, present the wrong certificate or
stall the request, and look exactly like a dead image. A run of the pipeline
happens on a datacenter with no such filter, so the published feed can be
perfectly correct while the home connection cannot load a single frame.

The distinction matters when reviewing feeds. "darknet's images are missing" was
diagnosed here as an upstream fault on the strength of one failed request from
behind a filter; the site serves a certificate for `*.rcs-rds.ro` at an
RCS & RDS address, which is the network's edge answering, not the publisher.

Two failure shapes are reported separately, because they mean different things:

  cert-mismatch  the peer answered with a certificate for a different name --
                  the request never reached the publisher
  stalled        the TCP connection was accepted and then went silent -- also
                  a filtering signature
  ok             a real HTTP response

Run it from the same connection you read the feeds on.
"""
from __future__ import annotations

import argparse
import glob
import re
import socket
import ssl
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

IMG_SRC = re.compile(r'<img[^>]+src="(https?://[^"]+)"', re.I)


def feed_image_hosts(feeds_dir: str) -> Counter:
    hosts: Counter = Counter()
    for path in sorted(glob.glob(f"{feeds_dir}/*.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        channel = root.find("channel")
        for item in (channel if channel is not None else root).findall("item"):
            body = item.findtext("description") or ""
            enc = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if enc is not None and enc.text:
                body = enc.text
            for src in IMG_SRC.findall(body):
                host = urlsplit(src).netloc.lower()
                if host:
                    hosts[host] += 1
    return hosts


def probe(host: str, timeout: float) -> tuple[str, str]:
    """(verdict, detail) for one host, from this network."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw, \
                ctx.wrap_socket(raw, server_hostname=host) as tls:
            return "ok", tls.version() or ""
    except ssl.SSLCertVerificationError as exc:
        # The peer answered, with a certificate for some other name.
        return "cert-mismatch", str(exc.verify_message or exc)
    except ssl.SSLError as exc:
        return "tls-error", str(exc)
    except TimeoutError:
        return "stalled", "connected, no TLS response"
    except (socket.gaierror, OSError) as exc:
        return "unreachable", str(exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feeds", default="feeds", help="directory of feed XML")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args(argv)

    hosts = feed_image_hosts(args.feeds)
    if not hosts:
        print(f"no images found in {args.feeds}/")
        return 0

    print(f"{len(hosts)} distinct image hosts across {args.feeds}/\n")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda h: probe(h, args.timeout), sorted(hosts)))

    verdicts: dict[str, list[str]] = {}
    for host, (verdict, _detail) in zip(sorted(hosts), results, strict=True):
        verdicts.setdefault(verdict, []).append(host)

    for verdict in ("ok", "cert-mismatch", "stalled", "tls-error", "unreachable"):
        found = verdicts.get(verdict)
        if not found:
            continue
        print(f"{verdict} ({len(found)}):")
        for host in found:
            print(f"    {host:44} {hosts[host]:5} images in the feeds")
        print()

    blocked = [h for v in ("cert-mismatch", "stalled") for h in verdicts.get(v, [])]
    if blocked:
        print("These are unreachable from HERE, not necessarily broken in the feed.")
        print("The pipeline runs on a datacenter with no such filter, so an image")
        print("that will not load on this connection can still be published intact.")
        print("Verify a feed's images from a different network before changing code.")
        return 0
    print("every image host reached from this network")
    return 0


if __name__ == "__main__":
    sys.exit(main())
