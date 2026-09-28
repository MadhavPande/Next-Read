# Next Read: a book recommender

Next Read recommends books three ways:

1. **By mood:** pick a mood (Other worlds, Page-turners, Love stories, True stories, Timeless reads,
   Young at heart, Dark & eerie, Feel-good), see one top pick from each of its shelves, then
   browse a shelf with filters for genre, theme, era, rating, popularity and series.
2. **Books like this one:** every book page shows what the readers who loved it also loved, plus
   the books readers tagged most alike, the rest of its series, and more by the author.
3. **For you:** tap ♡ on the books you've loved and get blended recommendations, each saying which of
   your books it came from, plus the next book in each series you've started.

It's a static website: everything is computed ahead of time in Python, and the page does the rest in
the browser. There's no server, no AI model and no API key, so it's free to host.

## Data

- **Books up to 2017:** [goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k), the 10,000
  most-rated books on Goodreads, with 5,976,479 ratings from 53,424 readers, plus the shelves readers
  put each book on. It's licensed CC BY-SA 4.0. The files in `site/` are derived from it and shared
  under the same license. Missing covers (a third of the books) were filled in from Open Library.
- **Books from 2018 on:** 5,000 of the most-read newer books on [Open Library](https://openlibrary.org),
  the Internet Archive's open catalogue, taken from its official API (`new_books.py`). Each comes with
  subjects, a cover, and Open Library's own reader numbers: ratings, "want to read" and "have read".
  Goodreads isn't scraped; its terms don't allow it. Instead, every book page links to the book on
  Goodreads, Open Library and Google Books, and says where its details come from.

## How it works

- **Moods, shelves and themes** come from reader tags (`taxonomy.py`). A book joins a shelf when enough
  readers filed it there. The thresholds are relative, because broad tags swamp specific ones ("fantasy"
  20,679 vs "epic-fantasy" 1,023 on *The Name of the Wind*). Personal shelves like "to-read" and
  "read-in-2015" are filtered out.
- **Similar books** (`recommender.py`): for each book, the 50 most similar books by a 70/30 blend of:
  - *ratings*: cosine similarity over which readers rated both books 4–5 stars;
  - *tags*: cosine similarity of the books' TF-IDF-weighted tag profiles, plus a shared-author bonus.
- **Recommendations** add up the similar-book lists of every book you loved, with these rules:
  - later books in a series point you to book 1;
  - books from series you've started go in their own row;
  - box sets are skipped;
  - no author gets more than two spots.

  The page runs this in JavaScript. On 400 random test cases it produces exactly the same lists as the
  Python version that was evaluated.
- **Newer books** can't use ratings: Open Library's reading data is anonymous, so nothing says which
  reader read what. Their Open Library subjects ("Fiction, romance, contemporary", "Dragons", New York
  Times list names) are mapped onto the same shelves and themes (`taxonomy.py`), and they're matched
  to other books by shared shelves, themes and author, with rarer labels counting more. That powers
  "Same genre and themes" on their pages, and the For you page when all your favourites are newer
  books. They're marked "New", and weren't part of the accuracy test below.

## How accurate is it?

`evaluate.py` holds out 5,000 readers, builds the model from everyone else, gives it 5 books (or 1)
that each held-out reader loved, and checks the top 10 against the other books they loved.

| | Next Read | Recommend the bestsellers |
|---|---|---|
| Correct picks in top 10, from 5 loved books | **3.3** | 2.0 |
| Readers with at least one correct pick | **92%** | 77% |
| Correct picks in top 10, from 1 book | **2.2** | 2.2 |
| How alike the picks are to the starting book (tags, 0–1) | **0.40** | 0.07 |
| Share of the catalogue that gets recommended | **31%** | 0.1% |

Things the evaluation found:

- **Ratings beat tags for accuracy:** 3.4 vs 1.6 correct picks. Blending in 30% tags keeps nearly all
  of that accuracy and makes picks noticeably more like the starting book, so that's the chosen
  setting. The rule is: keep settings within 90% of the best accuracy, then pick the most alike.
- **From a single very popular book, "readers who loved this" leans towards other huge bestsellers.**
  Almost every reader in the data has read them. That's why book pages also have a tags-only
  "Same genre and themes" row: *The Hunger Games* → *The Maze Runner*, *The Testing*, *Matched*, *Unwind*.
- **Pointing to book 1 of a series costs measured accuracy (3.5 → 3.3)** because the test rewards
  listing every book of a series. It's kept anyway, since three slots on one series helps nobody.
- **Discounting popular books inside the similarity measure** made picks only slightly more alike
  and cost 10–25% of the accuracy, so it isn't used.

Full numbers are in `data/evaluation.json` (created by `evaluate.py`).

## Running it

```powershell
cd "D:\Projects\Book Recommender"
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

.venv\Scripts\python fetch_data.py   # dataset, missing covers, newer books (about 20 minutes, once)
                                     # add --refresh to re-download the newer books
.venv\Scripts\python evaluate.py     # optional: re-tune the settings (about 6 minutes)
.venv\Scripts\python build.py        # builds the site/ folder (about 30 seconds)
```

Preview it at http://127.0.0.1:8010 by serving the `site` folder:

```powershell
cd site
..\.venv\Scripts\python -m http.server 8010
```

## Publishing on Netlify

Sign up at https://app.netlify.com/drop, then drag the `site` folder onto the page. To update it
later, run `build.py` again and drag the new `site` folder onto your site's **Deploys** tab. File names
carry a version tag, so visitors always get the new version.

The page downloads 0.9 MB of book data up front, and 2.1 MB of similarity data the first time someone
asks for recommendations.

## Files

| File | What it does |
|---|---|
| `fetch_data.py` | Downloads goodbooks-10k, missing covers and the newer books |
| `new_books.py` | Downloads, filters and labels the newer books from Open Library |
| `taxonomy.py` | Moods, shelves, themes, the filter for personal shelves, and the Open Library subject rules |
| `recommender.py` | Loading data, labelling books, similarity, and the ranking rules |
| `evaluate.py` | Offline evaluation and choice of settings |
| `build.py` | Builds `site/`: `books.json`, `similar.json` and the page |
| `web/` | The page source: `index.html`, `style.css`, `app.js` |
| `site/` | The built website to upload |
| `data/` | Downloaded dataset, cover lookups, evaluation results |

## Limits

- Ratings-based recommendations only cover the 10,000 Goodreads books up to 2017. Newer books are
  matched by genre, themes and author, which is rougher.
- Open Library has far fewer readers than Goodreads: most newer books have under 5 ratings there, so
  no average is shown for them. Some very popular books (*Atomic Habits*) are missing because Open
  Library has no genre information for them.
- Favourites are stored in the visitor's browser, so they don't follow them to other devices.
- Covers load from Goodreads' and Open Library's servers; Open Library covers can take a few seconds.
