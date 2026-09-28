"""Download all the data: goodbooks-10k, missing covers, and newer books.

    .venv\\Scripts\\python fetch_data.py            (skips what's already downloaded)
    .venv\\Scripts\\python fetch_data.py --refresh  (also re-downloads the newer books)

The dataset (6M Goodreads ratings for 10,000 books, CC BY-SA 4.0) comes from
https://github.com/zygmuntz/goodbooks-10k. About a third of its books have a
"no photo" placeholder instead of a cover; for those, this looks up a cover on
Open Library by ISBN (40 books per request), then by title and author.
Results are cached in data/covers.json, so re-running only fetches what's new.
Books published from 2018 on come from Open Library too (see new_books.py).
"""

import json
import os
import sys
import time

import httpx
import pandas as pd

import new_books

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(ROOT, "data", "raw")
COVERS_PATH = os.path.join(ROOT, "data", "covers.json")
DATASET_URL = "https://raw.githubusercontent.com/zygmuntz/goodbooks-10k/master/{}"
FILES = ["books.csv", "ratings.csv", "book_tags.csv", "tags.csv"]

OPEN_LIBRARY = "https://openlibrary.org/search.json"
HEADERS = {"User-Agent": "NextRead-book-recommender-demo/1.0 (personal portfolio project)"}
BATCH = 40
PAUSE_SECONDS = 1.5


def download_dataset(client: httpx.Client):
    os.makedirs(RAW_DIR, exist_ok=True)
    for name in FILES:
        path = os.path.join(RAW_DIR, name)
        if os.path.exists(path):
            continue
        print(f"downloading {name}…")
        with client.stream("GET", DATASET_URL.format(name)) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)


def isbn10(value) -> str | None:
    """The CSV stores ISBN-10s as numbers, which drops leading zeros."""
    if pd.isna(value):
        return None
    text = str(value).strip().upper()
    return text.zfill(10) if len(text) <= 10 else None


def lookup_by_isbn(client: httpx.Client, isbns: list[str]) -> dict[str, int]:
    query = "isbn:(" + " OR ".join(isbns) + ")"
    r = client.get(OPEN_LIBRARY, params={"q": query, "fields": "isbn,cover_i", "limit": 200})
    r.raise_for_status()
    found = {}
    wanted = set(isbns)
    for doc in r.json().get("docs", []):
        if not doc.get("cover_i"):
            continue
        for isbn in wanted & set(doc.get("isbn", [])):
            found.setdefault(isbn, doc["cover_i"])
    return found


def lookup_by_title(client: httpx.Client, title: str, author: str) -> int | None:
    r = client.get(OPEN_LIBRARY, params={"title": title, "author": author,
                                         "fields": "cover_i", "limit": 5})
    r.raise_for_status()
    for doc in r.json().get("docs", []):
        if doc.get("cover_i"):
            return doc["cover_i"]
    return None


def fetch_covers(client: httpx.Client):
    books = pd.read_csv(os.path.join(RAW_DIR, "books.csv"), dtype={"isbn": str})
    missing = books[books["image_url"].str.contains("nophoto")]
    covers = {}
    if os.path.exists(COVERS_PATH):
        with open(COVERS_PATH, encoding="utf-8") as f:
            covers = json.load(f)  # book_id -> cover id (or null when none was found)
    todo = missing[~missing["book_id"].astype(str).isin(covers)]
    print(f"{len(missing)} books lack a cover; {len(todo)} not looked up yet")

    def save():
        with open(COVERS_PATH, "w", encoding="utf-8") as f:
            json.dump(covers, f)

    # 1. By ISBN, in batches.
    by_isbn = {isbn10(row.isbn): str(row.book_id) for row in todo.itertuples() if isbn10(row.isbn)}
    isbns = list(by_isbn)
    for start in range(0, len(isbns), BATCH):
        batch = isbns[start:start + BATCH]
        try:
            found = lookup_by_isbn(client, batch)
        except httpx.HTTPError as e:
            print(f"  ISBN batch failed ({e}); will retry by title")
            found = {}
        for isbn in batch:
            if isbn in found:
                covers[by_isbn[isbn]] = found[isbn]
        print(f"  ISBN batch {start // BATCH + 1}/{-(-len(isbns) // BATCH)}: {len(found)}/{len(batch)} found")
        save()
        time.sleep(PAUSE_SECONDS)

    # 2. By title and author, one at a time, for the rest.
    rest = todo[~todo["book_id"].astype(str).isin(covers)]
    print(f"{len(rest)} left to look up by title")
    for i, row in enumerate(rest.itertuples(), 1):
        title = row.title.split(" (")[0]
        author = row.authors.split(",")[0]
        try:
            covers[str(row.book_id)] = lookup_by_title(client, title, author)
        except httpx.HTTPError as e:
            print(f"  lookup failed for {title!r}: {e}")
            continue
        if i % 25 == 0:
            print(f"  {i}/{len(rest)}")
            save()
        time.sleep(1.0)
    save()
    found = sum(1 for v in covers.values() if v)
    print(f"covers found for {found} of {len(missing)} books without one")


def main():
    refresh = "--refresh" in sys.argv
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client:
        download_dataset(client)
        fetch_covers(client)
        new_books.fetch(client, refresh=refresh)


if __name__ == "__main__":
    main()
