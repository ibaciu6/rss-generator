// ==UserScript==
// @name         RSS Player: TMDb frame reporter
// @namespace    https://github.com/ibaciu6/rss-generator
// @version      1.1.0
// @description  Tells the RSS Generator player which TMDb page is open in its frame — its address and its name — so "Get the TMDb id" reads the page you are on now and the browser tab can say it too.
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
// the frame can see its own address, so this script speaks for it: it answers the
// player's question "where are you now?" (`rss-player:locate`) with its address
// and its name, and — once the player has asked it to follow (`rss-player:follow`)
// — keeps reporting both as the viewer moves through TMDb. It does not use
// @noframes — the frame is the whole point — and it stays silent on a top-level
// TMDb tab.
//
// Every message is still a statement about the page as it is when the message is
// sent, never a replay of something earlier: `location.href` and `document.title`
// are read at send time, so a page reached by a link, by `history.pushState` or by
// the back button is all the same to it. The follow side is what a tab title
// needs: navigation is announced only when the address or the name changed, and
// the name arrives in its own write, so the address is announced first and the
// name replaces it a moment later.
//
// Only the page that embeds TMDb may talk to it, and only from an origin listed
// below: the published player, a local server, or a file:// copy (whose origin is
// the string "null"). Nothing is ever broadcast — the player introduces itself
// with a question, and every answer goes back to that one origin. Serving the
// player from somewhere else? Add its origin here.
(function () {
    'use strict';

    if (window.top === window) return;

    const LOCATE = 'rss-player:locate';
    const FOLLOW = 'rss-player:follow';
    const LOCATION = 'rss-player:location';
    const PLAYER_ORIGIN_RE =
        /^(?:https:\/\/ibaciu6\.github\.io|null|http:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?)$/;

    // Where the answers go, learned from the player's own question and only from
    // an origin listed above. Before that there is nobody to tell: a TMDb page
    // embedded by some other site hears nothing from this script.
    let playerOrigin = null;
    let lastSent = '';
    let queued = false;

    function send(extra) {
        const page = {
            type: LOCATION,
            href: window.location.href,
            title: document.title || ''
        };
        for (const key in extra) page[key] = extra[key];
        // "null" is not a valid targetOrigin, so a file:// player is answered with
        // "*"; every other player is answered at its own origin.
        window.parent.postMessage(page, playerOrigin === 'null' ? '*' : playerOrigin);
        lastSent = page.href + '\n' + page.title;
    }

    // The player follows the frame, so every move it makes is worth a word — but
    // only when it changed something. TMDb writes the address and then the name,
    // which is two events for one page: the address announce carries a slug-level
    // name, and the name announce follows it.
    function announce() {
        queued = false;
        if (!playerOrigin) return;
        if (window.location.href + '\n' + (document.title || '') === lastSent) return;
        send({});
    }

    function queueAnnounce() {
        if (queued) return;
        queued = true;
        setTimeout(announce, 60);
    }

    // TMDb navigates with Turbo and history.pushState, neither of which fires
    // `load`: patching the two history methods and listening for turbo:load is
    // what makes a click inside the frame announce at all.
    ['pushState', 'replaceState'].forEach((method) => {
        const original = history[method];
        history[method] = function () {
            const result = original.apply(this, arguments);
            queueAnnounce();
            return result;
        };
    });

    window.addEventListener('popstate', queueAnnounce);
    window.addEventListener('hashchange', queueAnnounce);
    document.addEventListener('turbo:load', queueAnnounce);
    document.addEventListener('DOMContentLoaded', queueAnnounce);

    // The name arrives in its own write after the address, and watching <title>
    // is what turns that second write into a name instead of leaving the tab on
    // the slug the address carries.
    function watchTitle() {
        const title = document.querySelector('title');
        if (!title || typeof MutationObserver === 'undefined') return;
        new MutationObserver(queueAnnounce).observe(title, {
            childList: true,
            characterData: true,
            subtree: true
        });
    }

    watchTitle();
    document.addEventListener('DOMContentLoaded', watchTitle);

    window.addEventListener('message', (event) => {
        if (event.source !== window.parent) return;
        if (!PLAYER_ORIGIN_RE.test(event.origin)) return;
        const data = event.data;
        if (!data || typeof data.type !== 'string') return;
        if (data.type === LOCATE) {
            playerOrigin = event.origin;
            send({ id: data.id });
        } else if (data.type === FOLLOW) {
            playerOrigin = event.origin;
            send({});
        }
    });
})();
