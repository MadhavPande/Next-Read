"use strict";

// Next Read: book recommendations that run entirely in the browser.
//
// Data: books.json (the catalogue, loaded at start) and similar.json (each
// book's closest books by reader ratings, loaded the first time a
// recommendation is needed). Both are made by build.py.
//
// Routes (hash-based, so the Back button works):
//   #/                        mood picker
//   #/mood/<mood>             top picks and shelves for a mood
//   #/mood/<mood>/<shelf>     filtered grid (shelf "all" = the whole mood)
//   #/browse                  every book, filtered grid
//   #/book/<id>-<slug>        a book: its series, similar books, same author
//   #/for-you                 recommendations from the books you loved
//   #/search/<query>          search results
//   #/about                   how it works

const view = document.getElementById("view");
const PAGE_SIZE = 24;
const LOVED_KEY = "nextread.loved";
const HIDDEN_KEY = "nextread.hidden";
const ACCLAIM_PRIOR = 50000;      // Goodreads ratings a book needs before its average counts fully
const NEW_ACCLAIM_PRIOR = 20;     // the same for Open Library, where books have far fewer ratings
const AUTHOR_CAP = 2;             // most books per author in one list of recommendations
const MOOD_COLOURS = {
  "other-worlds": "#3d4a7d", "page-turners": "#2e3037", "love-stories": "#9c3f56",
  "true-stories": "#2c6767", "timeless": "#6a5634", "young-at-heart": "#b8632a",
  "dark-eerie": "#3b2946", "feel-good": "#66761f",
};

let meta = {};
let moods = [];
let themes = {};
let books = [];
const shelves = new Map();        // shelf id -> { id, label, mood }
const seriesOpener = new Map();   // series key -> index of the series' first book
let similarPromise = null;
let loved = [];                   // indexes of loved books, oldest first
let hidden = new Set();           // indexes the reader marked "not for me"
let routeToken = 0;               // bumps on navigation so late async work can bow out

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
}

function setChildren(node, ...children) {
  node.replaceChildren();
  append(node, children);
}

