"""Data loading and the recommendation model, shared by evaluate.py and build.py.

The model is item-to-item: for every book it finds the most similar books by
blending two signals.
  * Collaborative ("readers who loved this also loved"): cosine similarity
    between books over the readers who rated them 4-5 stars.
  * Content: cosine similarity of the books' reader tags (TF-IDF weighted,
    personal shelves like "to-read" removed) plus a shared-author bonus.
Recommendations for several loved books add up each book's neighbour lists,
the same way the website does it in the browser.
"""

import json
import os
import re

import numpy as np
import pandas as pd
import scipy.sparse as sp

import taxonomy

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(ROOT, "data", "raw")
COVERS_PATH = os.path.join(ROOT, "data", "covers.json")

LIKE_THRESHOLD = 4        # a rating of 4 or 5 stars counts as "loved"
MIN_TAG_BOOKS = 10        # tags used on fewer books are too idiosyncratic to compare on
# Tag counts are dominated by broad genres ("fantasy": 20,679 on The Name of the
# Wind vs "epic-fantasy": 1,023), so the thresholds are relative and low, and
# rarer (more specific) labels are preferred when there are too many.
SHELF_MIN_COUNT = 10      # a shelf needs at least this many readers' shelvings...
SHELF_RATIO = 0.05        # ...and 5% of the book's strongest shelf evidence
MAX_SHELVES = 4
THEME_MIN_COUNT = 10
THEME_RATIO = 0.02        # a theme needs 2% of the book's strongest shelf evidence
MAX_THEMES = 5

_SERIES = re.compile(r"^(?P<title>.*?)\s*\((?P<series>[^()]*?),?\s*#\s*(?P<num>[\d.]+(?:\s*-\s*[\d.]+)?)\)\s*$")
_COLLECTION = re.compile(r"box(?:ed)?[\s-]*set|boxset|omnibus|collection set", re.IGNORECASE)
_GR_COVER = re.compile(r"/books/(\d+)[a-z]/(\d+)\.jpg")


# ---------------------------------------------------------------- books

def load_books() -> pd.DataFrame:
    """One row per book, in book_id order (row i is book_id i + 1)."""
    books = pd.read_csv(os.path.join(RAW_DIR, "books.csv"), dtype={"isbn": str})
    books = books.sort_values("book_id").reset_index(drop=True)
    assert (books["book_id"].to_numpy() == np.arange(1, len(books) + 1)).all()

    parts = books["title"].str.extract(_SERIES)
    books["display_title"] = parts["title"].fillna(books["title"]).str.strip()
    empty = books["display_title"] == ""
    books.loc[empty, "display_title"] = books.loc[empty, "title"]
    books["series"] = parts["series"].str.strip()
    books["series_num"] = parts["num"].str.replace(" ", "")
    books["collection"] = (books["series_num"].str.contains("-", na=False)
                           | books["title"].str.contains(_COLLECTION))
    books["author"] = books["authors"].str.split(",").str[0].str.strip()
    books["year"] = books["original_publication_year"].astype("Int64")
    books["cover"] = cover_codes(books)
    return books


def cover_codes(books: pd.DataFrame) -> list:
    """Compact cover references: "g<stamp>/<id>" for Goodreads images,
    "o<id>" for Open Library covers, None when there's no cover."""
    ol = {}
    if os.path.exists(COVERS_PATH):
        with open(COVERS_PATH, encoding="utf-8") as f:
            ol = json.load(f)
    codes = []
    for book_id, url in zip(books["book_id"], books["image_url"]):
        m = _GR_COVER.search(url)
        if m and "nophoto" not in url:
            codes.append(f"g{m.group(1)}/{m.group(2)}")
        elif ol.get(str(book_id)):
            codes.append(f"o{ol[str(book_id)]}")
        else:
            codes.append(None)
    return codes


def series_ids(books: pd.DataFrame) -> np.ndarray:
    """An integer per series (-1 for standalone books)."""
    keys = books["series"].str.lower().str.replace(r"^the\s+", "", regex=True)
    codes, _ = pd.factorize(keys)
    return codes  # factorize already gives -1 for missing


# ---------------------------------------------------------------- tags

def load_tag_matrix(books: pd.DataFrame):
    """Sparse matrix of books x tags holding how many readers used each tag."""
    book_tags = pd.read_csv(os.path.join(RAW_DIR, "book_tags.csv"))
    tags = pd.read_csv(os.path.join(RAW_DIR, "tags.csv"))
    book_tags = book_tags[book_tags["count"] > 0]
    row_of = pd.Series(books.index, index=books["goodreads_book_id"])
    rows = book_tags["goodreads_book_id"].map(row_of).to_numpy()
    matrix = sp.csr_matrix((book_tags["count"].to_numpy(dtype=np.float32),
                            (rows, book_tags["tag_id"].to_numpy())),
                           shape=(len(books), len(tags)))
    matrix.sum_duplicates()
    names = tags.sort_values("tag_id")["tag_name"].to_numpy()
    return matrix, names


