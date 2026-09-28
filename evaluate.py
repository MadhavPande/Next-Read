"""Measure how well the recommender predicts what readers loved, and pick its settings.

    .venv\\Scripts\\python evaluate.py

Method: 5,000 readers (with 20+ books rated 4-5 stars) are held out entirely.
The model is built from everyone else's ratings. For each held-out reader we
pretend they told the site 5 books they loved (the "For you" page) or 1 book
("books like this one"), take the top 10 recommendations with the site's
rules, and measure:
  * precision: how many of the 10 are among the other books that reader loved
    (books in the same series as the chosen ones don't count; the site lists
    those separately),
  * tag similarity: how alike the 10 are to the starting book, by reader tags
    (the cosine of their tag profiles, 0 to 1),
  * coverage: how much of the catalogue shows up across all readers' top 10s.

Precision alone rewards recommending what everyone has read: suggesting the
same bestsellers to all readers scores well here. So the settings are chosen
in two steps: keep every setting within 90% of the best precision that also
recommends from at least a fifth of the catalogue, then pick the one whose
recommendations are most like the starting book.
Results go to data/evaluation.json, which build.py reads.
"""

import json
import os
import time

import numpy as np

import recommender as rec

TEST_USERS = 5000
MIN_LIKES = 20
PROFILE_SIZES = [5, 1]
TOP_N = 10
K = 50
ALPHAS = [0.0, 0.3, 0.5, 0.7, 0.85, 1.0]
CANDIDATE_EXPONENTS = [0.5, 0.6, 0.7, 0.8]
GRID_ALPHAS = [0.7, 1.0]
POPULARITY = [0.0, 0.1]
PRECISION_FLOOR = 0.9
MIN_COVERAGE = 0.2
OUT_PATH = os.path.join(rec.ROOT, "data", "evaluation.json")