const ICONS = {
  heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.3s-7.4-4.5-9.2-9.1C1.5 7.9 3.7 4.6 7.1 4.6c2 0 3.6 1.1 4.9 2.8 1.3-1.7 2.9-2.8 4.9-2.8 3.4 0 5.6 3.3 4.3 6.6-1.8 4.6-9.2 9.1-9.2 9.1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-4-4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

function icon(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.style.display = "inline-flex";
  span.innerHTML = ICONS[name];
  return span;
}

const fmtInt = (n) => Number(n).toLocaleString("en-US");

function fmtCount(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

function yearLabel(y) {
  return y == null ? "" : y < 0 ? `${-y} BC` : String(y);
}

function normalise(text) {
  return text.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/&/g, " and ").replace(/'/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

function slug(text) {
  return normalise(text).replace(/ /g, "-").slice(0, 60);
}

const bookHref = (b) => `#/book/${b.i + 1}-${slug(b.t)}`;
const byAcclaim = (a, b) => b.acclaim - a.acclaim;

function pickRandom(list) {
  return list.length ? list[Math.floor(Math.random() * list.length)] : null;
}

function shuffled(list) {
  const copy = [...list];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function setTitle(title) {
  document.title = title ? `${title} · Next Read` : "Next Read · Book recommendations";
}

function go(hash) {
  location.hash = hash;
}

// ---------------------------------------------------------------- storage

function readList(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.filter(Number.isInteger) : [];
  } catch {
    return [];
  }
}

function writeList(key, list) {
  try {
    localStorage.setItem(key, JSON.stringify(list));
  } catch {
    // Storage blocked (private mode, disabled cookies): favourites last for this visit only.
  }
}

// ---------------------------------------------------------------- data

// Popularity as a percentile (0 to 1) within each book's own source, because
// Goodreads counts (millions) and Open Library counts (dozens) aren't comparable.
function popularityPercentiles(list, value) {
  const order = [...list].sort((a, b) => value(a) - value(b));
  order.forEach((b, n) => { b.popPct = order.length > 1 ? n / (order.length - 1) : 1; });
}

function prepare() {
  for (const mood of moods) for (const s of mood.shelves) shelves.set(s.id, { ...s, mood: mood.id });
  books.forEach((b, i) => {
    b.i = i;
    b.isNew = b.src === "ol";
    b.readers = b.isNew ? b.wr + b.rd : b.c;
  });
  const older = books.filter((b) => !b.isNew);
  const newer = books.filter((b) => b.isNew);
  popularityPercentiles(older, (b) => b.c);
  popularityPercentiles(newer, (b) => b.readers);
  const mean = (list) => list.reduce((sum, b) => sum + b.r, 0) / Math.max(list.length, 1);
  const meanOld = mean(older);
  const meanNew = mean(newer.filter((b) => b.r !== null));

  books.forEach((b) => {
    b.moods = [...new Set(b.sh.map((s) => shelves.get(s)?.mood).filter(Boolean))];
    // Same series key as build.py/recommender.py, so the rules match the evaluated model.
    b.skey = b.sr ? b.sr[0].toLowerCase().replace(/^the\s+/, "") : null;
    b.snum = b.sr ? parseFloat(String(b.sr[1]).split("-")[0]) : NaN;
    // A rating's weight grows with how many people gave it; the prior differs by
    // source because Open Library books have far fewer ratings.
    if (!b.isNew) b.acclaim = (ACCLAIM_PRIOR * meanOld + b.c * b.r) / (ACCLAIM_PRIOR + b.c);
    else if (b.r !== null) b.acclaim = (NEW_ACCLAIM_PRIOR * meanNew + b.c * b.r) / (NEW_ACCLAIM_PRIOR + b.c);
    else b.acclaim = meanNew - 0.05;
    b.pop = b.nyt || b.popPct >= 0.9 ? "bestseller" : b.popPct >= 0.5 ? "popular" : "gem";
    b.titleNorm = normalise(b.t);
    b.titleWords = normalise(`${b.t} ${b.sr ? b.sr[0] : ""}`).split(" ");
    b.authorWords = normalise(b.a).split(" ");
    b.labelWords = normalise([...b.sh.map((s) => shelves.get(s)?.label ?? ""), ...b.th.map((t) => themes[t] ?? "")].join(" ")).split(" ");
  });

  // A series' opener is book 1 (or the lowest number from 1 up), ahead of
  // prequel novellas numbered below 1, and never a box set. Same as Python.
  const openerBefore = (a, b) => ((a.snum >= 1) !== (b.snum >= 1) ? a.snum >= 1 : a.snum < b.snum);
  for (const b of books) {
    if (!b.skey || b.col || !Number.isFinite(b.snum)) continue;
    const current = seriesOpener.get(b.skey);
    if (current === undefined || openerBefore(b, books[current])) seriesOpener.set(b.skey, b.i);
  }
  for (const b of books) {
    b.later = b.skey !== null && seriesOpener.has(b.skey) && seriesOpener.get(b.skey) !== b.i;
    b.kind = !b.sr ? "standalone" : b.later ? "later" : "first";
  }
}

function loadSimilar() {
  similarPromise ||= fetch("similar.json?v=468269a4e8")
    .then((r) => {
      if (!r.ok) throw new Error(`similar.json: ${r.status}`);
      return r.json();
    })
    .then((d) => ({
      k: d.k, ids: Int32Array.from(d.ids), w: Uint8Array.from(d.w),
      tagK: d.tag_k, tagIds: Int32Array.from(d.tag_ids),
    }))
    .catch((err) => {
      similarPromise = null;   // let a later visit retry
      throw err;
    });
  return similarPromise;
}

// Mirrors recommender.Ranker in Python (the version that was evaluated):
// add up the seed books' neighbour lists, give well-known books a small boost,
// point later books in a series at book 1, skip the seeds' own series and box
// sets, and allow at most two books per author.
function recommend(sim, seeds, { limit = 60, exclude = new Set() } = {}) {
  const n = books.length;
  const k = sim.k;
  const boost = meta.model?.popularity ?? 0;
  const raw = new Float64Array(n);
  const strongest = new Float64Array(n);
  const because = new Int32Array(n).fill(-1);
  for (const s of seeds) {
    if (s * k >= sim.ids.length) continue;   // newer books have no ratings-based list
    for (let j = 0; j < k; j++) {
      const nb = sim.ids[s * k + j];
      const w = sim.w[s * k + j];
      raw[nb] += w;
      if (w > strongest[nb]) {
        strongest[nb] = w;
        because[nb] = s;
      }
    }
  }

  const scores = new Float64Array(n);
  for (let i = 0; i < n; i++) if (raw[i] > 0) scores[i] = raw[i] * Math.pow(books[i].c, boost);
  for (let i = 0; i < n; i++) {
    if (books[i].later && scores[i] > 0) {
      const first = seriesOpener.get(books[i].skey);
      scores[first] += 0.5 * scores[i];
      if (because[first] < 0) because[first] = because[i];
      scores[i] = 0;
    }
  }

  const seedSet = new Set(seeds);
  const seedSeries = new Set(seeds.map((s) => books[s].skey).filter(Boolean));
  const order = [];
  for (let i = 0; i < n; i++) {
    const b = books[i];
    if (scores[i] <= 0 || b.col || seedSet.has(i) || exclude.has(i)) continue;
    if (b.skey && seedSeries.has(b.skey)) continue;
    order.push(i);
  }
  order.sort((a, b) => scores[b] - scores[a] || a - b);   // ties by book number, like Python

  const perAuthor = new Map();
  const out = [];
  for (const i of order) {
    const author = books[i].a;
    const count = perAuthor.get(author) || 0;
    if (count >= AUTHOR_CAP) continue;
    perAuthor.set(author, count + 1);
    out.push({ book: books[i], because: because[i] >= 0 ? books[because[i]] : null });
    if (out.length >= limit) break;
  }
  return out;
}

// Books readers tagged most like this one (tags only, whatever their
// popularity). Later books in a series stand in for book 1, and the book's own
// series and author are left to their own rows on the page.
function taggedAlike(sim, b, { limit = 12, skip = new Set() } = {}) {
  const out = [];
  const seen = new Set(skip);
  const perAuthor = new Map();
  for (let j = 0; j < sim.tagK && out.length < limit; j++) {
    let x = books[sim.tagIds[b.i * sim.tagK + j]];
    if (x.later) x = books[seriesOpener.get(x.skey)];
    if (x === b || x.col || seen.has(x.i) || x.a === b.a || (b.skey && x.skey === b.skey)) continue;
    const count = perAuthor.get(x.a) || 0;
    if (count >= AUTHOR_CAP) continue;
    perAuthor.set(x.a, count + 1);
    seen.add(x.i);
    out.push(x);
  }
  return out;
}

// Adds up ranked neighbour lists over several seed books, keeping the seed that
// contributed most to each suggestion (for "Because you loved …").
function blendLists(seeds, listFor, { limit, exclude = new Set(), skipSeries = false }) {
  const scores = new Map();
  const because = new Map();
  for (const s of seeds) {
    for (const [i, w] of listFor(s)) {
      let x = books[i];
      if (x.later) x = books[seriesOpener.get(x.skey)];   // point at book 1 of a series
      scores.set(x.i, (scores.get(x.i) || 0) + w);
      if (!because.has(x.i) || w > because.get(x.i)[1]) because.set(x.i, [s, w]);
    }
  }
  const seedSet = new Set(seeds);
  const seedSeries = new Set(seeds.map((s) => books[s].skey).filter(Boolean));
  const order = [...scores.keys()]
    .filter((i) => !seedSet.has(i) && !exclude.has(i) && !books[i].col
      && !(skipSeries && books[i].skey && seedSeries.has(books[i].skey)))
    .sort((a, b) => scores.get(b) - scores.get(a) || a - b);
  const perAuthor = new Map();
  const out = [];
  for (const i of order) {
    const author = books[i].a;
    const count = perAuthor.get(author) || 0;
    if (count >= AUTHOR_CAP) continue;
    perAuthor.set(author, count + 1);
    out.push({ book: books[i], because: books[because.get(i)[0]] });
    if (out.length >= limit) break;
  }
  return out;
}

// Books alike by genre and themes, for readers whose favourites are all newer
// books (which have no ratings-based lists).
function genreAlike(sim, seeds, { limit = 60, exclude = new Set() } = {}) {
  return blendLists(seeds, function* (s) {
    for (let j = 0; j < sim.tagK; j++) yield [sim.tagIds[s * sim.tagK + j], sim.tagK - j];
  }, { limit, exclude, skipSeries: true });
}

// Where to read more about a book: its page on Goodreads, Open Library and
// Google Books (a direct page when we know its ID, a search otherwise).
function sourceLinks(b) {
  const q = encodeURIComponent(`${b.t} ${b.a}`);
  return [
    ["Goodreads", b.gr ? `https://www.goodreads.com/book/show/${b.gr}` : `https://www.goodreads.com/search?q=${b.isbn || q}`],
    ["Open Library", b.ol ? `https://openlibrary.org/works/${b.ol}`
      : b.isbn ? `https://openlibrary.org/isbn/${b.isbn}` : `https://openlibrary.org/search?q=${q}`],
    ["Google Books", b.isbn ? `https://books.google.com/books?vid=ISBN${b.isbn}` : `https://www.google.com/search?tbm=bks&q=${q}`],
  ];
}

function searchBooks(query, limit = 50) {
  const q = normalise(query);
  if (!q) return [];
  const tokens = q.split(" ");
  const found = [];
  for (const b of books) {
    let score = 0;
    let ok = true;
    for (let t = 0; t < tokens.length; t++) {
      const tok = tokens[t];
      const last = t === tokens.length - 1;
      const match = (w) => (last || tok.length > 3 ? w.startsWith(tok) : w === tok);
      if (b.titleWords.some(match)) score += 3;
      else if (b.authorWords.some(match)) score += 2;
      else if (b.labelWords.some(match)) score += 1;
      else { ok = false; break; }
    }
    if (!ok) continue;
    if (b.titleNorm === q) score += 10;
    else if (b.titleNorm.startsWith(q)) score += 4;
    found.push([score + b.popPct, b]);
  }
  found.sort((x, y) => y[0] - x[0]);
  return found.slice(0, limit).map((f) => f[1]);
}

// ---------------------------------------------------------------- loved books

const isLoved = (i) => loved.includes(i);

function setLoved(i, on) {
  const was = isLoved(i);
  if (on === was) return;
  loved = on ? [...loved, i] : loved.filter((x) => x !== i);
  if (on) hidden.delete(i);
  writeList(LOVED_KEY, loved);
  writeList(HIDDEN_KEY, [...hidden]);
  document.querySelectorAll(`[data-love="${i}"]`).forEach(updateLoveButton);
  updateLovedCount();
  if (currentPage() === "for-you") rerenderKeepingScroll(renderForYou);
}

function updateLovedCount() {
  const badge = document.getElementById("loved-count");
  badge.textContent = loved.length;
  badge.hidden = loved.length === 0;
}

function loveButton(b, { large = false } = {}) {
  const button = el("button", {
    class: large ? "btn" : "love",
    type: "button",
    "data-love": b.i,
    "data-large": large ? "1" : null,
    onclick: (e) => {
      e.preventDefault();
      setLoved(b.i, !isLoved(b.i));
    },
  });
  updateLoveButton(button);
  return button;
}

function updateLoveButton(button) {
  const i = Number(button.dataset.love);
  const on = isLoved(i);
  button.setAttribute("aria-pressed", String(on));
  const title = books[i].t;
  if (button.dataset.large) {
    setChildren(button, icon("heart"), on ? "You loved this" : "I loved this");
    button.setAttribute("aria-label", on ? `Remove ${title} from books you loved` : `Add ${title} to books you loved`);
  } else {
    setChildren(button, icon("heart"));
    button.setAttribute("aria-label", on ? `Remove ${title} from books you loved` : `I loved ${title}`);
    button.title = on ? "You loved this" : "I loved this";
  }
}

// ---------------------------------------------------------------- building blocks

function coverUrl(b, size) {
  if (!b.img) return null;
  if (b.img[0] === "g") {
    const [stamp, id] = b.img.slice(1).split("/");
    return `https://images.gr-assets.com/books/${stamp}l/${id}.jpg`;
  }
  return `https://covers.openlibrary.org/b/id/${b.img.slice(1)}-${size === "large" ? "L" : "M"}.jpg`;
}

function fallbackCover(b) {
  let hue = 0;
  for (const ch of b.t) hue = (hue * 31 + ch.charCodeAt(0)) % 360;
  return el("div", { class: "fallback", style: `--hue:${hue}`, role: "img", "aria-label": `${b.t} by ${b.a}` },
    el("span", { class: "fb-title" }, b.t),
    el("span", { class: "fb-author" }, b.a));
}

function cover(b, size = "medium") {
  const url = coverUrl(b, size);
  if (!url) return fallbackCover(b);
  const img = el("img", { class: "cover", src: url, alt: "", loading: "lazy", decoding: "async" });
  img.addEventListener("error", () => img.replaceWith(fallbackCover(b)), { once: true });
  return img;
}

function bookCard(b, { kicker, reason, onHide, badge = true } = {}) {
  const label = kicker ?? (b.sh[0] ? shelves.get(b.sh[0])?.label : null);
  return el("article", { class: "card" },
    el("a", { class: "card-link", href: bookHref(b) },
      el("div", { class: "card-cover" }, cover(b), badge && b.isNew ? el("span", { class: "badge-new" }, "New") : null),
      label ? el("span", { class: "card-kicker" }, label) : null,
      el("span", { class: "card-title" }, b.t),
      el("span", { class: "card-author" }, b.a),
      b.sr ? el("span", { class: "card-series" }, `${b.sr[0]} #${b.sr[1]}`) : null,
      el("span", { class: "card-meta" }, b.r !== null ? `★ ${b.r.toFixed(2)}` : "Not yet rated",
        b.y != null ? ` · ${yearLabel(b.y)}` : ""),
      reason ? el("span", { class: "card-reason" }, reason) : null),
    loveButton(b),
    onHide ? el("button", {
      class: "hide", type: "button", title: "Not for me",
      "aria-label": `Not for me: hide ${b.t}`, onclick: () => onHide(b),
    }, "✕") : null);
}

function bookRow(list, options = () => ({})) {
  return el("div", { class: "row" }, list.map((b) => bookCard(b, options(b))));
}

function crumbs(items) {
  const parts = [];
  items.forEach(([label, href], n) => {
    if (n) parts.push(el("span", { "aria-hidden": "true" }, "/"));
    parts.push(href ? el("a", { href }, label) : el("span", { "aria-current": "page" }, label));
  });
  return el("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, parts);
}

function pageHead(title, text) {
  return el("div", { class: "page-head" }, el("h1", {}, title), text ? el("p", {}, text) : null);
}

function section(title, text, ...content) {
  return el("section", { class: "section" }, sectionHead(title, text), content);
}

function sectionHead(title, text, action = null) {
  return el("div", { class: "section-head" },
    el("div", {}, el("h2", {}, title), text ? el("p", {}, text) : null),
    action);
}

function fanCovers(pool) {
  const candidates = pool.filter((b) => b.img && !b.later && !b.col).sort(byAcclaim).slice(0, 15);
  return shuffled(candidates).slice(0, 3);
}

function tile({ href, title, text, count, colour, covers, small = false }) {
  return el("a", { class: "tile", href, style: `--tile:${colour}` },
    el("div", { class: "fan", "aria-hidden": "true" }, covers.map((b) => cover(b))),
    el(small ? "h3" : "h2", {}, title),
    text ? el("p", {}, text) : null,
    count ? el("span", { class: "count" }, count) : null);
}

// Search box with suggestions (a combobox): used in the header and on the For you page.
let searchBoxCount = 0;

function searchBox({ placeholder, onPick, onSubmit }) {
  const listId = `suggestions-${++searchBoxCount}`;
  const input = el("input", {
    type: "search", placeholder, "aria-label": placeholder, autocomplete: "off", spellcheck: "false",
    role: "combobox", "aria-expanded": "false", "aria-controls": listId, "aria-autocomplete": "list",
  });
  const list = el("ul", { class: "suggestions", id: listId, role: "listbox", hidden: true });
  let items = [];
  let active = -1;
  let timer = null;

  const close = () => {
    list.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };
  const pick = (b) => {
    close();
    onPick(b, input);
  };
  const submitAll = () => {
    const q = input.value.trim();
    close();
    if (q && onSubmit) onSubmit(q, input);
  };

  function render() {
    if (!items.length || !input.value.trim()) return close();
    setChildren(list,
      items.map((b, n) => el("li", {
        class: "suggestion", role: "option", id: `${listId}-${n}`, "aria-selected": String(n === active),
        onmousedown: (e) => { e.preventDefault(); pick(b); },
      },
      cover(b),
      el("span", {},
        el("span", { class: "suggestion-title" }, b.t),
        el("span", { class: "suggestion-meta" }, `${b.a}${b.y != null ? ` · ${yearLabel(b.y)}` : ""}`)))),
      onSubmit ? el("li", {
        class: "suggestion-all", role: "option",
        onmousedown: (e) => { e.preventDefault(); submitAll(); },
      }, `See all results for “${input.value.trim()}”`) : null);
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) input.setAttribute("aria-activedescendant", `${listId}-${active}`);
    else input.removeAttribute("aria-activedescendant");
  }

  const refresh = () => {
    items = searchBooks(input.value, 7);
    active = -1;
    render();
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 60);
  });
  input.addEventListener("focus", () => { if (input.value.trim()) refresh(); });
  input.addEventListener("blur", () => setTimeout(close, 150));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      active = (active + 1) % items.length;
      render();
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      active = active <= 0 ? items.length - 1 : active - 1;
      render();
    } else if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(timer);
      if (!input.value.trim()) return;
      if (active >= 0 && items[active]) pick(items[active]);
      else if (onSubmit) submitAll();
      else {
        const first = searchBooks(input.value, 1)[0];
        if (first) pick(first);
      }
    } else if (e.key === "Escape") {
      close();
    }
  });

  return el("div", { class: "searchbox" }, el("label", { class: "searchbox-field" }, icon("search"), input), list);
}

