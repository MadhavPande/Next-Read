"""Newer books (published 2018 onwards) from Open Library.

goodbooks-10k stops in 2017. Open Library (openlibrary.org, run by the
Internet Archive) has an open API with newer books, their subjects, covers and
its own readers' numbers: ratings, "want to read" and "have read" counts.
Its reading data is anonymous (no way to tell which reader read what), so
these books can't join the ratings-based recommendations; they're matched to
other books by shelves and themes instead.

fetch() downloads the most-read books into data/openlibrary_new.json;
load() filters and labels them for build.py.
"""

import json
import os
import re
import time

import httpx
import numpy as np

import taxonomy

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_PATH = os.path.join(ROOT, "data", "openlibrary_new.json")
SEARCH_URL = "https://openlibrary.org/search.json"
QUERY = "first_publish_year:[2018 TO 2026] language:eng"
FIELDS = ("key,title,author_name,first_publish_year,isbn,cover_i,subject,ratings_average,ratings_count,"
          "want_to_read_count,already_read_count,currently_reading_count,readinglog_count,id_goodreads,"
          "number_of_pages_median,editions,editions.key,editions.cover_i,editions.language")
PAGES = 20                  # 1,000 books per page, most-read first
PAUSE_SECONDS = 2.0

TARGET_BOOKS = 5000         # keep this many after filtering
MIN_READERS = 12            # people who logged the book as want-to-read, reading or read
MIN_RATINGS_SHOWN = 5       # below this, an average rating isn't shown
MAX_SHELVES, MAX_THEMES = 4, 5


def fetch(client: httpx.Client, refresh: bool = False):
    """Download the most-read books first published 2018 onwards."""
    if os.path.exists(RAW_PATH) and not refresh:
        print(f"newer books already downloaded ({RAW_PATH}); pass --refresh to update")
        return
    docs = []
    for page in range(1, PAGES + 1):
        r = client.get(SEARCH_URL, params={"q": QUERY, "sort": "readinglog", "limit": 1000,
                                           "page": page, "fields": FIELDS})
        r.raise_for_status()
        batch = r.json().get("docs", [])
        docs.extend(batch)
        print(f"  Open Library page {page}/{PAGES}: {len(batch)} books")
        if not batch:
            break
        time.sleep(PAUSE_SECONDS)
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump({"fetched": time.strftime("%Y-%m-%d"), "docs": docs}, f)
    print(f"saved {len(docs)} newer books to {RAW_PATH}")


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def title_key(title: str, author: str) -> str:
    """Matches the same book across sources: the title before any subtitle, plus the author's surname."""
    main = _normalise(re.split(r"[:(]", title)[0])
    surname = _normalise(author).split(" ")[-1] if author else ""
    return f"{main}|{surname}"


_FOREIGN_WORDS = {"el", "la", "las", "del", "y", "mi", "tu", "su", "de", "un", "una", "le", "les", "des",
                  "et", "du", "der", "die", "das", "und", "ein", "eine", "il", "di", "che", "para", "por",
                  "con", "sin", "en"}
_ENGLISH_WORDS = {"the", "of", "and", "a", "an", "to", "in", "for", "with", "my", "your", "on", "at", "is"}


def looks_non_english(title: str) -> bool:
    """Open Library keeps some works under a translated title ("El viento conoce mi nombre").
    English titles with a foreign phrase ("Vampires of El Norte") are kept."""
    lower = title.lower()
    if not re.search(r"[a-z]", lower):          # no Latin letters at all ("薬屋のひとりごと")
        return True
    words = re.findall(r"[^\W\d_]+", lower)
    if any(w in _ENGLISH_WORDS for w in words):
        return False
    foreign = sum(w in _FOREIGN_WORDS for w in words)
    accented = bool(re.search(r"[áéíóúñ¿¡àèìòùçâêîôûäöß]", lower))
    return foreign >= 2 or (accented and foreign >= 1) or bool(re.search(r"\b(el|las|del|und)\b", lower))


_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on",
                "or", "the", "to", "with"}


def tidy_title(title: str) -> str:
    """Capitalise titles Open Library stores in sentence case ("Trail of lightning")."""
    words = title.split()
    shouting = sum(ch.isalpha() for ch in title) > 3 and title.upper() == title
    if shouting:                                  # "BUILDING A STORY BRAND"
        words = [w.lower() for w in words]
    elif len(words) < 2 or any(ch.isupper() for w in words[1:] for ch in w):
        return title
    return " ".join(w if n and w.lower() in _SMALL_WORDS else w[:1].upper() + w[1:] for n, w in enumerate(words))


def pick_author(names: list[str]) -> str:
    """The first author name in Latin script (Open Library also lists "陈楸帆" for Chen Qiufan)."""
    return next((n for n in names if re.search(r"[A-Za-z]", n)), names[0]).strip()


def _pick_isbn(isbns: list[str]) -> str | None:
    thirteen = [i for i in isbns if len(i) == 13 and i[:3] in ("978", "979")]
    ten = [i for i in isbns if len(i) == 10]
    return (thirteen or ten or [None])[0]