def main():
    t0 = time.time()
    rng = np.random.default_rng(42)
    books = rec.load_books()
    n = len(books)
    series = rec.series_ids(books)
    collection = books["collection"].to_numpy()

    tag_matrix, tag_names = rec.load_tag_matrix(books)
    content = rec.content_matrix(tag_matrix, tag_names, books)
    tags_only = rec.content_matrix(tag_matrix, tag_names, books, author_weight=0.0)

    ratings = rec.load_ratings()
    loved = ratings[ratings["rating"] >= rec.LIKE_THRESHOLD]
    likes_per_user = loved.groupby("user_id").size()
    eligible = likes_per_user[likes_per_user >= MIN_LIKES].index.to_numpy()
    test_users = rng.choice(eligible, size=TEST_USERS, replace=False)
    train = ratings[~ratings["user_id"].isin(test_users)]
    likes = rec.like_matrix(train, n)
    test_loved = loved[loved["user_id"].isin(test_users)].groupby("user_id")["book_id"].apply(
        lambda s: s.to_numpy() - 1)
    print(f"{len(test_users)} test readers held out; training on {train['user_id'].nunique()} readers")

    # Each test reader's pretend "books I loved" and the loved books we hope to find.
    cases = {size: [] for size in PROFILE_SIZES}
    for books_loved in test_loved:
        for size in PROFILE_SIZES:
            profile = rng.choice(books_loved, size=size, replace=False)
            profile_series = series[profile][series[profile] >= 0]
            hidden = np.setdiff1d(books_loved, profile)
            hidden = hidden[~np.isin(series[hidden], profile_series) & ~collection[hidden]]
            if len(hidden):
                cases[size].append((profile, set(hidden.tolist())))

    results = []

    def evaluate(name, scores_for, settings=None, popularity=0.0, rollup=True):
        ranker = rec.Ranker(books, popularity=popularity, series_rollup=rollup)
        row = {"model": name}
        for size in PROFILE_SIZES:
            hits, any_hit, shown, alike = 0, 0, set(), []
            for profile, hidden in cases[size]:
                top = ranker.rank(scores_for(profile), profile, TOP_N)
                found = len(hidden.intersection(top))
                hits += found
                any_hit += found > 0
                shown.update(top)
                if size == 1 and top:
                    alike.append(float((tags_only[top] @ tags_only[profile[0]].T).toarray().mean()))
            count = len(cases[size])
            row[f"precision_at_10_from_{size}"] = round(hits / (count * TOP_N), 4)
            row[f"hit_rate_at_10_from_{size}"] = round(any_hit / count, 4)
            if size == 1:
                row["tag_similarity_from_1"] = round(float(np.mean(alike)), 4)
            else:
                row["catalog_coverage"] = round(len(shown) / n, 4)
        row["precision"] = round((row["precision_at_10_from_5"] + row["precision_at_10_from_1"]) / 2, 4)
        if settings:
            row.update(settings, popularity=popularity, series_rollup=rollup)
        results.append(row)
        print(f"  {name:<50} p@10(5)={row['precision_at_10_from_5']:.3f} p@10(1)={row['precision_at_10_from_1']:.3f} "
              f"alike={row['tag_similarity_from_1']:.3f} coverage={row['catalog_coverage']:.3f} "
              f"({time.time() - t0:.0f}s)")
        return row

    def model(neigh, alpha, exponent, k=K, popularity=0.0, graded=False, rollup=True):
        S = rec.similarity_matrix(*neigh[alpha], k=k)
        name = (f"alpha={alpha} exponent={exponent} k={k}{f' popularity={popularity}' if popularity else ''}"
                f"{' graded' if graded else ''}{'' if rollup else ' no-series-rollup'}")
        return evaluate(name, lambda p: np.asarray(S[p].sum(axis=0)).ravel(),
                        {"alpha": alpha, "exponent": exponent, "k": k, "graded": graded}, popularity, rollup)

    print("baselines:")
    popularity = np.bincount(train.loc[train["rating"] >= rec.LIKE_THRESHOLD, "book_id"] - 1,
                             minlength=n).astype(float)
    evaluate("popular books", lambda p: popularity)
    evaluate("random books", lambda p: rng.random(n) + 1e-9)

    print("1. blend of ratings (alpha=1) and tags (alpha=0):")
    neigh = rec.neighbours(likes, content, ALPHAS, k=K)
    grid = [model(neigh, alpha, 0.5) for alpha in ALPHAS]

    print("2. discounting popular candidates (exponent), with and without a boost for well-known books:")
    for exponent in CANDIDATE_EXPONENTS:
        if exponent != 0.5:
            neigh = rec.neighbours(likes, content, GRID_ALPHAS, k=K, candidate_exponent=exponent)
        for alpha in GRID_ALPHAS:
            for pop in POPULARITY:
                if (exponent, pop) != (0.5, 0.0):   # already measured in step 1
                    grid.append(model(neigh, alpha, exponent, popularity=pop))

    best_precision = max(r["precision"] for r in grid)
    shortlist = [r for r in grid if r["precision"] >= PRECISION_FLOOR * best_precision
                 and r["catalog_coverage"] >= MIN_COVERAGE]
    chosen = max(shortlist, key=lambda r: r["tag_similarity_from_1"])
    print(f"best precision {best_precision:.3f}; {len(shortlist)} settings within 90% of it; "
          f"most alike: {chosen['model']}")

    print("3. checks on the chosen setting:")
    exponent, alpha, pop = chosen["exponent"], chosen["alpha"], chosen["popularity"]
    graded = rec.neighbours(rec.like_matrix(train, n, graded=True), content, [alpha], k=K,
                            candidate_exponent=exponent)
    model(graded, alpha, exponent, popularity=pop, graded=True)
    neigh = rec.neighbours(likes, content, [alpha], k=K, candidate_exponent=exponent)
    for k in (20, 30):
        model(neigh, alpha, exponent, k=k, popularity=pop)
    model(neigh, alpha, exponent, popularity=pop, rollup=False)

    summary = {
        "test_readers": len(test_users),
        "rule": f"within {PRECISION_FLOOR:.0%} of the best precision and coverage >= {MIN_COVERAGE:.0%}, "
                "then most alike to the starting book",
        "chosen": {key: chosen[key] for key in ("alpha", "exponent", "k", "graded", "popularity")},
        "chosen_model": chosen["model"],
        "results": results,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nchosen: {chosen['model']}  (saved to {OUT_PATH}, total {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