// ---------------------------------------------------------------- filtered grid

const ERAS = [
  { id: "pre1900", label: "Before 1900", test: (y) => y < 1900 },
  { id: "1900", label: "1900–1959", test: (y) => y >= 1900 && y < 1960 },
  { id: "1960", label: "1960–1999", test: (y) => y >= 1960 && y < 2000 },
  { id: "2000", label: "2000–2009", test: (y) => y >= 2000 && y < 2010 },
  { id: "2010", label: "2010–2017", test: (y) => y >= 2010 && y < 2018 },
  { id: "2018", label: "2018 onwards", test: (y) => y >= 2018 },
];
const RATINGS = [
  { id: "45", label: "★ 4.5 and up", test: (r) => r >= 4.5 },
  { id: "40", label: "★ 4.0–4.49", test: (r) => r >= 4 && r < 4.5 },
  { id: "35", label: "★ 3.5–3.99", test: (r) => r >= 3.5 && r < 4 },
  { id: "low", label: "Under ★ 3.5", test: (r) => r < 3.5 },
];
const POPULARITY = [
  { id: "bestseller", label: "Bestsellers" },
  { id: "popular", label: "Well known" },
  { id: "gem", label: "Hidden gems" },
];
const SERIES_KINDS = [
  { id: "standalone", label: "Standalone" },
  { id: "first", label: "First in a series" },
  { id: "later", label: "Later in a series" },
];
const FACETS = [
  { key: "shelf", label: "Genre", values: (b) => b.sh, name: (id) => shelves.get(id)?.label ?? id, limit: 10 },
  { key: "theme", label: "Themes", values: (b) => b.th, name: (id) => themes[id] ?? id, limit: 10 },
  { key: "era", label: "Published", values: (b) => (b.y == null ? [] : ERAS.filter((e) => e.test(b.y)).map((e) => e.id)), fixed: ERAS },
  { key: "rating", label: "Rating", values: (b) => (b.r === null ? [] : RATINGS.filter((r) => r.test(b.r)).map((r) => r.id)), fixed: RATINGS },
  { key: "pop", label: "Popularity", values: (b) => [b.pop], fixed: POPULARITY },
  { key: "series", label: "Series", values: (b) => [b.kind], fixed: SERIES_KINDS },
];
// Well rated but less read, within each book's own source.
const gemScore = (b) => (b.r === null ? -Infinity : b.r - 0.5 * b.popPct);
const SORTS = {
  match: { label: "Best match" },
  acclaimed: { label: "Most acclaimed", fn: (a, b) => b.acclaim - a.acclaim },
  rating: { label: "Highest rated", fn: (a, b) => (b.r ?? 0) - (a.r ?? 0) || b.popPct - a.popPct },
  popular: { label: "Most popular", fn: (a, b) => b.popPct - a.popPct },
  gems: { label: "Hidden gems first", fn: (a, b) => gemScore(b) - gemScore(a) },
  newest: { label: "Newest", fn: (a, b) => (b.y ?? -1e9) - (a.y ?? -1e9) },
  oldest: { label: "Oldest", fn: (a, b) => (a.y ?? 1e9) - (b.y ?? 1e9) },
};

