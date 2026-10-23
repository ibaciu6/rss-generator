# Exploration: watchtv.win player servers (Sinners 2025 / Fight Club)

Date: 2026-10-23  
Test targets:
- Fight Club (TMDB 550) - used for baseline
- Sinners (TMDB 1233413, IMDB tt31193180, 2025-04-16) - used to test recency/availability

## Servers observed on watchtv.win (from page source)
From https://www.watchtv.win/movie/sacrifice-1284186/watch and https://www.watchtv.win/movie/sinners-1233413/watch:

```json
[
  {"id":"srchsh","name":"Instaplay","movie_url":"https://media.instantplay.click/embed/movie/{id}?autoplay=1","tv_url":"https://media.instantplay.click/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"Default · Progress","supports_progress":true},
  {"id":"vidsrc_buzz","name":"VidSrc","movie_url":"https://vidsrc.buzz/embed/movie/{id}?autoplay=true","tv_url":"https://vidsrc.buzz/embed/tv/{id}/{s}/{e}?autoplay=true","quality":"Ads Multi-Sub","supports_progress":false},
  {"id":"vidcore","name":"VidCore","movie_url":"https://vidcore.io/movie/{id}?autoplay=true","tv_url":"https://vidcore.io/tv/{id}/{s}/{e}?autoplay=true","quality":"Ads · Progress","supports_progress":true},
  {"id":"vembed","name":"VEmbed","movie_url":"https://vsembed.ru/embed/movie/{id}?autoplay=1","tv_url":"https://vsembed.ru/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidnest","name":"VidNest","movie_url":"https://vidnest.fun/movie/{id}?autoplay=1","tv_url":"https://vidnest.fun/tv/{id}/{s}/{e}?autoplay=1","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidfast","name":"VidFast","movie_url":"https://vidfast.pro/movie/{id}?autoPlay=true","tv_url":"https://vidfast.pro/tv/{id}/{s}/{e}?autoPlay=true","quality":"Ads · Progress","supports_progress":true},
  {"id":"vidlink","name":"VidLink","movie_url":"https://vidlink.pro/movie/{id}?autoplay=true","tv_url":"https://vidlink.pro/tv/{id}/{s}/{e}?autoplay=true","quality":"1080p · Progress","supports_progress":true},
  {"id":"vidsrc","name":"VidSrc Pro","movie_url":"https://vidsrc.to/embed/movie/{id}","tv_url":"https://vidsrc.to/embed/tv/{id}/{s}/{e}","quality":"1080p Multi-Sub","supports_progress":false},
  {"id":"superembed","name":"SuperEmbed HD","movie_url":"https://multiembed.mov/?video_id={id}&tmdb=1","tv_url":"https://multiembed.mov/?video_id={id}&tmpm=1&s={s}&e={e}","quality":"1080p High Speed","supports_progress":false},
  {"id":"vidspark","name":"VidSpark","movie_url":"https://vidspark.to/movie/{id}?autoplay=1","tv_url":"https://vidspark.to/tv/{id}/{s}/{e}?autoplay=1","quality":"Progress","supports_progress":true},
  {"id":"peachify","name":"Peachify","movie_url":"https://peachify.top/embed/movie/{id}?autoplay=1","tv_url":"https://peachify.top/embed/tv/{id}/{s}/{e}?autoplay=1","quality":"1080p · Progress","supports_progress":true},
  {"id":"moviesapi","name":"MoviesAPI","movie_url":"https://moviesapi.to/movie/{id}?autoPlay=true","tv_url":"https://moviesapi.to/tv/{id}/{s}/{e}?autoPlay=true","quality":"1080p · Progress","supports_progress":true}
]
```