def _tag_score(matrix, index: dict, tags: list[str]) -> np.ndarray:
    cols = [index[t] for t in tags if t in index]
    if not cols:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    return np.asarray(matrix[:, cols].sum(axis=1)).ravel()


def assign_labels(matrix, names) -> tuple[list[list[str]], list[list[str]], np.ndarray]:
    """Shelves and themes for every book, from how readers tagged it."""
    index = {name: j for j, name in enumerate(names)}
    nonfiction = _tag_score(matrix, index, taxonomy.NONFICTION_TAGS) > \
        _tag_score(matrix, index, taxonomy.FICTION_TAGS)

    shelf_ids = [s["id"] for s in taxonomy.ALL_SHELVES]
    shelf_scores = np.column_stack([_tag_score(matrix, index, s["tags"]) for s in taxonomy.ALL_SHELVES])
    for j, shelf in enumerate(taxonomy.ALL_SHELVES):
        if shelf.get("requires") == "fiction":
            shelf_scores[nonfiction, j] = 0
        elif shelf.get("requires") == "nonfiction":
            shelf_scores[~nonfiction, j] = 0
    column = {s: j for j, s in enumerate(shelf_ids)}
    before = shelf_scores.copy()
    for shelf, rivals in taxonomy.RIVALS.items():
        rival_best = before[:, [column[r] for r in rivals]].max(axis=1)
        shelf_scores[before[:, column[shelf]] < 0.5 * rival_best, column[shelf]] = 0
    strongest = shelf_scores.max(axis=1)

    theme_ids = list(taxonomy.THEMES)
    theme_scores = np.column_stack([_tag_score(matrix, index, tags) for _, tags in taxonomy.THEMES.values()])

    def rarity(scores):
        # How specific a label is: rarer labels get priority for the limited slots.
        return np.log(len(scores) / (1 + (scores > 0).sum(axis=0)))

    shelf_rarity, theme_rarity = rarity(shelf_scores), rarity(theme_scores)
    shelves, themes = [], []
    for i in range(matrix.shape[0]):
        keep = [(shelf_scores[i, j] * shelf_rarity[j], shelf_ids[j]) for j in np.nonzero(shelf_scores[i])[0]
                if shelf_scores[i, j] >= max(SHELF_MIN_COUNT, SHELF_RATIO * strongest[i])]
        shelves.append([s for _, s in sorted(keep, reverse=True)[:MAX_SHELVES]])
        keep = [(theme_scores[i, j] * theme_rarity[j], theme_ids[j]) for j in np.nonzero(theme_scores[i])[0]
                if theme_scores[i, j] >= max(THEME_MIN_COUNT, THEME_RATIO * strongest[i])]
        themes.append([t for _, t in sorted(keep, reverse=True)[:MAX_THEMES]])
    return shelves, themes, nonfiction


def content_matrix(matrix, names, books: pd.DataFrame, author_weight: float = 0.5):
    """Row-normalised TF-IDF over descriptive tags, plus a primary-author column."""
    keep = np.array([not taxonomy.is_noise(n) for n in names])
    tagged = matrix[:, np.nonzero(keep)[0]].tocsc()
    doc_freq = np.diff(tagged.indptr)
    tagged = tagged[:, np.nonzero(doc_freq >= MIN_TAG_BOOKS)[0]].tocsr()

    weights = tagged.copy()
    weights.data = np.log1p(weights.data)
    n = matrix.shape[0]
    df = np.asarray((tagged > 0).sum(axis=0)).ravel()
    weights = weights @ sp.diags(np.log(n / df).astype(np.float32))
    weights = _normalise_rows(weights)

    author_codes, _ = pd.factorize(books["author"])
    authors = sp.csr_matrix((np.full(n, author_weight, dtype=np.float32),
                             (np.arange(n), author_codes)))
    return _normalise_rows(sp.hstack([weights, authors]).tocsr())


def _normalise_rows(m):
    norms = np.sqrt(np.asarray(m.multiply(m).sum(axis=1)).ravel())
    norms[norms == 0] = 1
    return sp.diags((1 / norms).astype(np.float32)) @ m


# ---------------------------------------------------------------- ratings

def load_ratings() -> pd.DataFrame:
    return pd.read_csv(os.path.join(RAW_DIR, "ratings.csv"),
                       dtype={"user_id": np.int32, "book_id": np.int32, "rating": np.int8})