function bookGrid({ base, sorts, preset = {}, reasonFor = null, onHide = null }) {
  let items = [...base];
  const state = {
    selected: Object.fromEntries(FACETS.map((f) => [f.key, new Set(preset[f.key] || [])])),
    sort: sorts[0],
    shown: PAGE_SIZE,
    expanded: new Set(),
  };

  const matches = (b, skipKey) => {
    for (const f of FACETS) {
      if (f.key === skipKey) continue;
      const chosen = state.selected[f.key];
      if (chosen.size && !f.values(b).some((v) => chosen.has(v))) return false;
    }
    return true;
  };

  const filtersEl = el("aside", { class: "filters", "aria-label": "Filters" });
  const countEl = el("span", { class: "result-count", "aria-live": "polite" });
  const gridEl = el("div", { class: "grid" });
  const moreEl = el("div", { class: "more" });
  const toggle = el("button", {
    class: "btn filters-toggle", type: "button", "aria-expanded": "false",
    onclick: () => toggle.setAttribute("aria-expanded", String(filtersEl.classList.toggle("open"))),
  }, "Filters");
  const sortSelect = el("select", {
    "aria-label": "Sort by",
    onchange: (e) => { state.sort = e.target.value; state.shown = PAGE_SIZE; update(); },
  }, sorts.map((s) => el("option", { value: s }, SORTS[s].label)));

  function facetSection(f) {
    // Counts respect every other active filter, so no option leads to zero books.
    const counts = new Map();
    const all = new Set();
    for (const b of items) {
      const values = f.values(b);
      values.forEach((v) => all.add(v));
      if (!matches(b, f.key)) continue;
      for (const v of new Set(values)) counts.set(v, (counts.get(v) || 0) + 1);
    }
    let options = f.fixed ? f.fixed.map((o) => o.id).filter((id) => all.has(id)) : [...all];
    if (options.length < 2) return null;
    if (!f.fixed) options.sort((a, b) => (counts.get(b) || 0) - (counts.get(a) || 0) || String(a).localeCompare(String(b)));

    const chosen = state.selected[f.key];
    const truncated = f.limit && options.length > f.limit && !state.expanded.has(f.key);
    const visible = truncated ? options.filter((o, n) => n < f.limit || chosen.has(o)) : options;
    const name = (id) => (f.fixed ? f.fixed.find((o) => o.id === id).label : f.name(id));

    return el("div", { class: "facet" },
      el("h3", {}, f.label),
      el("div", { class: "options" }, visible.map((id) => {
        const count = counts.get(id) || 0;
        return el("button", {
          class: "option", type: "button", "aria-pressed": String(chosen.has(id)),
          disabled: !count && !chosen.has(id),
          onclick: () => {
            if (chosen.has(id)) chosen.delete(id);
            else chosen.add(id);
            state.shown = PAGE_SIZE;
            update();
          },
        }, name(id), el("span", { class: "n" }, fmtInt(count)));
      })),
      truncated ? el("button", {
        class: "link-btn", type: "button", style: "margin-top:10px",
        onclick: () => { state.expanded.add(f.key); update(); },
      }, `Show all ${options.length}`) : null);
  }

  function update() {
    const results = items.filter((b) => matches(b));
    const sortFn = SORTS[state.sort].fn;
    if (sortFn) results.sort(sortFn);
    const active = FACETS.some((f) => state.selected[f.key].size);
    setChildren(filtersEl,
      el("div", { class: "filters-head" },
        el("h2", {}, "Filters"),
        active ? el("button", {
          class: "link-btn", type: "button",
          onclick: () => { FACETS.forEach((f) => state.selected[f.key].clear()); state.shown = PAGE_SIZE; update(); },
        }, "Clear all") : null),
      FACETS.map(facetSection));
    countEl.textContent = `${fmtInt(results.length)} ${results.length === 1 ? "book" : "books"}`;
    setChildren(gridEl, results.length
      ? results.slice(0, state.shown).map((b) => bookCard(b, { reason: reasonFor?.(b), onHide: onHide && ((x) => onHide(x)) }))
      : el("p", { class: "empty" }, "No books match these filters. Try removing one."));
    setChildren(moreEl, results.length > state.shown
      ? el("button", {
        class: "btn", type: "button",
        onclick: () => { state.shown += PAGE_SIZE; update(); },
      }, `Show more (${fmtInt(results.length - state.shown)} left)`)
      : null);
  }

  const element = el("div", { class: "browse" },
    filtersEl,
    el("div", {}, el("div", { class: "toolbar" }, countEl, toggle, sortSelect), gridEl, moreEl));
  update();
  return {
    element,
    remove(b) {
      items = items.filter((x) => x !== b);
      update();
    },
  };
}