## Behavior comparison
| Server | Type | Sinners plays? | Notes |
|---|---|---|---|
| **CineSrc** (cinesrc.st, already integrated) | API-driven embed | Yes | Blob video element, gets m3u8/playlist via API. Integrated in enricher. |
| **VidSrc Pro (vidsrc.to)** | Chain (to -> vsembed.ru -> stellarconductornexus.com) | No | Uses `data.vidsrc.sh/api.php`. For Sinners hits "Session expired" on the final embed; works for some older titles (e.g. Fight Club in our tests). Heavy CF/challenges. Same family as Instaplay/VEmbed. |
| **VidSrc.buzz** | Own player | No (403) | Worked for Fight Club but blocked for Sinners in tests. Unstable. |
| **Instaplay (media.instantplay.click)** | Chain via vs_src.php | No | Calls `vs_src.php` -> redirects to `stellarconductornexus.com` embed; ends in "Session expired". Uses data.vidsrc.sh. |
| **VEmbed (vsembed.ru)** | Chain via vs_src.php | No | Same chain as above. |
| **VidLink (vidlink.pro)** | API-driven (different) | Yes | Calls `vidlink.pro/api/b/movie/{token}?multiLang=0`, gets stream with qualities/CDN URLs (e.g. surge.flooderrr.store / bcdn.hakunaymatata.com). Video element present with src, but direct CDN fetch needs proper headers/signs in some cases. Most viable alternative to CineSrc/VidSrc. |
| **VidCore (vidcore.io)** | Next.js app | No (stuck "FETCHING YOUR CONTENT...") | Different architecture, loads many chunks, makes tracking/ads calls but no playable video surfaced in tests. |
| **VidFast (vidfast.pro, redirects to vidfast.vc)** | Next.js app | No ("FETCHING, ONE MOMENT...") | Similar to VidCore. |
| **VidNest (vidnest.fun)** | Next.js app | No | Loads TMDB data, calls yflix/wyzie/subtitle APIs; no video playing. |
| **VidSpark (vidspark.to)** | SPA (same as MoviesAPI) | No | API calls to vidora return 502/403 for Sinners; iframe to cdn.vidspark.to loads but no playback. |
| **MoviesAPI (moviesapi.to)** | SPA (same as VidSpark) | No | Same family as VidSpark, API broken for this title. |
| **SuperEmbed (multiembed.mov)** | Multi-embed aggregator | No | Redirects through streamingnow.mov with Cloudflare turnstile/challenges. Not easily automatable. |
| **Peachify (peachify.top)** | Unknown | Down (523) | Origin unreachable. |

## Correct/current URLs & patterns
- CineSrc: `https://cinesrc.st/embed/movie/{tmdb_id}` (also TV with `?s={s}&e={e}`). Already integrated.
- VidSrc Pro: `https://vidsrc.to/embed/movie/{id}` or `/embed/tv/{id}/{s}/{e}` (prefers IMDb when available). Integrated; works for some titles.
- VidLink: `https://vidlink.pro/movie/{id}?autoplay=true` (movie) / `https://vidlink.pro/tv/{id}/{s}/{e}?autoplay=true` (TV). Uses its own API. Viable but different integration path.
- Others as listed above; many are unstable or use different architectures.

## Recommendation
- **VidLink** is the only other server tested that actually produces playable sources for Sinners (via API). It is architecturally different from the simple embed pattern used by CineSrc/VidSrc (requires calling `vidlink.pro/api/b/...` and handling CDN URLs with signatures/headers). Not a drop-in replacement.
- The "vidsrc.sh family" (Instaplay/VEmbed/VidSrc.to) shares `data.vidsrc.sh` API but redirects through `stellarconductornexus.com` and appears to have session/geo restrictions; unreliable for new releases.
- VidCore/VidFast/VidNest/VidSpark/MoviesAPI/SuperEmbed/Peachify are not usable in their current form for automated playback/enrichment.
- The ChatGPT suggestions have some valid names/domains, but several are down or not the same pattern. The correct URLs from watchtv.win are as shown above.

Artifacts stored in /tmp/opencode/probe/: screenshots, JSON probe results (results-*, one-*, sinners-*, cap-*, all-*.json), and test scripts.
