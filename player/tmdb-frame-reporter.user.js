// ==UserScript==
// @name         RSS Player: TMDb frame reporter
// @namespace    https://github.com/ibaciu6/rss-generator
// @version      1.0.0
// @description  Tells the RSS Generator player which TMDb page is open in its frame, so "Get the TMDb id" reads the page you are on now.
// @match        https://www.themoviedb.org/*
// @match        https://themoviedb.org/*
// @run-at       document-start
// @grant        none
// @downloadURL  https://raw.githubusercontent.com/ibaciu6/rss-generator/main/player/tmdb-frame-reporter.user.js
// @updateURL    https://raw.githubusercontent.com/ibaciu6/rss-generator/main/player/tmdb-frame-reporter.user.js
// ==/UserScript==
//
// Why this exists: the player embeds TMDb in an iframe, and the browser hides a
// cross-origin frame's address from the page that embeds it. Code running inside
// the frame can see its own address, so this script answers the player's question
// "where are you now?" from there. It does not use @noframes -- the frame is the
// whole point -- and it stays silent on a top-level TMDb tab.
//
// The player asks at click time (`rss-player:locate`) and this script replies
// with `location.href` as it is at that moment, so a page reached by a link, by
// history.pushState or by the back button is all the same to it: no hooks, and
// nothing that can go stale.
//
// Only the page that embeds TMDb may ask, and only from an origin listed below:
// the published player, a local server, or a file:// copy (whose origin is the
// string "null"). Serving the player from somewhere else? Add its origin here.
(function () {
    'use strict';

    if (window.top === window) return;

    const LOCATE = 'rss-player:locate';
    const LOCATION = 'rss-player:location';
    const PLAYER_ORIGIN_RE =
        /^(?:https:\/\/ibaciu6\.github\.io|null|http:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?)$/;

    window.addEventListener('message', (event) => {
        if (event.source !== window.parent) return;
        if (!PLAYER_ORIGIN_RE.test(event.origin)) return;
        const data = event.data;
        if (!data || data.type !== LOCATE) return;
        // "null" is not a valid targetOrigin, so a file:// player is answered with
        // "*"; every other player is answered at its own origin.
        window.parent.postMessage(
            { type: LOCATION, id: data.id, href: window.location.href },
            event.origin === 'null' ? '*' : event.origin
        );
    });
})();