// ---------------------------------------------------------------- pages

function renderHome() {
  setTitle();
  const tiles = moods.map((m) => {
    const pool = books.filter((b) => b.moods.includes(m.id) && !b.col);
    return tile({
      href: `#/mood/${m.id}`, title: m.label, text: m.blurb, count: `${fmtInt(pool.length)} books`,
      colour: MOOD_COLOURS[m.id], covers: fanCovers(pool),
    });
  });
  setChildren(view,
    pageHead("What are you in the mood for?",
      `Pick a mood, start from a book you loved, or tell us a few favourites. Picks come from ${fmtCount(meta.ratings)} ratings by ${fmtInt(meta.readers)} readers, plus ${fmtInt(meta.sources?.openlibrary_books ?? 0)} newer books from Open Library.`),
    el("div", { class: "tiles" }, tiles),
    el("div", { class: "start-options" },
      el("section", { class: "start-card" },
        el("h2", {}, "Loved a book?"),
        el("p", {}, "Find it, and see what the readers who loved it loved next."),
        searchBox({ placeholder: "Search a book you loved", onPick: (b) => go(bookHref(b)), onSubmit: (q) => go(`#/search/${encodeURIComponent(q)}`) })),
      el("section", { class: "start-card" },
        el("h2", {}, "Picks just for you"),
        el("p", {}, "Tap ♡ on 3–5 books you've loved and we'll blend them into recommendations."),
        el("a", { class: "btn primary", href: "#/for-you" }, loved.length ? `See your picks (${loved.length} loved)` : "Choose your favourites")),
      el("section", { class: "start-card" },
        el("h2", {}, "Just browsing?"),
        el("p", {}, `Filter all ${fmtInt(books.length)} books by genre, theme, era and rating.`),
        el("a", { class: "btn", href: "#/browse" }, "Browse every book"))));
}