def like_matrix(ratings: pd.DataFrame, n_books: int, graded: bool = False):
    """Readers x books: 1 for a loved book (4-5 stars); with graded, 5 stars counts 2."""
    liked = ratings[ratings["rating"] >= LIKE_THRESHOLD]
    users, user_rows = np.unique(liked["user_id"].to_numpy(), return_inverse=True)
    values = (liked["rating"].to_numpy() - (LIKE_THRESHOLD - 1)).astype(np.float32) if graded \
        else np.ones(len(liked), dtype=np.float32)
    return sp.csr_matrix((values, (user_rows, liked["book_id"].to_numpy() - 1)),
                         shape=(len(users), n_books))


# ---------------------------------------------------------------- similarity

def neighbours(likes, content, alphas: list[float], k: int = 50, block: int = 500, seed: int = 0,
               candidate_exponent: float = 0.5):
    """Top-k most similar books for every book, for each blend weight alpha.

    alpha=1 is purely collaborative, alpha=0 purely tag-based. Both similarity
    kinds are rescaled to comparable ranges before blending. Returns
    {alpha: (indices, similarities)} with rows sorted best first.

    candidate_exponent controls how much a candidate's own popularity is
    discounted: shared fans / (|fans of the book| ** 0.5 * |fans of the candidate| ** e).
    0.5 is plain cosine similarity; higher values stop mega-bestsellers, which
    everyone has read, from topping every list.
    """
    n = likes.shape[1]
    by_book = likes.T.tocsr()                       # books x readers
    sq_norms = np.asarray(by_book.multiply(by_book).sum(axis=1)).ravel()
    sq_norms[sq_norms == 0] = 1
    seed_norms = np.sqrt(sq_norms)
    candidate_norms = sq_norms ** candidate_exponent

    def collaborative(rows):
        dots = (by_book[rows] @ likes).toarray()
        return dots / seed_norms[rows, None] / candidate_norms[None, :]

    def tag_based(rows):
        return (content[rows] @ content.T).toarray()

    # Typical strength of a close neighbour, measured on a sample of books,
    # so the two similarity kinds can be put on the same scale.
    sample = np.random.default_rng(seed).choice(n, size=min(500, n), replace=False)
    cf_scale = _typical_top(collaborative(sample), sample, k)
    ct_scale = _typical_top(tag_based(sample), sample, k)

    out = {a: (np.empty((n, k), dtype=np.int32), np.empty((n, k), dtype=np.float32)) for a in alphas}
    for start in range(0, n, block):
        rows = np.arange(start, min(start + block, n))
        cf = collaborative(rows) / cf_scale
        ct = tag_based(rows) / ct_scale
        for alpha in alphas:
            blend = alpha * cf + (1 - alpha) * ct
            blend[np.arange(len(rows)), rows] = -np.inf     # a book isn't its own neighbour
            top = np.argpartition(-blend, k, axis=1)[:, :k]
            vals = np.take_along_axis(blend, top, axis=1)
            order = np.argsort(-vals, axis=1)
            out[alpha][0][rows] = np.take_along_axis(top, order, axis=1)
            out[alpha][1][rows] = np.take_along_axis(vals, order, axis=1)
    return out


def label_matrix(shelves: list[list[str]], themes: list[list[str]], authors: list[str],
                 theme_weight: float = 0.6, author_weight: float = 0.25):
    """Books x labels: shelves (the first is the most characteristic), themes and
    author, weighted by rarity and row-normalised. This is the description that
    Goodreads and Open Library books have in common, so it can compare the two."""
    shelf_weights = (1.0, 0.85, 0.7, 0.6)
    vocab: dict[str, int] = {}
    rows, cols, vals = [], [], []
    for i, (book_shelves, book_themes, author) in enumerate(zip(shelves, themes, authors)):
        labels = [(f"s:{s}", shelf_weights[min(pos, 3)]) for pos, s in enumerate(book_shelves)]
        labels += [(f"t:{t}", theme_weight) for t in book_themes] + [(f"a:{author}", author_weight)]
        for key, weight in labels:
            rows.append(i)
            cols.append(vocab.setdefault(key, len(vocab)))
            vals.append(weight)
    m = sp.csr_matrix((np.array(vals, dtype=np.float32), (rows, cols)), shape=(len(shelves), len(vocab)))
    df = np.asarray((m > 0).sum(axis=0)).ravel()
    rarity = np.clip(np.log(len(shelves) / (1 + df)), 0.1, None).astype(np.float32)
    return _normalise_rows(m @ sp.diags(rarity))


