# [2026-10-29] - The player page is served from this repository again

- **`/rss-generator/player/` is the player again.** The Pages deploy used to copy
  `player/redirect.html` there, a forwarder to `https://ibaciu6.github.io/player/`.
  The user site removed its copy ("rss-generator remains its sole home"), so the
  forwarder led every ▶ Play link and the index's *Open Player* button to a 404.
  `player/index.html` is now published at that path, and the unused forwarder is
  gone; old links land on the page itself instead of bouncing. `PLAYER_PAGE_BASE_URL`
  already named this path, so published feeds need no rewrite — and the user-site
  URL stays in `LEGACY_PLAYER_PAGE_BASE_URLS` for any anchor that still carries it.

# [2026-10-29] - Fixed the IMDb year walk and the TMDb link's position under IMDb

- **The hourly deploy publishes again.** Three tests added with the TMDb link
  work were red on `main`, so the `Update RSS and Deploy Pages` run stopped at
  `Run tests` and the Pages site kept its previous artifact — including the
  `/player/` forwarder. The enricher now implements what those tests specify and
  the suite is green, which lets the deploy upload `player/redirect.html` as
  `/player/index.html` again.
- **IMDb suggestions are walked by year again.** Without an explicit year,
  `_imdb_suggest()` asks for `title year` at the current year and walks back one
  year at a time until a candidate is filed under the year it asked for. The
  year in the response is what selects the hit, so a 2025 release found while
  walking 2026 is not accepted until 2025 is reached. A year whose only
  same-title entry is the wrong medium stops the walk (an older film of the same
  name is a different title), and a single broad search remains the last resort.
- **An explicit year is one request.** When the item carries its own year, that
  year is searched once instead of walking back from the current year, which
  spent up to eleven requests to find the same entry.
- **TMDb sits directly below IMDb.** The TMDb link is inserted after the IMDb
  anchor — the generated one, or a site-provided one that sits later in the
  description — instead of ahead of it in the generated block. The
  `_has_tmdb_link()` guard keeps a re-run from adding a second one.
- **Published items are repaired in place.** Items enriched by the older block
  already carry TMDb above IMDb, and the link guard alone would keep that order
  forever. `_move_tmdb_under_imdb()` moves the anchor below the last IMDb anchor
  and drops the separator it leaves behind; an item with two TMDb anchors above
  IMDb keeps the first, because a leftover above IMDb would re-trigger the
  repair on every run. The move runs at the top of the item loop, ahead of the
  already-enriched skip and of the TMDb lookup, so it costs no API call and an
  item no lookup can resolve is repaired too. It is a no-op once the order is
  right, so the feeds converge instead of being rewritten every hour.

# [2026-10-28] - Fixed the ▶ Play link's parameters and the duplicate EpGuides anchors

- **One EpGuides link per item.** The streaming enricher no longer writes a page
  guessed from the title (`epguides.com/Celebrity+Masterchef/`), which 404s
  because real slugs are space-free CamelCase. `_attach_epguides_link()` is the
  only writer, and `scripts/fix_feeds.py` gained `dedupe_epguides_links()`, which
  drops the guessed form and collapses repeats — so published feeds with two
  anchors repair themselves on the next run.
- **▶ Play now reaches the episode.** `build_player_page_link()` passes
  `season`/`episode` parsed from the item title, and the resolved
  `epguides` slug.
- **Year folded into the title.** The link sends `title=Best Medicine (2026)`
  instead of a separate `year=` parameter the player ignored, so its header and
  trailer search use a disambiguated string.
- **Already-published links upgrade in place.** `_upgrade_player_anchor()`
  rewrites an old href (with `year=`, no episode) once, converging like the
  existing `/find?` → `/title/tt…/` upgrade. The rewrite rebuilds through the
  writer's own `build_player_page_url()`, so the two paths cannot drift apart —
  and re-quotes every value it read out of the old href, which is what keeps a
  percent-encoded `<` or `"` in a published link from coming back as live markup
  in a description.
- **The player page frames its metadata.** The row beside the embed servers is
  grouped by what a frame can hold: EpGuides (TV only) and TMDB are iframe
  buttons first, sharing the player's active state — TMDB straight to
  `/tv/{id}/season/{s}/episode/{e}` for an episode — then IMDb and Trailer as
  button-styled links marked ↗, because neither can be framed (YouTube sends
  `X-Frame-Options: SAMEORIGIN`; IMDb's AWS WAF challenge never completes in a
  third-party iframe — measured 2026-10-28 from a browser capture, `202` →
  `challenge.js` → `verify` → `202` → "Max challenge attempts exceeded"), and an
  external button also offers *Open in new tab*. The info bar shows title, year
  and `S{season}E{episode}`, and query-string ids are allowlisted before they
  reach an iframe `src`.
- **Manual lookup on the player page.** Below the metadata row: TMDb id, IMDb
  id, a Movie/TV select, and Season/episode inputs that appear when TV is
  chosen. The form is prefilled from the current URL and submits new query
  parameters, so any title can be played by id without going through a feed,
  and the result is a shareable link. It accepts only what the page can read
  back (digits, a `tt` id, season and episode as a pair) and refuses when there
  is no id, rather than reloading into its own error state. A stale `title` is
  not carried onto new ids.
- **An enriched item no longer costs a lookup every hour.** The short-circuit
  checked the per-server label tags, which feeds stopped writing when the page
  took over the catalogue — so the condition never settled and every torrent
  item re-ran the enrichment on every run. It now checks for the ▶ Play link
  itself, which settles as soon as the link is written. An item whose id never
  resolves stays in the set by design — it is re-asked against the cached TMDb
  miss (5 days) and writes nothing in the meantime.

# [YYYY-MM-DD] - Added standalone player page link to feed items in all player enrichers and end-to-end tests

- Ensured the `build_player_page_link` function is compliant with the `ruff` linter.

- Fixed any issues identified by `ruff`.

- Updated test suite to ensure all player servers are covered.

- Added `▶ Play` label to `_POSTER_LINK_LABELS` for consistency in feeds.

- Updated `scripts/e2e_player_servers.py` to include all 10 players and mock function to return more reliable info.
