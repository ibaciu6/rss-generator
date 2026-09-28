"""Extensive testing of the cosmetic filter set against the real feeds.

For every rule: how many elements it removed, how much body text went with
them, how many images, and -- the number that matters -- which rules were
refused by the content budget. A rule that never fires is dead weight; a rule
that fires on 50 items is either the point or a bug, and this is what tells
you which.
"""
import glob
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.enrichers.cosmetic_filters import apply_cosmetic_filters, load_rules

STRIP = re.compile(r'<(script|style|noscript)\b.*?</\1>', re.S | re.I)
FILTERS = Path('config/cosmetic-filters.txt')


def load_items(f):
    root = ET.parse(f).getroot()
    ch = root.find('channel')
    out = []
    for it in (ch if ch is not None else root).findall('item'):
        d = it.findtext('description') or ''
        enc = it.find('{http://purl.org/rss/1.0/modules/content/}encoded')
        if enc is not None and enc.text:
            d = enc.text
        link = it.findtext('link') or ''
        m = re.match(r'https?://([^/]+)', link)
        out.append({'title': it.findtext('title') or '', 'html': d,
                    'host': m.group(1) if m else None})
    return out


def vis(h):
    s = BeautifulSoup(h, 'html.parser')
    for t in s(['script', 'style', 'noscript']):
        t.decompose()
    return len(s.get_text(' ', strip=True))


def n_imgs(h):
    return len(BeautifulSoup(h, 'html.parser').find_all('img'))


def main():
    rules, exempts = load_rules(FILTERS)
    per_rule = defaultdict(lambda: {'items': 0, 'elems': 0, 'text': 0, 'imgs': 0, 'feeds': set()})
    rule_imgs = defaultdict(int)
    rule_chars = defaultdict(int)
    rule_srcs = defaultdict(list)
    refusals = defaultdict(list)
    per_feed = defaultdict(lambda: {'items': 0, 'text0': 0, 'text1': 0, 'imgs0': 0, 'imgs1': 0, 'fired': set()})
    bad = []

    for f in sorted(glob.glob('feeds/*.xml')):
        base = Path(f).stem
        for it in load_items(f):
            h, host = it['html'], it['host']
            if not h.strip():
                continue
            t0, i0 = vis(h), n_imgs(h)
            out, rep = apply_cosmetic_filters(h, rules, host=host, exempts=exempts)
            t1, i1 = vis(out), n_imgs(out)
            pf = per_feed[base]
            pf['items'] += 1
            pf['text0'] += t0
            pf['text1'] += t1
            pf['imgs0'] += i0
            pf['imgs1'] += i1
            for sel, n in rep.matched.items():
                r = per_rule[sel]
                r['items'] += 1
                r['elems'] += n
                r['feeds'].add(base)
                pf['fired'].add(sel)
                rule_imgs[sel] += rep.images.get(sel, 0)
                rule_chars[sel] += rep.chars.get(sel, 0)
                rule_srcs[sel].extend(rep.image_srcs.get(sel, []))
            for sel, why in rep.refused:
                refusals[sel].append((base, it['title'][:44], why))
            # An article that lost most of itself, or all of its images.
            if t0 > 800 and (t1 < t0 * 0.6 or (i0 >= 3 and i1 == 0)):
                bad.append((base, it['title'][:50], t0, t1, i0, i1, sorted(pf['fired'])[-3:]))

    print('=' * 100)
    print(f'{len(rules)} rules, {len(exempts)} exceptions, '
          f'{len(per_feed)} feeds, {sum(v["items"] for v in per_feed.values())} items')
    print('=' * 100)
    print(f'{"rule":58} {"elems":>6} {"items":>6} {"chars":>8} {"imgs":>5}  feeds')
    print('-' * 100)
    for sel, r in sorted(per_rule.items(), key=lambda kv: -kv[1]['elems']):
        print(f'{sel[:58]:58} {r["elems"]:6} {r["items"]:6} {rule_chars[sel]:8} '
              f'{rule_imgs[sel]:5}  {len(r["feeds"])}')
    if refusals:
        print('\nREFUSED by the content budget:')
        for sel, hits in sorted(refusals.items(), key=lambda kv: -len(kv[1])):
            print(f'  {sel[:58]:58} {len(hits):3}x  e.g. {hits[0][0]} / {hits[0][2]}')
    if rule_imgs:
        print('\nevery image a rule removed (exact, from the engine):')
        for sel in sorted(rule_imgs, key=lambda k: -rule_imgs[k]):
            srcs = sorted(set(rule_srcs[sel]))
            print(f'  {sel[:56]:56} {rule_imgs[sel]:4} imgs, {len(srcs)} distinct')
            for x in srcs[:6]:
                print(f'       {x[:92]}')

    print('\nnever fired (dead weight):')
    fired = set(per_rule)
    for r in rules:
        if r.selector not in fired:
            print(f'  {r.selector}')

    print('\nper-feed effect (only where something changed):')
    print(f'{"feed":34} {"items":>5} {"text kept":>10} {"imgs kept":>10}  rules fired')
    print('-' * 100)
    for base, pf in sorted(per_feed.items()):
        if pf['text0'] == pf['text1'] and pf['imgs0'] == pf['imgs1']:
            continue
        tk = f"{pf['text1']}/{pf['text0']} {pf['text1']*100//max(1,pf['text0'])}%"
        ik = f"{pf['imgs1']}/{pf['imgs0']} {pf['imgs1']*100//max(1,pf['imgs0'])}%"
        print(f'{base:34} {pf["items"]:5} {tk:>10} {ik:>10}  {len(pf["fired"])}')

    if bad:
        print('\n!! ITEMS THAT LOST MOST OF THEIR BODY -- investigate:')
        for base, title, t0, t1, i0, i1, fired in bad:
            print(f'  {base} / {title}')
            print(f'     text {t0} -> {t1}   imgs {i0} -> {i1}   fired: {fired}')
    else:
        print('\nno item lost more than 40% of its body text or all of its images')


if __name__ == '__main__':
    sys.exit(main())
