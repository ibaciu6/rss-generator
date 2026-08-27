#!/usr/bin/env python3
"""
Analyze RSS feeds for duplicate content across different site URLs.
Identifies potential site clones/mirrors by comparing feed items.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple
import json


def parse_feed(feed_path: Path) -> List[Dict[str, str]]:
    """Extract items from an RSS feed."""
    try:
        tree = ET.parse(feed_path)
        root = tree.getroot()

        items = []
        # Handle both RSS and Atom feeds
        for item in root.findall('.//item'):
            title = item.find('title')
            link = item.find('link')

            if title is not None and link is not None:
                items.append({
                    'title': title.text or '',
                    'link': link.text or '',
                })

        return items
    except Exception as e:
        print(f"Error parsing {feed_path}: {e}")
        return []


def normalize_title(title: str) -> str:
    """Normalize title for comparison (remove extra spaces, lowercase)."""
    return ' '.join(title.lower().split())


def calculate_similarity(items1: List[Dict], items2: List[Dict]) -> Tuple[float, int, int]:
    """
    Calculate similarity between two feed item lists.
    Returns (similarity_ratio, common_count, total_unique)
    """
    titles1 = {normalize_title(item['title']) for item in items1}
    titles2 = {normalize_title(item['title']) for item in items2}

    common = titles1 & titles2
    total_unique = len(titles1 | titles2)

    if total_unique == 0:
        return 0.0, 0, 0

    similarity = len(common) / total_unique
    return similarity, len(common), total_unique


def analyze_feeds(feeds_dir: Path) -> Dict:
    """Analyze all feeds in directory for duplicates."""
    feeds = {}

    # Load all feeds
    for feed_file in feeds_dir.glob('*.xml'):
        if feed_file.name == 'index.html':
            continue
        items = parse_feed(feed_file)
        if items:
            feeds[feed_file.stem] = {
                'file': feed_file.name,
                'items': items,
                'count': len(items)
            }

    print(f"Loaded {len(feeds)} feeds with content\n")

    # Compare all pairs
    similarities = []
    feed_names = list(feeds.keys())

    for i, feed1 in enumerate(feed_names):
        for feed2 in feed_names[i+1:]:
            sim, common, total = calculate_similarity(
                feeds[feed1]['items'],
                feeds[feed2]['items']
            )

            if sim > 0.1:  # Only record if >10% similarity
                similarities.append({
                    'feed1': feed1,
                    'feed2': feed2,
                    'similarity': sim,
                    'common_items': common,
                    'total_unique': total,
                    'feed1_count': feeds[feed1]['count'],
                    'feed2_count': feeds[feed2]['count']
                })

    # Sort by similarity (highest first)
    similarities.sort(key=lambda x: x['similarity'], reverse=True)

    return {
        'feeds': feeds,
        'comparisons': similarities
    }


def print_report(analysis: Dict):
    """Print human-readable duplicate analysis report."""
    print("=" * 80)
    print("RSS FEED DUPLICATE ANALYSIS REPORT")
    print("=" * 80)
    print()

    print(f"Total feeds analyzed: {len(analysis['feeds'])}")
    print(f"Feed pairs with >10% similarity: {len(analysis['comparisons'])}")
    print()

    if not analysis['comparisons']:
        print("No significant duplicates found.")
        return

    print("POTENTIAL DUPLICATES (sorted by similarity):")
    print("-" * 80)

    # Group by similarity level
    high_similarity = [c for c in analysis['comparisons'] if c['similarity'] >= 0.7]
    medium_similarity = [c for c in analysis['comparisons'] if 0.3 <= c['similarity'] < 0.7]
    low_similarity = [c for c in analysis['comparisons'] if c['similarity'] < 0.3]

    if high_similarity:
        print("\n🚨 HIGH SIMILARITY (≥70%) - Likely clones/mirrors:")
        print("-" * 80)
        for comp in high_similarity:
            print(f"\n  {comp['feed1']} ↔ {comp['feed2']}")
            print(f"  Similarity: {comp['similarity']*100:.1f}%")
            print(f"  Common items: {comp['common_items']}/{comp['total_unique']}")
            print(f"  Feed sizes: {comp['feed1_count']} vs {comp['feed2_count']}")

    if medium_similarity:
        print("\n\n⚠️  MEDIUM SIMILARITY (30-70%) - Possible overlap:")
        print("-" * 80)
        for comp in medium_similarity:
            print(f"\n  {comp['feed1']} ↔ {comp['feed2']}")
            print(f"  Similarity: {comp['similarity']*100:.1f}%")
            print(f"  Common items: {comp['common_items']}/{comp['total_unique']}")

    if low_similarity:
        print(f"\n\nℹ️  LOW SIMILARITY (10-30%): {len(low_similarity)} pairs")
        print("  (Some content overlap but likely different sites)")

    print("\n" + "=" * 80)


def identify_clone_groups(comparisons: List[Dict], threshold: float = 0.7) -> List[Set[str]]:
    """Group feeds into clone clusters based on high similarity."""
    high_sim = [c for c in comparisons if c['similarity'] >= threshold]

    # Build adjacency graph
    graph = defaultdict(set)
    for comp in high_sim:
        graph[comp['feed1']].add(comp['feed2'])
        graph[comp['feed2']].add(comp['feed1'])

    # Find connected components (clone groups)
    visited = set()
    groups = []

    def dfs(node, group):
        if node in visited:
            return
        visited.add(node)
        group.add(node)
        for neighbor in graph[node]:
            dfs(neighbor, group)

    for node in graph:
        if node not in visited:
            group = set()
            dfs(node, group)
            if len(group) > 1:
                groups.append(group)

    return groups


def main():
    feeds_dir = Path(__file__).parent / 'feeds'

    print("Analyzing RSS feeds for duplicates...\n")
    analysis = analyze_feeds(feeds_dir)

    print_report(analysis)

    # Identify clone groups
    clone_groups = identify_clone_groups(analysis['comparisons'])

    if clone_groups:
        print("\n" + "=" * 80)
        print("IDENTIFIED CLONE GROUPS:")
        print("=" * 80)
        for i, group in enumerate(clone_groups, 1):
            print(f"\nGroup {i}: {len(group)} feeds")
            for feed in sorted(group):
                print(f"  - {feed}")

        print("\n💡 RECOMMENDATION:")
        print("   Consider keeping only one feed from each clone group.")
        print("   Check the actual websites to confirm they're mirrors.\n")

    # Save JSON report
    output_file = Path(__file__).parent / 'duplicate_analysis.json'
    with open(output_file, 'w') as f:
        json.dump({
            'timestamp': '2026-08-27',
            'total_feeds': len(analysis['feeds']),
            'comparisons': analysis['comparisons'],
            'clone_groups': [list(g) for g in clone_groups]
        }, f, indent=2)

    print(f"Detailed report saved to: {output_file}")


if __name__ == '__main__':
    main()