function renderMood(moodId) {
  const mood = moods.find((m) => m.id === moodId);
  if (!mood) return go("#/");
  setTitle(mood.label);
  const colour = MOOD_COLOURS[mood.id];
  const pool = books.filter((b) => b.moods.includes(mood.id) && !b.col);
  const byShelf = mood.shelves
    .map((s) => ({ shelf: s, items: pool.filter((b) => b.sh.includes(s.id)) }))
    .filter((x) => x.items.length);

  // One acclaimed book (a standalone or a series opener) from each shelf. Newer
  // books need 20+ Open Library ratings to count as acclaimed.
  const picksRow = el("div", { class: "row" });
  const shuffle = () => {
    const used = new Set();
    const picks = [];
    for (const { shelf, items } of byShelf) {
      const top = items.filter((b) => !b.later && b.img && !used.has(b.i) && (!b.isNew || b.c >= 20))
        .sort(byAcclaim).slice(0, 25);
      const pick = pickRandom(top);
      if (!pick) continue;
      used.add(pick.i);
      picks.push(bookCard(pick, { kicker: shelf.label }));
    }
    setChildren(picksRow, picks);
  };
  shuffle();

  const tiles = byShelf.map(({ shelf, items }) => tile({
    href: `#/mood/${mood.id}/${shelf.id}`, title: shelf.label, text: `${fmtInt(items.length)} books`,
    colour, covers: fanCovers(items), small: true,
  }));
  tiles.push(tile({
    href: `#/mood/${mood.id}/all`, title: `All ${mood.label.toLowerCase()}`, text: `${fmtInt(pool.length)} books`,
    colour, covers: fanCovers(pool), small: true,
  }));

  setChildren(view,
    crumbs([["Moods", "#/"], [mood.label]]),
    pageHead(mood.label, mood.blurb),
    el("section", { class: "section" },
      sectionHead("Top picks", "One acclaimed book from each shelf.",
        el("button", { class: "link-btn", type: "button", onclick: shuffle }, "Shuffle ↻")),
      picksRow),
    section("Shelves", null, el("div", { class: "tiles small" }, tiles)));
}

function renderShelf(moodId, shelfId) {
  const mood = moods.find((m) => m.id === moodId);
  const shelf = shelfId === "all" ? null : shelves.get(shelfId);
  if (!mood || (shelfId !== "all" && (!shelf || shelf.mood !== moodId))) return go("#/");
  const title = shelf ? shelf.label : `All ${mood.label.toLowerCase()}`;
  setTitle(title);
  const base = books.filter((b) => !b.col && (shelf ? b.sh.includes(shelf.id) : b.moods.includes(mood.id)));
  setChildren(view,
    crumbs([["Moods", "#/"], [mood.label, `#/mood/${mood.id}`], [title]]),
    pageHead(title, `${shelf ? mood.label : mood.blurb}. Later books in a series are hidden until you pick them under Series.`),
    bookGrid({
      base, sorts: ["acclaimed", "rating", "popular", "gems", "newest", "oldest"],
      preset: { series: ["standalone", "first"] },
    }).element);
}

function renderBrowse() {
  setTitle("Browse");
  const base = books.filter((b) => !b.col);
  setChildren(view,
    crumbs([["Moods", "#/"], ["Browse"]]),
    pageHead("Every book", `${fmtInt(base.length)} books, from Homer to ${Math.max(...base.map((b) => b.y ?? 0))}. Later books in a series are hidden until you pick them under Series.`),
    bookGrid({
      base, sorts: ["acclaimed", "rating", "popular", "gems", "newest", "oldest"],
      preset: { series: ["standalone", "first"] },
    }).element);
}

function sharedReason(a, b) {
  const shared = [
    ...a.th.filter((t) => b.th.includes(t)).map((t) => themes[t]),
    ...a.sh.filter((s) => b.sh.includes(s)).map((s) => shelves.get(s).label),
  ];
  return shared.length ? `Also: ${shared.slice(0, 2).join(" · ")}` : null;
}

async function renderBook(index) {
  const b = books[index];
  if (!b) return go("#/");
  const token = routeToken;
  setTitle(b.t);
  const mood = b.moods[0] ? moods.find((m) => m.id === b.moods[0]) : null;
  const inSeries = b.skey
    ? books.filter((x) => x.skey === b.skey && x !== b && !x.col).sort((x, y) => (x.snum || 0) - (y.snum || 0))
    : [];
  const byAuthor = books
    .filter((x) => x.a === b.a && x !== b && !x.col && (!b.skey || x.skey !== b.skey))
    .sort((x, y) => (y.y ?? 0) - (x.y ?? 0))
    .slice(0, 12);
  const similarSlot = el("div", {}, el("p", { class: "status" }, "Finding what the same readers loved…"));
  const alikeSection = el("section", { class: "section", hidden: true });

  const rating = b.r !== null
    ? el("span", {}, el("strong", {}, `★ ${b.r.toFixed(2)}`),
      b.isNew ? ` from ${fmtInt(b.c)} Open Library ratings` : ` from ${fmtCount(b.c)} Goodreads ratings`)
    : el("span", {}, "Not enough Open Library ratings yet");
  const source = b.isNew
    ? `Details and numbers from Open Library, ${monthYear(meta.sources?.openlibrary_fetched)}.`
    : "Details and numbers from Goodreads, as of 2017 (the goodbooks-10k dataset).";

  setChildren(view,
    crumbs([["Moods", "#/"], mood ? [mood.label, `#/mood/${mood.id}`] : null, [b.t]].filter(Boolean)),
    el("article", { class: "book" },
      el("div", { class: "book-cover" }, cover(b, "large")),
      el("div", {},
        b.sh[0] ? el("span", { class: "card-kicker" }, shelves.get(b.sh[0]).label) : null,
        el("h1", {}, b.t),
        el("p", { class: "book-by" }, "by ", el("strong", {}, b.a)),
        b.sr ? el("p", { class: "book-series" }, `Book ${b.sr[1]} of ${b.sr[0]}`) : null,
        el("div", { class: "book-stats" },
          rating,
          b.isNew ? el("span", {}, `${fmtInt(b.wr)} want to read · ${fmtInt(b.rd)} have read`) : null,
          b.y != null ? el("span", {}, `Published ${yearLabel(b.y)}`) : null,
          el("span", {}, b.nyt ? "New York Times bestseller" : POPULARITY.find((p) => p.id === b.pop).label.replace(/s$/, ""))),
        el("p", { class: "book-source" }, source),
        el("div", { class: "chips" },
          b.sh.map((s) => el("a", { class: "chip", href: `#/mood/${shelves.get(s).mood}/${s}` }, shelves.get(s).label)),
          b.th.map((t) => el("span", { class: "chip theme" }, themes[t]))),
        el("div", { class: "book-actions" },
          loveButton(b, { large: true }),
          sourceLinks(b).map(([name, href]) => el("a", { class: "btn", href, target: "_blank", rel: "noopener" }, `${name} ↗`))))),
    b.isNew ? el("p", { class: "callout" },
      "This is a newer book. Open Library doesn't share which readers read what, which “readers who loved this also loved” needs, so the suggestions below are by genre, themes and author.") : null,
    inSeries.length ? section(`More in ${b.sr[0]}`, null, bookRow(inSeries, () => ({ kicker: null }))) : null,
    b.isNew ? null : el("section", { class: "section" },
      sectionHead("Readers who loved this also loved", "From 6 million ratings: what the same readers rated 4 or 5 stars."),
      similarSlot),
    alikeSection,
    byAuthor.length ? section(`More by ${b.a}`, null, bookRow(byAuthor)) : null);

  let sim;
  try {
    sim = await loadSimilar();
  } catch {
    if (token === routeToken) setChildren(similarSlot, el("p", { class: "status" }, "Couldn't load recommendations. Please refresh the page."));
    return;
  }
  if (token !== routeToken) return;
  const shown = new Set();
  if (!b.isNew) {
    const recs = recommend(sim, [b.i], { limit: 12 });
    recs.forEach((r) => shown.add(r.book.i));
    setChildren(similarSlot, el("div", { class: "grid" }, recs.map((r) => bookCard(r.book, { reason: sharedReason(b, r.book) }))));
  }

  const alike = taggedAlike(sim, b, { skip: shown });
  alike.forEach((x) => shown.add(x.i));
  if (alike.length) {
    setChildren(alikeSection,
      sectionHead("Same genre and themes", b.isNew
        ? "Books of any year that share its genres and themes."
        : "Books readers tagged most like this one, bestsellers or not."),
      bookRow(alike, (x) => ({ reason: sharedReason(b, x) })));
    alikeSection.hidden = false;
  }
}

