# Feed Enable/Disable Toggle System

The RSS generator now supports an `enabled` field for each feed in `config/sites.yaml`. This allows you to keep duplicate/fallback feeds in the configuration without generating them.

## How It Works

### Configuration

Add `enabled: false` to any feed entry to disable it:

```yaml
sites:
  serialeonline-movies:
    enabled: false  # ← This feed will be skipped
    display_name: SerialeOnline.live
    url: https://serialeonline.live/filme/
    method: playwright
    # ... rest of config
```

Feeds without the `enabled` field default to `enabled: true`.

### Code Changes

**1. `core/config.py`** - Added `enabled` field to `SiteConfig`:
```python
enabled: bool = True  # Default to enabled
```

**2. `core/engine.py`** - Filter disabled sites before generation:
```python
enabled_sites = [site for site in self._config.sites if site.enabled]
```

The engine logs disabled feeds at startup for visibility.

## Disabling Duplicate Feeds

### Automatic Method (Recommended)

Run the provided script:

```bash
cd /mnt/d/Download/tools/rss-generator
python3 disable_duplicates.py
```

This will:
1. Show what will be changed (dry run)
2. Ask for confirmation
3. Create a backup (`sites.yaml.backup`)
4. Add `enabled: false` to the 4 duplicate feeds

### Manual Method

Edit `config/sites.yaml` and add `enabled: false` to these feeds:

```yaml
sites:
  # ... other feeds ...

  serialeonline-movies:
    enabled: false  # Duplicate of filmehd-cc-filme
    display_name: SerialeOnline.live
    # ... rest of config

  serialeonline-episodes:
    enabled: false  # Duplicate of filmehd-cc-seriale
    display_name: SerialeOnline.live Episodes
    # ... rest of config

  fsonline-film:
    enabled: false  # Duplicate of filmehd-filme
    display_name: FSOnline
    # ... rest of config

  seriale-online-movies:
    enabled: false  # Duplicate of filmehd-filme (80-100% overlap)
    display_name: Seriale-Online.net
    # ... rest of config
```

## Usage

### Generate Feeds (Respects enabled Flag)

```bash
PYTHONPATH=. python3 scripts/generate_feeds.py
```

Disabled feeds will be skipped. Output will show:
```json
{"event": "engine.disabled_sites", "disabled_count": 4, "disabled_sites": [...]}
{"event": "engine.start", "sites": 12, "total_configured": 16, ...}
```

### Re-enable a Feed

Change `enabled: false` to `enabled: true` (or remove the line):

```yaml
serialeonline-movies:
  enabled: true  # ← Re-enabled
  display_name: SerialeOnline.live
  # ...
```

Or just delete the `enabled:` line entirely (defaults to true).

### Check Which Feeds Are Disabled

```bash
grep -B1 "enabled: false" config/sites.yaml
```

## Fallback Strategy

### When to Re-enable a Disabled Feed

If a primary feed fails, you can quickly enable its fallback:

1. **Primary site goes down:**
   ```bash
   # Check which site failed
   ls -lh feeds/*.xml | grep -E '(filmehd-cc-filme|filmehd-filme)'
   
   # If filmehd-cc-filme is failing, enable its fallback:
   # Edit config/sites.yaml: serialeonline-movies -> enabled: true
   ```

2. **Regenerate:**
   ```bash
   PYTHONPATH=. python3 scripts/generate_feeds.py
   ```

3. **Once primary recovers, disable fallback again**

### Monitoring

Add a cron job to check for failed feeds:

```bash
# Check for failure feeds (title contains "Failed")
grep -l "Failed to fetch" feeds/*.xml
```

If a primary feed is consistently failing, switch to its fallback permanently.

## Benefits

✅ **No Data Loss** - Duplicate configs remain for quick fallback  
✅ **Reduced Generation Time** - Only 12 feeds instead of 16 (25% faster)  
✅ **Easy Toggle** - Change one line to enable/disable  
✅ **Version Control Friendly** - Clear intent in config history  
✅ **Backward Compatible** - Existing configs work (default enabled: true)  

## Verification

After disabling duplicates:

```bash
# Count enabled feeds
grep -c "enabled: false" config/sites.yaml  # Should show 4

# Run generation
PYTHONPATH=. python3 scripts/generate_feeds.py

# Count generated feeds
ls -1 feeds/*.xml | wc -l  # Should show 12 (down from 16)

# List disabled sites from log
# Look for: {"event": "engine.disabled_sites", ...}
```

## Files

- **`disable_duplicates.py`** - Automatic disabling script
- **`FEED_TOGGLE_SYSTEM.md`** - This documentation
- **`core/config.py`** - Config with enabled field
- **`core/engine.py`** - Engine that filters disabled sites

## Support

To see all configuration options:
```bash
python3 -c "from core.config import SiteConfig; help(SiteConfig)"
```
