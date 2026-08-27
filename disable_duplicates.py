#!/usr/bin/env python3
"""
Disable duplicate feeds in config/sites.yaml by adding 'enabled: false'.
Non-interactive version - use --apply to make changes.
"""
from pathlib import Path
import sys
import argparse


def disable_duplicates(config_path: Path, apply: bool = False):
    """Add 'enabled: false' to duplicate feed entries."""

    # Feeds to disable (identified as duplicates)
    DUPLICATES_TO_DISABLE = [
        'serialeonline-movies',      # 100% duplicate of filmehd-cc-filme
        'serialeonline-episodes',    # 100% duplicate of filmehd-cc-seriale
        'fsonline-film',             # 100% duplicate of filmehd-filme
        'seriale-online-movies',     # 80-100% duplicate of filmehd-filme
    ]

    with open(config_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    modified_lines = []
    current_site = None
    in_duplicate_site = False
    already_has_enabled = False
    indent_level = 0
    changes_made = []

    for i, line in enumerate(lines):
        # Detect site definition (e.g., "  serialeonline-movies:")
        if line.strip().endswith(':') and not line.strip().startswith('#'):
            site_key = line.strip().rstrip(':')
            current_site = site_key
            in_duplicate_site = site_key in DUPLICATES_TO_DISABLE
            already_has_enabled = False
            indent_level = len(line) - len(line.lstrip())

        # Check if current line has 'enabled:' key
        if in_duplicate_site and 'enabled:' in line:
            already_has_enabled = True
            if 'enabled: true' in line or 'enabled:true' in line:
                line = line.replace('enabled: true', 'enabled: false')
                line = line.replace('enabled:true', 'enabled: false')
                changes_made.append(f"Updated {current_site}: enabled: true → enabled: false")

        modified_lines.append(line)

        # Add 'enabled: false' right after site key if needed
        if in_duplicate_site and i + 1 < len(lines):
            next_line = lines[i + 1]
            next_indent = len(next_line) - len(next_line.lstrip())

            if (next_indent > indent_level and
                not already_has_enabled and
                line.strip().endswith(':')):

                enabled_line = ' ' * (indent_level + 2) + 'enabled: false\n'
                modified_lines.append(enabled_line)
                already_has_enabled = True
                changes_made.append(f"Added {current_site}: enabled: false")

    # Print changes
    print("\n" + "=" * 80)
    print("CHANGES TO BE MADE:" if not apply else "CHANGES APPLIED:")
    print("=" * 80)
    for change in changes_made:
        print(f"  ✓ {change}")

    if not apply:
        print("\n" + "=" * 80)
        print("DRY RUN - No changes made")
        print("Run with --apply to make changes")
        print("=" * 80)
        return

    # Create backup
    backup_path = config_path.with_suffix('.yaml.backup')
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f"\n✅ Backup created: {backup_path}")

    # Write modified config
    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(modified_lines)
    print(f"✅ Updated: {config_path}")


def main():
    parser = argparse.ArgumentParser(description='Disable duplicate feeds in RSS generator config')
    parser.add_argument('--apply', action='store_true', help='Apply changes (default is dry run)')
    args = parser.parse_args()

    config_path = Path(__file__).parent / 'config' / 'sites.yaml'

    if not config_path.exists():
        print(f"❌ Error: Config file not found: {config_path}")
        sys.exit(1)

    print("=" * 80)
    print("DISABLE DUPLICATE FEEDS")
    print("=" * 80)
    print()
    print("Feeds to disable:")
    print("  • serialeonline-movies (100% duplicate of filmehd-cc-filme)")
    print("  • serialeonline-episodes (100% duplicate of filmehd-cc-seriale)")
    print("  • fsonline-film (100% duplicate of filmehd-filme)")
    print("  • seriale-online-movies (80-100% duplicate of filmehd-filme)")
    print()

    disable_duplicates(config_path, apply=args.apply)

    if args.apply:
        print()
        print("=" * 80)
        print("✅ DONE")
        print("=" * 80)
        print()
        print("Next steps:")
        print("  1. Run: PYTHONPATH=. python3 scripts/generate_feeds.py")
        print("  2. Verify: Only 12 feeds should be generated (down from 16)")
        print("  3. To re-enable: Change 'enabled: false' to 'enabled: true' in config")
        print()
    else:
        print()
        print("To apply these changes, run:")
        print("  python3 disable_duplicates.py --apply")
        print()


if __name__ == '__main__':
    main()