function monthYear(isoDate) {
  if (!isoDate) return "recently";
  const [y, m] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
}

function nextInSeries() {
  const out = [];
  const seen = new Set();
  for (const i of loved) {
    const b = books[i];
    if (!b.skey || seen.has(b.skey)) continue;
    seen.add(b.skey);
    const read = loved.map((j) => books[j]).filter((x) => x.skey === b.skey && Number.isFinite(x.snum));
    const furthest = Math.max(...read.map((x) => x.snum), -Infinity);
    const next = books
      .filter((x) => x.skey === b.skey && !x.col && Number.isFinite(x.snum) && x.snum > furthest && !isLoved(x.i) && !hidden.has(x.i))
      .sort((x, y) => x.snum - y.snum)[0];
    if (next) out.push(next);
  }
  return out;
}

function starterBooks() {
  const picks = [];
  const used = new Set();
  // The three most-rated older books and the most-read newer book in each mood.
  for (const m of moods) {
    const fits = (b) => b.moods.includes(m.id) && !b.later && !b.col && b.img && !used.has(b.i);
    const top = [
      ...books.filter((b) => !b.isNew && fits(b)).sort((a, b) => b.c - a.c).slice(0, 3),
      ...books.filter((b) => b.isNew && fits(b)).sort((a, b) => b.readers - a.readers).slice(0, 1),
    ];
    top.forEach((b) => used.add(b.i));
    picks.push(...top);
  }
  return picks;
}

async function renderForYou() {
  const token = routeToken;
  setTitle("For you");
  const lovedBooks = loved.map((i) => books[i]);
  const addBox = searchBox({
    placeholder: "Add a book you loved",
    onPick: (b, input) => { input.value = ""; setLoved(b.i, true); },
  });
  const lovedSection = el("section", { class: "section", style: "margin-top:0" },
    sectionHead(lovedBooks.length ? `Books you loved (${lovedBooks.length})` : "Books you loved",
      "Saved in this browser only."),
    lovedBooks.length ? el("ul", { class: "loved-list", style: "list-style:none;padding:0" }, lovedBooks.map((b) =>
      el("li", { class: "loved-item" },
        cover(b),
        el("a", { href: bookHref(b), title: b.t }, b.t),
        el("button", { type: "button", "aria-label": `Remove ${b.t}`, title: "Remove", onclick: () => setLoved(b.i, false) }, "✕")))) : null,
    el("div", { class: "add-box" }, addBox),
    hidden.size ? el("p", { style: "font-size:13px;color:var(--muted);margin-top:10px" },
      `${hidden.size} ${hidden.size === 1 ? "book" : "books"} hidden as not for you. `,
      el("button", {
        class: "link-btn", type: "button",
        onclick: () => { hidden.clear(); writeList(HIDDEN_KEY, []); rerenderKeepingScroll(renderForYou); },
      }, "Show them again")) : null);

  if (!lovedBooks.length) {
    setChildren(view,
      pageHead("For you", "Tell us a few books you've loved and we'll blend them into recommendations."),
      lovedSection,
      section("Not sure where to start?", "Tap ♡ on any of these you've read and loved.",
        el("div", { class: "grid" }, starterBooks().map((b) => bookCard(b)))));
    return;
  }

  const nextUp = nextInSeries();
  const byRatings = lovedBooks.some((b) => !b.isNew);   // only 2017-and-earlier books have ratings-based lists
  const recsSlot = el("div", {}, el("p", { class: "status" }, "Blending your favourites…"));
  setChildren(view,
    pageHead("For you", byRatings
      ? "Picks from readers who loved the same books as you."
      : "Your favourites are all newer books, so these picks are matched by genre, themes and author."),
    lovedSection,
    lovedBooks.length < 3 ? el("p", { class: "callout" }, "Add a couple more books you loved for sharper picks.") : null,
    nextUp.length ? section("Next in your series", "The next book after the ones you loved.", bookRow(nextUp)) : null,
    el("section", { class: "section" }, sectionHead("Recommended for you", "Hover a book and press ✕ if it's not for you."), recsSlot));

  let sim;
  try {
    sim = await loadSimilar();
  } catch {
    if (token === routeToken) setChildren(recsSlot, el("p", { class: "status" }, "Couldn't load recommendations. Please refresh the page."));
    return;
  }
  if (token !== routeToken) return;
  const recs = byRatings
    ? recommend(sim, loved, { limit: 150, exclude: hidden })
    : genreAlike(sim, loved, { limit: 150, exclude: hidden });
  const reasons = new Map(recs.map((r) => [r.book.i, r.because ? `Because you loved ${r.because.t}` : null]));

  const grid = bookGrid({
    base: recs.map((r) => r.book),
    sorts: ["match", "acclaimed", "rating", "popular", "newest"],
    reasonFor: (b) => reasons.get(b.i),
    onHide: (b) => {
      hidden.add(b.i);
      writeList(HIDDEN_KEY, [...hidden]);
      grid.remove(b);
    },
  });
  setChildren(recsSlot, grid.element);
}

