# Exploration: watchtv.win player servers (comprehensive review)

Date: 2026-10-23  
Test targets: Fight Club (TMDB 550), Sinners (TMDB 1233413, tt31193180), The Matrix (603), Dune: Part Two (693134)

## WatchTV.win server configuration
From https://www.watchtv.win/movie/sacrifice-1284186/watch and https://www.watchtv.win/movie/sinners-1233413/watch:

```json
[
  {"id":"srchsh","name":"Instaplay","movie_url":"https://media.instantplay.click/embed/movie/{id}?autoplay=1","tv_url":"https://media.instantplay.click/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"Default · Progress","supports_progress":true},
  {"id":"vidsrc_buzz","name":"VidSrc","movie_url":"https://vidsrc.buzz/embed/movie/{id}?autoplay=true","tv_url":"https://vidsrc.buzz/embed/tv/{id}/{s}/{e}?autoplay=true","quality":"Ads Multi-Sub","supports_progress":false},
  {"id":"vidcore","name":"VidCore","movie_url":"https://vidcore.io/movie/{id}?autoplay=true","tv_url":"https://vidcore.io/tv/{id}/{s}/{e}?autoplay=true","quality":"Ads · Progress","supports_progress":true},
  {"id":"vembed","name":"VEmbed","movie_url":"https://vsembed.ru/embed/movie/{id}?autoplay=1","tv_url":"https://vsembed.ru/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidnest","name":"VidNest","movie_url":"https://vidnest.fun/movie/{id}?autoplay=1","tv_url":"https://vidnest.fun/tv/{id}/{s}/{e}?autoplay=1","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidfast","name":"VidFast","movie_url":"https://vidfast.vc/movie/{id}?autoPlay=true","tv_url":"https://vidfast.vc/tv/{id}/{s}/{e}?autoPlay=true","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidlink","name":"VidLink","movie_url":"https://vidlink.pro/movie/{id}?autoplay=true","tv_url":"https://vidlink.pro/tv/{id}/{s}/{e}?autoplay=true","quality":"1080p · Progress","supports_progress":true},
  {"id":"vidsrc","name":"VidSrc Pro","movie_url":"https://vidsrc.to/embed/movie/{id}","tv_url":"https://vidsrc.to/embed/tv/{id}/{s}/{e}","quality":"1080p Multi-Sub","supports_progress":false},
  {"id":"superembed","name":"SuperEmbed HD","movie_url":"https://multiembed.mov/?video_id={id}&tmdb=1","tv_url":"https://multiembed.mov/?video_id={id}&tmdb=1&s={s}&e={e}","quality":"1080p High Speed","supports_progress":false},
  {"id":"vidspark","name":"VidSpark","movie_url":"https://vidspark.to/movie/{id}?autoplay=1","tv_url":"https://vidspark.to/tv/{id}/{s}/{e}?autoplay=1","quality":"Progress","supports_progress":true},
  {"id":"peachify","name":"Peachify","movie_url":"https://peachify.top/embed/movie/{id}?autoplay=1","tv_url":"https://peachify.top/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"1080p · Progress","supports_progress":true},
  {"id":"moviesapi","name":"MoviesAPI","movie_url":"https://moviesapi.to/movie/{id}?autoPlay=true","tv_url":"https://moviesapi.to/tv/{id}/{s}/{e}?autoPlay=true","quality":"1080p · Progress","supports_progress":true}
]
```

## Site reviews (homepage + subpages)

