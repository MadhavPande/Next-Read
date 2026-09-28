"""Build the website into site/, ready to upload to Netlify.

    .venv\\Scripts\\python build.py

Combines the 10,000 Goodreads books (goodbooks-10k, to 2017) with newer books
from Open Library (see new_books.py), then writes:
  site/books.json    the catalogue: titles, authors, covers, genres, themes, sources
  site/similar.json  each book's neighbour lists (loaded only when needed):
      ids / w        Goodreads books: most similar by 6 million ratings, blended
                     with tags (settings chosen by evaluate.py)
      tag_ids        every book: most alike by genre and themes
and copies the page from web/.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import time

import numpy as np
import pandas as pd

import new_books
import recommender as rec
import taxonomy

WEB_DIR = os.path.join(rec.ROOT, "web")
SITE_DIR = os.path.join(rec.ROOT, "site")
EVALUATION_PATH = os.path.join(rec.ROOT, "data", "evaluation.json")
TAG_K = 20        # "Same genre and themes" neighbours per book
DEFAULTS = {"alpha": 1.0, "exponent": 0.5, "k": 50, "graded": False, "popularity": 0.1}


def load_settings():
    if not os.path.exists(EVALUATION_PATH):
        print("no evaluation found; using default settings (run evaluate.py to tune)")
        return DEFAULTS, None
    with open(EVALUATION_PATH, encoding="utf-8") as f:
        evaluation = json.load(f)
    return evaluation["chosen"], evaluation


def evaluation_summary(evaluation, settings):
    """The headline numbers shown on the site's About panel."""
    if not evaluation:
        return None
    rows = {r["model"]: r for r in evaluation["results"]}
    ours, popular = rows[evaluation["chosen_model"]], rows["popular books"]
    return {
        "readers": evaluation["test_readers"],
        "precision": ours["precision_at_10_from_5"],
        "hit_rate": ours["hit_rate_at_10_from_5"],
        "single_precision": ours["precision_at_10_from_1"],
        "coverage": ours["catalog_coverage"],
        "popular_precision": popular["precision_at_10_from_5"],
        "popular_single_precision": popular["precision_at_10_from_1"],
    }


def book_records(books, shelves, themes):
    records = []
    for row, book_shelves, book_themes in zip(books.itertuples(), shelves, themes):
        record = {
            "t": row.display_title,
            "a": row.author,
            "y": None if pd.isna(row.year) else int(row.year),
            "r": round(float(row.average_rating), 2),
            "c": int(row.ratings_count),
            "img": row.cover if isinstance(row.cover, str) else None,
            "gr": int(row.goodreads_book_id),
            "sh": book_shelves,
            "th": book_themes,
        }
        isbn = clean_isbn(row.isbn)
        if isbn:
            record["isbn"] = isbn
        if isinstance(row.series, str) and row.series:
            record["sr"] = [row.series, row.series_num]
        if row.collection:
            record["col"] = 1
        records.append(record)
    return records


def clean_isbn(value) -> str | None:
    """goodbooks stores ISBN-10s as numbers, which drops leading zeros."""
    if not isinstance(value, str):
        return None
    text = value.strip().upper().zfill(10)
    return text if re.fullmatch(r"\d{9}[\dX]", text) else None


def write_json(name, data):
    path = os.path.join(SITE_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path) / 1e6