function renderSearch(query) {
  setTitle(`“${query}”`);
  const results = searchBooks(query, 300);
  setChildren(view,
    crumbs([["Moods", "#/"], ["Search"]]),
    pageHead(`“${query}”`, results.length
      ? `${fmtInt(results.length)} ${results.length === 1 ? "book" : "books"} matching titles, authors, genres and themes.`
      : "No books match. Try a title, an author, or a theme like “dragons”."),
    results.length ? bookGrid({ base: results, sorts: ["match", "acclaimed", "rating", "popular", "newest", "oldest"] }).element : null);
}

function renderAbout() {
  setTitle("How it works");
  const ev = meta.evaluation;
  const pct = (x) => `${Math.round(x * 100)}%`;
  setChildren(view,
    crumbs([["Moods", "#/"], ["How it works"]]),
    el("div", { class: "prose" },
      pageHead("How Next Read works", "Recommendations from real readers' ratings, computed ahead of time so everything runs instantly in your browser."),
      el("h2", {}, "Three ways in"),
      el("ul", {},
        el("li", {}, el("strong", {}, "Moods and shelves "), "come from how readers tagged each book on Goodreads (“epic-fantasy”, “cozy-mystery”, “made-me-cry”), with personal shelves like “to-read” filtered out."),
        el("li", {}, el("strong", {}, "Books like this one: "), "for every book, we found the books most often loved (4–5 stars) by the same readers, across 6 million ratings from 53,000 readers, blended 70/30 with how alike readers tagged them. A second row shows the books tagged most alike, bestsellers or not."),
        el("li", {}, el("strong", {}, "For you "), "adds up those lists for each book you loved. Later books in a series point you to book 1, books from series you've read go in their own row, and no author gets more than two spots.")),
      ev ? [
        el("h2", {}, "How accurate is it?"),
        el("p", {}, `We hid ${fmtInt(ev.readers)} readers from the model, gave it 5 books each of them loved, and checked its top 10 picks against the other books they loved.`),
        el("div", { class: "stats" },
          el("div", { class: "stat" }, el("strong", {}, `${(ev.precision * 10).toFixed(1)} of 10`), el("span", {}, "picks were books the reader really loved")),
          el("div", { class: "stat" }, el("strong", {}, pct(ev.hit_rate)), el("span", {}, "of readers got at least one book they loved in their top 10")),
          el("div", { class: "stat" }, el("strong", {}, `${(ev.single_precision * 10).toFixed(1)} of 10`), el("span", {}, "starting from just one book (“readers who loved this also loved”)"))),
        el("p", {}, `For comparison, suggesting the same bestsellers to everyone scores ${(ev.popular_precision * 10).toFixed(1)} and ${(ev.popular_single_precision * 10).toFixed(1)} of 10, and shows everyone the same handful of books. Next Read's picks spanned ${pct(ev.coverage)} of the catalogue.`),
      ] : null,
      el("h2", {}, "Newer books (2018 onwards)"),
      el("p", {}, `The Goodreads data stops in 2017, and Goodreads no longer shares its data. So ${fmtInt(meta.sources?.openlibrary_books ?? 0)} newer books come from `,
        el("a", { href: "https://openlibrary.org", target: "_blank", rel: "noopener" }, "Open Library"),
        `, the Internet Archive's open book catalogue (downloaded ${monthYear(meta.sources?.openlibrary_fetched)}): its most-read books first published from 2018, with their subjects, covers and Open Library readers' ratings and reading counts.`),
      el("p", {}, "Open Library doesn't share which reader read what, so newer books can't be in the “readers who loved this” lists and weren't part of the accuracy test above. They're matched by genre, themes and author instead, and marked “New”. Open Library ratings come from far fewer readers than Goodreads', so a rating is only shown once a book has at least 5."),
      el("h2", {}, "Where each number comes from"),
      el("p", {}, "Every book page says where its details come from, and links to the book on Goodreads, Open Library and Google Books, so you can check the latest ratings and reviews there."),
      el("h2", {}, "Your data"),
      el("p", {}, "The books you love are saved in this browser only. Nothing is sent anywhere."),
      el("h2", {}, "Credits"),
      el("p", {}, "Ratings, tags and details for books up to 2017: ",
        el("a", { href: "https://github.com/zygmuntz/goodbooks-10k", target: "_blank", rel: "noopener" }, "goodbooks-10k"),
        " (10,000 popular Goodreads books), licensed CC BY-SA 4.0. Newer books, and covers missing from goodbooks, from ",
        el("a", { href: "https://openlibrary.org", target: "_blank", rel: "noopener" }, "Open Library"),
        ". Next Read isn't affiliated with Goodreads or Open Library.")));
}

// ---------------------------------------------------------------- routing

function currentPage() {
  return location.hash.replace(/^#\/?/, "").split("/")[0] || "home";
}

function rerenderKeepingScroll(render) {
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
}

function route() {
  routeToken++;
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const page = parts[0] || "home";
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  window.scrollTo(0, 0);

  if (page === "mood" && parts[2]) renderShelf(parts[1], parts[2]);
  else if (page === "mood" && parts[1]) renderMood(parts[1]);
  else if (page === "book" && parts[1]) renderBook(parseInt(parts[1], 10) - 1);
  else if (page === "for-you") renderForYou();
  else if (page === "browse") renderBrowse();
  else if (page === "about") renderAbout();
  else if (page === "search" && parts[1]) renderSearch(decodeURIComponent(parts.slice(1).join("/")));
  else renderHome();
}

async function start() {
  try {
    const res = await fetch("books.json?v=eb5cbe4344");
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    ({ meta, moods, themes, books } = data);
  } catch {
    setChildren(view, el("p", { class: "status" }, "Couldn't open the library. Please refresh the page."));
    return;
  }
  prepare();
  loved = readList(LOVED_KEY).filter((i) => books[i]);
  hidden = new Set(readList(HIDDEN_KEY).filter((i) => books[i]));
  setChildren(document.getElementById("header-search"), searchBox({
    placeholder: "Search books, authors, themes",
    onPick: (b, input) => { input.value = ""; input.blur(); go(bookHref(b)); },
    onSubmit: (q, input) => { input.blur(); go(`#/search/${encodeURIComponent(q)}`); },
  }));
  updateLovedCount();
  window.addEventListener("hashchange", route);
  route();
}

start();