def label_neighbours(labels, rows: np.ndarray, candidates: np.ndarray, k: int,
                     bonus: np.ndarray | None = None, min_sim: float = 0.0, block: int = 500):
    """For each book in rows, the k most similar candidates by label cosine.

    bonus (per book, small) only breaks ties in the ranking, e.g. towards
    better-known books. Slots below min_sim are left as -1.
    """
    candidates = np.asarray(candidates)
    position = np.full(labels.shape[0], -1)
    position[candidates] = np.arange(len(candidates))
    by_candidate = labels[candidates].T.tocsc()
    extra = bonus[candidates][None, :] if bonus is not None else 0
    indices = np.full((len(rows), k), -1, dtype=np.int32)
    sims = np.zeros((len(rows), k), dtype=np.float32)
    for start in range(0, len(rows), block):
        chunk = rows[start:start + block]
        true = (labels[chunk] @ by_candidate).toarray()
        ranked = true + extra
        own = position[chunk]
        has_own = own >= 0
        ranked[np.nonzero(has_own)[0], own[has_own]] = -np.inf   # not its own neighbour
        top = np.argpartition(-ranked, k, axis=1)[:, :k]
        order = np.argsort(-np.take_along_axis(ranked, top, axis=1), axis=1)
        top = np.take_along_axis(top, order, axis=1)
        values = np.take_along_axis(true, top, axis=1)
        found = candidates[top]
        found[values < min_sim] = -1
        indices[start:start + len(chunk)] = found
        sims[start:start + len(chunk)] = np.where(values < min_sim, 0, values)
    return indices, sims


def _typical_top(sims: np.ndarray, rows: np.ndarray, k: int) -> float:
    sims = sims.copy()
    sims[np.arange(len(rows)), rows] = 0
    top = -np.partition(-sims, k, axis=1)[:, :k]
    return float(np.median(top)) or 1.0


def similarity_matrix(indices: np.ndarray, sims: np.ndarray, k: int | None = None):
    """Sparse books x books matrix holding each book's top-k neighbours."""
    if k is not None:
        indices, sims = indices[:, :k], sims[:, :k]
    n, kk = indices.shape
    return sp.csr_matrix((np.maximum(sims, 0).ravel(), (np.repeat(np.arange(n), kk), indices.ravel())),
                         shape=(n, n))


# ---------------------------------------------------------------- ranking

class Ranker:
    """Turns similarity scores into a ranked list, with the website's rules:
      * popular books get a small boost (score x ratings_count ** popularity),
      * a later book in a series passes half its score to the series' first
        book and is dropped, so readers are pointed at book 1, not book 3,
      * the loved books, other books in their series and box sets are skipped,
      * at most two books per author."""

    def __init__(self, books: pd.DataFrame, author_cap: int = 2,
                 popularity: float = 0.0, series_rollup: bool = True):
        self.series = series_ids(books)
        self.authors = pd.factorize(books["author"])[0]
        self.collection = books["collection"].to_numpy()
        self.author_cap = author_cap
        self.boost = books["ratings_count"].to_numpy(dtype=np.float64) ** popularity
        self.series_rollup = series_rollup
        self.later, self.first_of = _series_starts(books, self.series, self.collection)

    def rank(self, scores: np.ndarray, loved: np.ndarray, n: int = 10) -> list[int]:
        scores = scores.astype(np.float64, copy=True) * self.boost
        if self.series_rollup:
            moved = scores[self.later]
            np.add.at(scores, self.first_of[self.later], 0.5 * moved)
            scores[self.later] = 0
        scores[self.collection] = -np.inf
        scores[loved] = -np.inf
        loved_series = self.series[loved]
        loved_series = loved_series[loved_series >= 0]
        if len(loved_series):
            scores[np.isin(self.series, loved_series)] = -np.inf

        # Every positive candidate, best first (ties by book number, like the site),
        # so the author cap can't leave the list short.
        pool = np.nonzero(np.isfinite(scores) & (scores > 0))[0]
        pool = pool[np.lexsort((pool, -scores[pool]))]
        picked, per_author = [], {}
        for i in pool:
            if not np.isfinite(scores[i]) or scores[i] <= 0:
                break
            a = self.authors[i]
            if per_author.get(a, 0) >= self.author_cap:
                continue
            per_author[a] = per_author.get(a, 0) + 1
            picked.append(int(i))
            if len(picked) == n:
                break
        return picked


def series_number(value) -> float:
    try:
        return float(str(value).split("-")[0])
    except ValueError:
        return float("nan")


def _series_starts(books: pd.DataFrame, series: np.ndarray, collection: np.ndarray):
    """For books after the first one in their series: a mask, and each book's
    series opener: book 1 (or the lowest number from 1 up), ahead of prequel
    novellas numbered below 1 (Divergent #0.1), and never a box set."""
    numbers = books["series_num"].map(series_number).to_numpy(dtype=np.float64)
    first_of = np.full(len(books), -1)
    opener = {}
    for i in np.lexsort((numbers, numbers < 1)):
        s = series[i]
        if s >= 0 and not collection[i] and np.isfinite(numbers[i]) and s not in opener:
            opener[s] = i
    for i, s in enumerate(series):
        if s in opener:
            first_of[i] = opener[s]
    later = (first_of >= 0) & (first_of != np.arange(len(books)))
    return later, first_of