| Site | Status | Title/Desc | Endpoints/Notes |
|---|---|---|---|
| **[CineSrc](https://cinesrc.st/)** | 200 | "CineSrc - Free Video Streaming API \| Embed Movies & TV Shows" — Free streaming API with minimal ads, embed via TMDB IDs. | Uses `a2.cineflix.st/api/script.js`. Has `/docs`, `/party`. **Already integrated** in enricher. Works well. |
| **[VidSrc Pro (vidsrc.to)](https://vidsrc.to/)** | 200 | "VidSrc - Next generation Video Streaming API" — Movie/TV streaming API by IMDB/TMDB. | Endpoints: `https://vidsrc.to/embed/movie/{id}`, `https://vidsrc.to/embed/tv/{id}/{s}/{e}`, vapi endpoints for listings. **Integrated** in enricher. Accepts IMDB preferred. |
| **[VidSrc.buzz](https://vidsrc.buzz/)** | 200 | "VIDSRC — Free Video Streaming Embed API" — Embed via IMDB/TMDB, HLS player, multi-server, subtitles. | Endpoints: `https://vidsrc.buzz/embed/movie/{id}`, `https://vidsrc.buzz/embed/tv/{id}/{s}/{e}`. **Iframe only, no sandbox**. Direct access blocked. Worked for Fight Club, 403 for Sinners in headless tests. |
| **[Instaplay](https://media.instantplay.click/)** | 200 | "AbyssStream — Enterprise Video Infrastructure" | Minimal page. Uses same vidsrc.sh chain as VEmbed/vidsrc.to (calls `vs_src.php` -> redirects to `stellarconductornexus.com`). Appears related to the vidsrc.sh family. |
| **[VEmbed](https://vsembed.ru/)** | 200 | "VidSrc — Free Video Streaming API" | Has full docs at `/vidsrc/docs` with detailed API: embed URLs, query params (`ds_lang`, `sub_url`, `startAt`, `theme`, `autoplay`), info endpoints (`/info/movie/{id}.json`, `/info/tv/{id}/{s}/{e}.json`), events, resume playback. Uses `vidsrc.sh` in examples. Same chain as Instaplay/vidsrc.to. |
| **[VidLink](https://vidlink.pro/)** | 200 | "VidLink" — "Biggest and Fastest Streaming API" | Endpoints: `https://vidlink.pro/movie/{tmdbId}`, `https://vidlink.pro/tv/{tmdbId}/{s}/{e}`. Has `/changelog`. Different API structure - calls `vidlink.pro/api/b/movie/...` to get streams from CDNs (surge.flooderrr.store). **Works for Sinners**. Most viable alternative to CineSrc/VidSrc. |
| **[VidCore](https://vidcore.io/)** | 200 | "VidCore — Next-Gen Video Embedding" — "Premium video embedding service built for developers. Zero config, blazing fast streaming." | Next.js app, minimal homepage. In tests gets stuck on "FETCHING YOUR CONTENT..." - no playable video surfaced. Heavy tracking/ads. |
| **[VidFast](https://vidfast.vc/)** | 200 | "VidFast" — "Fast, Reliable, and Scalable – The Streaming API You Need" | Next.js/Vite app (assets in `/assets/`). Redirects from `vidfast.pro`. In tests stuck on "FETCHING, ONE MOMENT...". Same architecture family as VidCore. |
| **[VidNest](https://vidnest.fun/)** | 200 | "VidNest – Seamless Movie, TV & Anime Streaming Embeds" — "Professional-grade streaming API for effortless movie, TV-show and anime embeds. Lightning-fast player, ad-free experience and fully customisable." | Endpoints: `https://vidnest.fun/movie/{tmdb}`, `https://vidnest.fun/tv/{tmdb}/{s}/{e}` with many query params (server, startAt, progress, hide UI elements). Uses TMDB API, wyzie/subtitle services, yflix backend. Loads data but no playback in tests. |
| **[VidSpark](https://vidspark.to/)** | 200 | "VidSpark - Free Movie Streaming API \| Best 2embed, VidSrc & SuperEmbed Alternative" — "Auto embed thousands of movies and TV shows with high quality and fast streaming. Totally free, no registration needed." | Endpoints: `https://vidspark.to/movie/{id}`, `https://vidspark.to/tv/{id}/{s}/{e}` or `tv/{id}-{s}-{e}`. Has discover APIs. Links to MoviesAPI/Telegram. **Same codebase as MoviesAPI** (identical structure). API returns 502 for some titles (Sinners). |
| **[MoviesAPI](https://moviesapi.to/)** | 200 | Same as VidSpark - "Best 2embed, VidSrc & SuperEmbed Alternative" | Identical to VidSpark. Same API family, same issues. Links to Telegram `@moviesapi_club`. |
| **[SuperEmbed](https://multiembed.mov/)** | 200 | No title/desc visible | Aggregator - redirects to `streamingnow.mov` with Cloudflare challenges/turnstile. Not easily automatable. |
| **[Peachify](https://peachify.top/)** | 523 | "peachify.top \| 523: Origin is unreachable" | **Down**. |

## Playback test results (Sinners 2025 TMDB 1233413)
| Server | Loads? | Plays? | Notes |
|---|---|---|---|
| CineSrc | Yes | **Yes** | Blob video, works well. Already integrated. |
| VidSrc Pro (vidsrc.to) | Yes | No | Goes through vsembed.ru -> stellarconductornexus.com, hits "Session expired". Works for older titles. |
| VidSrc.buzz | Yes | No (403) | Blocked for Sinners, worked for Fight Club. Hotlink/referrer sensitive. |
| Instaplay | Yes | No | Same chain as vidsrc.to, "Session expired". |
| VEmbed | Yes | No | Same chain, has good docs but playback fails for new releases. |
| **VidLink** | Yes | **Yes** | Gets sources via API, delivers playable streams. Most viable alternative. Different architecture. |
| VidCore | Yes | No | Stuck on "FETCHING YOUR CONTENT...". |
| VidFast | Yes | No | Stuck on "FETCHING, ONE MOMENT...". |
| VidNest | Yes | No | Loads metadata/APIs but no video element. |
| VidSpark | Yes | No | vidora API returns 502/403. |
| MoviesAPI | Yes | No | Same as VidSpark. |
| SuperEmbed | Yes | No | CF challenges. |
| Peachify | N/A | N/A | Down (523). |

## Key findings
- **Vidsrc.sh family** (vidsrc.to, vsembed.ru, media.instantplay.click) shares infrastructure (`data.vidsrc.sh` API) and redirects through `stellarconductornexus.com`. Works better for older/catalog titles, often fails with "Session expired" on newer releases like Sinners.
- **VidSrc.buzz** is separate from vidsrc.to but similar pattern - stricter hotlinking, works sometimes.
- **VidLink** is architecturally different (own API, different CDN structure) and is the most promising alternative for newer titles. Requires custom integration logic.
- **VidSpark/MoviesAPI** are identical twins, same issues.
- **VidCore/VidFast** are Next.js apps with different backend, currently not playable in automated tests.
- **VidNest** has extensive customization options but complex backend.
- **CineSrc** remains the most reliable of the integrated ones.

## Recommendations for the enricher
- Current enricher only adds CineSrc + VidSrc to torrent feeds. This is reasonable given reliability.
- VidLink would be worth adding if reliability becomes an issue, but needs API integration (not just simple URL building).
- Others are not drop-in replacements or unreliable.

Artifacts: `/tmp/opencode/probe/sites_review.json`, test scripts, screenshots, probe results.