def stamp_versions():
    """Add content hashes to file references so browsers never keep an old copy."""
    def stamp(name):
        with open(os.path.join(SITE_DIR, name), "rb") as f:
            return f"{name}?v={hashlib.sha1(f.read()).hexdigest()[:10]}"

    # app.js first: its own stamp must include the data stamps written into it.
    for page, refs in [("app.js", ["books.json", "similar.json"]), ("index.html", ["style.css", "app.js"])]:
        path = os.path.join(SITE_DIR, page)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for ref in refs:
            text = text.replace(f'"{ref}"', f'"{stamp(ref)}"')
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def main():
    t0 = time.time()
    settings, evaluation = load_settings()
    print(f"settings: {settings}")

    books = rec.load_books()
    tag_matrix, tag_names = rec.load_tag_matrix(books)
    shelves, themes, _ = rec.assign_labels(tag_matrix, tag_names)
    content = rec.content_matrix(tag_matrix, tag_names, books)
    ratings = rec.load_ratings()
    likes = rec.like_matrix(ratings, len(books), graded=settings["graded"])
    k = settings["k"]
    indices, sims = rec.neighbours(likes, content, [settings["alpha"]], k=k,
                                   candidate_exponent=settings.get("exponent", 0.5))[settings["alpha"]]
    # Tag-only neighbours (alpha=0): the books readers tagged most alike,
    # whatever their popularity.
    tag_indices, _ = rec.neighbours(likes, content, [0.0], k=TAG_K)[0.0]
    print(f"neighbours for {len(books)} books computed ({time.time() - t0:.0f}s)")

    # Similarities as whole numbers 1-99 (the page only compares and adds them).
    scale = np.percentile(sims[:, 0], 99)
    weights = np.clip(np.rint(sims / scale * 99), 1, 99).astype(int)

    # Newer books from Open Library, after the 10,000 Goodreads books.
    old_records = book_records(books, shelves, themes)
    known = {new_books.title_key(t, a) for t, a in zip(books["display_title"], books["author"])}
    new_records, fetched = new_books.load(known) if os.path.exists(new_books.RAW_PATH) else ([], "")
    records = old_records + new_records
    n_old, n_all = len(old_records), len(records)

    # Genre-and-theme matching works across both sources: shelves, themes and
    # author, with a tiny nudge towards better-known books to break ties.
    labels = rec.label_matrix([r["sh"] for r in records], [r["th"] for r in records], [r["a"] for r in records])
    popularity = np.concatenate([new_books.popularity_percentiles(books["ratings_count"]),
                                 new_books.popularity_percentiles([r["wr"] + r["rd"] for r in new_records])])
    bonus = 0.02 * popularity
    tag_ids = tag_indices
    if new_records:
        new_tag_ids, _ = rec.label_neighbours(labels, np.arange(n_old, n_all), np.arange(n_all), TAG_K, bonus)
        tag_ids = np.vstack([tag_indices, new_tag_ids])
    print(f"{len(new_records)} newer books added ({time.time() - t0:.0f}s)")

    # Empty site/ rather than deleting it, so a preview server running in the
    # folder (which Windows won't let go of) doesn't break the build.
    os.makedirs(SITE_DIR, exist_ok=True)
    for name in os.listdir(SITE_DIR):
        path = os.path.join(SITE_DIR, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    shutil.copytree(WEB_DIR, SITE_DIR, dirs_exist_ok=True)

    catalogue = {
        "meta": {
            "built": datetime.date.today().isoformat(),
            "books": n_all,
            "ratings": int(len(ratings)),
            "readers": int(ratings["user_id"].nunique()),
            "sources": {"goodreads_books": n_old, "openlibrary_books": len(new_records),
                        "openlibrary_fetched": fetched},
            "evaluation": evaluation_summary(evaluation, settings),
            # The page applies the same ranking rules the evaluation measured.
            "model": {"k": k, "popularity": settings.get("popularity", 0.0)},
        },
        "moods": [{"id": m["id"], "label": m["label"], "blurb": m["blurb"],
                   "shelves": [{"id": s["id"], "label": s["label"]} for s in m["shelves"]]}
                  for m in taxonomy.MOODS],
        "themes": {tid: label for tid, (label, _) in taxonomy.THEMES.items()},
        "books": records,
    }
    books_mb = write_json("books.json", catalogue)
    similar_mb = write_json("similar.json", {
        "k": k, "ids": indices.ravel().tolist(), "w": weights.ravel().tolist(),
        "tag_k": TAG_K, "tag_ids": tag_ids.ravel().tolist(),
    })
    stamp_versions()
    print(f"site/ built in {time.time() - t0:.0f}s: books.json {books_mb:.1f} MB, "
          f"similar.json {similar_mb:.1f} MB (before compression)")


if __name__ == "__main__":
    main()