def _labels(subjects: list[str]) -> tuple[list[str], list[str]]:
    joined = [s.lower() for s in subjects]
    nyt_lists = [s[4:].split("=")[0] for s in joined if s.startswith("nyt:")]
    fiction = sum(bool(re.search(taxonomy.OL_FICTION, s)) for s in joined) + \
        2 * sum(1 for n in nyt_lists if "fiction" in n and "nonfiction" not in n)
    nonfiction = sum(bool(re.search(taxonomy.OL_NONFICTION, s)) for s in joined) + \
        2 * sum(1 for n in nyt_lists if "nonfiction" in n) > fiction

    requires = {s["id"]: s.get("requires") for s in taxonomy.ALL_SHELVES}
    shelf_hits = []
    for shelf, pattern in taxonomy.OL_SHELF_PATTERNS.items():
        hits = sum(bool(re.search(pattern, s)) for s in joined)
        hits += sum(1 for n in nyt_lists for p, sh in taxonomy.OL_NYT_SHELVES.items()
                    if sh == shelf and re.search(p, n))
        need = requires.get(shelf)
        if hits and not (need == "fiction" and nonfiction) and not (need == "nonfiction" and not nonfiction):
            shelf_hits.append((hits, shelf))
    theme_hits = [(sum(bool(re.search(p, s)) for s in joined), theme)
                  for theme, p in taxonomy.OL_THEME_PATTERNS.items()]
    # A shelf needs at least half the evidence of the book's main shelf, so one
    # stray "suspense" subject doesn't put a romance on the thriller shelf.
    ranked = sorted(shelf_hits, key=lambda x: -x[0])
    shelves = [s for hits, s in ranked if hits >= 0.5 * ranked[0][0]][:MAX_SHELVES] if ranked else []
    if not shelves and fiction and not nonfiction:
        shelves = ["literary-fiction"]      # general fiction with no clearer genre
    themes = [t for n, t in sorted(theme_hits, key=lambda x: -x[0]) if n][:MAX_THEMES]
    return shelves, themes


def load(known_keys: set[str]) -> tuple[list[dict], str]:
    """Filtered, labelled newer books as records for books.json, most-read first.

    known_keys: title_key() of books already in the catalogue, to skip duplicates.
    """
    with open(RAW_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    exclude_title = re.compile(taxonomy.OL_EXCLUDE_TITLE, re.IGNORECASE)
    exclude_subject = re.compile(taxonomy.OL_EXCLUDE_SUBJECT, re.IGNORECASE)
    seen = set(known_keys)
    records = []
    for doc in raw["docs"]:
        title, authors = doc.get("title"), doc.get("author_name")
        isbn = _pick_isbn(doc.get("isbn") or [])
        # Open Library's default cover can come from any edition (Pivot Year's is
        # Italian); the English edition's cover is used when it has one.
        english = ((doc.get("editions") or {}).get("docs") or [{}])[0]
        cover_id = english.get("cover_i") or doc.get("cover_i")
        if not title or not authors or not cover_id or not isbn:
            continue
        subjects = doc.get("subject") or []
        if exclude_title.search(title) or any(exclude_subject.search(s) for s in subjects):
            continue
        if looks_non_english(title) or not re.search(r"[A-Za-z]", pick_author(authors)):
            continue
        if (doc.get("number_of_pages_median") or 0) > 1500:      # multi-book sets
            continue
        readers = doc.get("readinglog_count") or sum(doc.get(f) or 0 for f in (
            "want_to_read_count", "already_read_count", "currently_reading_count"))
        if readers < MIN_READERS:
            continue
        key = title_key(title, authors[0])
        if key in seen:
            continue
        shelves, themes = _labels(subjects)
        if not shelves:          # can't be placed in any mood
            continue
        seen.add(key)
        ratings = doc.get("ratings_count") or 0
        record = {
            "t": tidy_title(title.strip()),
            "a": pick_author(authors),
            "y": doc.get("first_publish_year"),
            "r": round(float(doc["ratings_average"]), 2) if ratings >= MIN_RATINGS_SHOWN else None,
            "c": int(ratings),
            "wr": int(doc.get("want_to_read_count") or 0),
            "rd": int(doc.get("already_read_count") or 0),
            "img": f"o{cover_id}",
            "isbn": isbn,
            "ol": doc["key"].rsplit("/", 1)[-1],
            "sh": shelves,
            "th": themes,
            "src": "ol",
        }
        goodreads = doc.get("id_goodreads") or []
        if goodreads and str(goodreads[0]).isdigit():
            record["gr"] = int(goodreads[0])
        if any(s.startswith("nyt:") or s.lower() == "new york times bestseller" for s in subjects):
            record["nyt"] = 1
        records.append(record)
        if len(records) >= TARGET_BOOKS:
            break
    return records, raw.get("fetched", "")


def popularity_percentiles(values) -> np.ndarray:
    """Each value's rank as a fraction from 0 (least) to 1 (most)."""
    values = np.asarray(values, dtype=float)
    order = values.argsort(kind="stable")
    pct = np.empty(len(values))
    pct[order] = np.arange(len(values)) / max(len(values) - 1, 1)
    return pct
