"""Moods, shelves (sub-genres) and themes, all derived from Goodreads reader tags.

A book joins a shelf when readers shelved it under that shelf's tags often
enough (see assign_labels in recommender.py). Moods are groups of shelves.
"""

import re

# Each mood is a front-page tile; its shelves are the categories inside it.
# "requires" limits a shelf to fiction or non-fiction books, for tags that are
# used both ways ("historical" is on history books and historical novels).
MOODS = [
    {
        "id": "other-worlds", "label": "Other worlds",
        "blurb": "Fantasy, science fiction & the supernatural",
        "shelves": [
            {"id": "epic-fantasy", "label": "Epic fantasy",
             "tags": ["epic-fantasy", "high-fantasy", "sword-and-sorcery", "adult-fantasy"]},
            {"id": "fantasy", "label": "Fantasy",
             "tags": ["fantasy", "fantasy-fiction", "magic", "ya-fantasy"]},
            {"id": "urban-paranormal", "label": "Urban & paranormal",
             "tags": ["urban-fantasy", "paranormal", "supernatural", "vampires", "vampire",
                      "witches", "werewolves", "shapeshifters", "fae", "faeries", "demons", "angels"]},
            {"id": "science-fiction", "label": "Science fiction",
             "tags": ["science-fiction", "sci-fi", "scifi", "sf", "space", "space-opera",
                      "cyberpunk", "aliens", "time-travel"]},
            {"id": "dystopian", "label": "Dystopian",
             "tags": ["dystopian", "dystopia", "post-apocalyptic", "apocalyptic", "zombies"]},
        ],
    },
    {
        "id": "page-turners", "label": "Page-turners",
        "blurb": "Mysteries & thrillers you can't put down",
        "shelves": [
            {"id": "mystery", "label": "Mystery", "requires": "fiction",
             "tags": ["mystery", "mysteries", "murder-mystery", "detective", "whodunit"]},
            {"id": "cozy-mystery", "label": "Cozy mystery",
             "tags": ["cozy-mystery", "cozy-mysteries", "cozy"]},
            {"id": "thriller", "label": "Thriller", "requires": "fiction",
             "tags": ["thriller", "thrillers", "suspense", "psychological-thriller", "legal-thriller"]},
            {"id": "crime", "label": "Crime", "requires": "fiction",
             "tags": ["crime", "crime-fiction", "noir", "police", "procedural", "serial-killer"]},
            {"id": "spy", "label": "Spies & espionage",
             "tags": ["espionage", "spy", "spies"]},
        ],
    },
    {
        "id": "love-stories", "label": "Love stories",
        "blurb": "Romance in every flavour",
        "shelves": [
            {"id": "romance", "label": "Romance", "requires": "fiction",
             "tags": ["romance", "contemporary-romance", "new-adult", "ya-romance"]},
            {"id": "rom-com", "label": "Rom-coms & chick lit",
             "tags": ["chick-lit", "romantic-comedy", "rom-com"]},
            {"id": "historical-romance", "label": "Historical romance",
             "tags": ["historical-romance", "regency", "regency-romance"]},
            {"id": "paranormal-romance", "label": "Paranormal romance",
             "tags": ["paranormal-romance", "pnr", "fantasy-romance"]},
            {"id": "steamy", "label": "Steamy",
             "tags": ["erotica", "erotic", "steamy", "erotic-romance", "bdsm"]},
        ],
    },
    {
        "id": "true-stories", "label": "True stories",
        "blurb": "Memoir, history, science & big ideas",
        "shelves": [
            {"id": "memoir", "label": "Memoir & biography", "requires": "nonfiction",
             "tags": ["memoir", "memoirs", "biography", "biographies", "autobiography"]},
            {"id": "history", "label": "History", "requires": "nonfiction",
             "tags": ["history", "world-history", "american-history", "military-history"]},
            {"id": "science", "label": "Science & nature", "requires": "nonfiction",
             "tags": ["science", "popular-science", "physics", "biology", "nature", "math",
                      "astronomy", "evolution"]},
            {"id": "self-help", "label": "Self-help & business", "requires": "nonfiction",
             "tags": ["self-help", "self-improvement", "personal-development", "productivity",
                      "business", "leadership", "economics", "finance"]},
            {"id": "mind", "label": "Psychology & philosophy", "requires": "nonfiction",
             "tags": ["psychology", "philosophy"]},
            {"id": "faith", "label": "Faith & spirituality", "requires": "nonfiction",
             "tags": ["religion", "spirituality", "christian", "theology", "faith", "buddhism"]},
            {"id": "society", "label": "Politics & society", "requires": "nonfiction",
             "tags": ["politics", "political", "sociology", "feminism", "true-crime"]},
        ],
    },
    {
        "id": "timeless", "label": "Timeless reads",
        "blurb": "Classics, literary & historical fiction",
        "shelves": [
            {"id": "classics", "label": "Classics",
             "tags": ["classics", "classic", "classic-literature", "modern-classics"]},
            {"id": "literary-fiction", "label": "Literary fiction", "requires": "fiction",
             "tags": ["literary-fiction", "literary", "literature", "contemporary-fiction"]},
            {"id": "historical-fiction", "label": "Historical fiction", "requires": "fiction",
             "tags": ["historical-fiction", "historical"]},
            {"id": "poetry-plays", "label": "Poetry & plays",
             "tags": ["poetry", "poems", "plays", "theatre", "shakespeare"]},
            {"id": "short-stories", "label": "Short stories",
             "tags": ["short-stories", "anthology"]},
        ],
    },
    {
        "id": "young-at-heart", "label": "Young at heart",
        "blurb": "YA, children's books & graphic novels",
        "shelves": [
            {"id": "young-adult", "label": "Young adult",
             "tags": ["young-adult", "ya", "teen"]},
            {"id": "children", "label": "Children's & middle grade",
             "tags": ["children", "childrens", "middle-grade", "juvenile", "chapter-books",
                      "picture-books", "picture-book"]},
            {"id": "graphic-novels", "label": "Graphic novels & manga",
             "tags": ["graphic-novels", "graphic-novel", "comics", "manga"]},
        ],
    },
    {
        "id": "dark-eerie", "label": "Dark & eerie",
        "blurb": "Horror, ghosts & gothic chills",
        "shelves": [
            {"id": "horror", "label": "Horror", "tags": ["horror", "scary", "creepy"]},
            {"id": "gothic", "label": "Ghosts & gothic", "tags": ["gothic", "ghosts"]},
            {"id": "monsters", "label": "Vampires & monsters",
             "tags": ["vampires", "vampire", "werewolves", "zombies", "demons"]},
        ],
    },
    {
        "id": "feel-good", "label": "Feel-good",
        "blurb": "Funny, warm & uplifting",
        "shelves": [
            {"id": "funny", "label": "Funny", "tags": ["humor", "humour", "funny", "comedy"]},
            {"id": "satire", "label": "Satire", "tags": ["satire"]},
            {"id": "animals", "label": "Animal tales", "tags": ["animals", "dogs", "cats"]},
            {"id": "friendship", "label": "Friendship & family", "requires": "fiction",
             "tags": ["friendship", "family", "feel-good"]},
        ],
    },
]

# Themes: short descriptive chips ("Dragons", "World War II") shown on books
# and used to explain why two books are alike. Label, then the tags behind it.
THEMES = {
    "magic": ("Magic", ["magic"]),
    "dragons": ("Dragons", ["dragons"]),
    "vampires": ("Vampires", ["vampires", "vampire"]),
    "werewolves": ("Werewolves", ["werewolves", "shapeshifters"]),
    "witches": ("Witches", ["witches"]),
    "faeries": ("Faeries", ["fae", "faeries", "fairies"]),
    "angels-demons": ("Angels & demons", ["angels", "demons"]),
    "ghosts": ("Ghosts", ["ghosts"]),
    "zombies": ("Zombies", ["zombies"]),
    "aliens": ("Aliens", ["aliens"]),
    "space": ("Space", ["space", "space-opera"]),
    "time-travel": ("Time travel", ["time-travel"]),
    "dystopia": ("Dystopia", ["dystopian", "dystopia"]),
    "post-apocalyptic": ("Post-apocalyptic", ["post-apocalyptic", "apocalyptic"]),
    "steampunk": ("Steampunk", ["steampunk"]),
    "superheroes": ("Superheroes", ["superheroes"]),
    "mythology": ("Mythology", ["mythology"]),
    "fairy-tales": ("Fairy tales & retellings", ["fairy-tales", "fairytales", "retellings", "retelling"]),
    "royalty": ("Royalty", ["royalty"]),
    "assassins": ("Assassins", ["assassins"]),
    "pirates": ("Pirates", ["pirates"]),
    "adventure": ("Adventure", ["adventure"]),
    "survival": ("Survival", ["survival"]),
    "war": ("War", ["war"]),
    "wwii": ("World War II", ["world-war-ii", "wwii", "holocaust"]),
    "spies": ("Spies", ["espionage", "spy", "spies"]),
    "serial-killers": ("Serial killers", ["serial-killer"]),
    "detective": ("Detectives", ["detective"]),
    "psychological": ("Psychological", ["psychological", "psychological-thriller"]),
    "love-triangle": ("Love triangle", ["love-triangle"]),
    "coming-of-age": ("Coming of age", ["coming-of-age"]),
    "friendship": ("Friendship", ["friendship"]),
    "family": ("Family", ["family"]),
    "school": ("Boarding school", ["boarding-school"]),
    "college": ("College", ["college"]),
    "animals": ("Animals", ["animals", "dogs", "cats"]),
    "lgbtq": ("LGBTQ+", ["lgbt", "lgbtq", "glbt"]),
    "feminism": ("Feminism", ["feminism"]),
    "race": ("Race & civil rights", ["race", "racism", "civil-rights", "african-american"]),
    "politics": ("Politics", ["politics", "political"]),
    "religion": ("Religion", ["religion", "faith"]),
    "philosophy": ("Philosophy", ["philosophy"]),
    "art": ("Art", ["art"]),
    "music": ("Music", ["music"]),
    "sports": ("Sports", ["sports"]),
    "food": ("Food", ["food", "cooking"]),
    "travel": ("Travel", ["travel"]),
    "nature": ("Nature", ["nature"]),
    "tearjerker": ("Tearjerker", ["made-me-cry", "tear-jerker", "sad"]),
    "dark": ("Dark", ["dark"]),
    "funny": ("Funny", ["humor", "humour", "funny", "comedy"]),
    "gothic": ("Gothic", ["gothic"]),
    "regency": ("Regency", ["regency", "regency-romance"]),
    "new-york": ("New York", ["new-york"]),
    "london": ("London", ["london"]),
    "paris": ("Paris", ["paris"]),
    "india": ("India", ["india"]),
    "japan": ("Japan", ["japan"]),
    "africa": ("Africa", ["africa"]),
    "russia": ("Russia", ["russia"]),
    "american-south": ("American South", ["southern"]),
}

# Shelving that signals fiction or non-fiction, used for "requires" above.
# Personal shelves like "to-read-non-fiction" are noise for similarity but
# useful here.
NONFICTION_TAGS = ["non-fiction", "nonfiction", "non-fic", "adult-non-fiction", "to-read-non-fiction",
                   "to-read-nonfiction", "non-fiction-to-read", "memoir", "memoirs", "biography",
                   "biographies", "autobiography", "self-help", "essays", "popular-science", "true-crime"]
FICTION_TAGS = ["fiction", "novels", "novel", "adult-fiction", "general-fiction", "to-read-fiction",
                "fiction-to-read", "contemporary-fiction", "literary-fiction", "historical-fiction",
                "realistic-fiction"]

# Personal shelves ("to-read", "owned", "read-in-2015", "5-stars"...) say how a
# reader organises their books, not what the book is like. They're dropped
# before comparing books by their tags.
NOISE_TAG = re.compile(
    r"^(to-|books-|currently|want|wish|need-to|books-to|own|i-own|my-|mine|in-my|on-my|on-|"
    r"read-|re-?read|to-re|favou?r|fav|all-time|best-|top-|shelfari|goodreads|"
    r"kindle|nook|e-?books?|audio|audible|listened|library|calibre|overdrive|"
    r"paperback|hardcover|signed|bought|purchased|borrowed|scanned|netgalley|arc$|"
    r"dnf|did-not|didn-t|couldn-t|gave-up|never-finished|unfinished|abandoned|unread|"
    r"finished|maybe|meh|default|books?$|bookshelf|book-?club|book-group|reviewed|"
    r"recommended|must-read|general$|other$|have$|loved$|faves$|tbr$|tbr-|english$|"
    r"first-reads|stars?$|series$|part-of-a-series|favorite-series|finished-series|"
    r"reading-challenge|challenge|1001|home-|personal-|shelf|wishlist|library-|school-books|"
    r"read$|reads$|novels?$|fiction$|adult$)"
    r"|\d"
)


def is_noise(tag: str) -> bool:
    return bool(NOISE_TAG.search(tag))


ALL_SHELVES = [dict(shelf, mood=mood["id"]) for mood in MOODS for shelf in mood["shelves"]]

# Many readers file fantasy and science fiction together ("sci-fi-fantasy"), so
# each gets some of the other's evidence. A shelf here is dropped when it's
# under half as strong as its strongest rival on the same book.
RIVALS = {
    "science-fiction": ["fantasy", "epic-fantasy"],
    "fantasy": ["science-fiction"],
    "epic-fantasy": ["science-fiction"],
}


# ---------------------------------------------------------------- Open Library
# Newer books (2018 on) come from Open Library, which describes books with
# free-text subjects ("Fiction, romance, contemporary", "Dragons", "enemies to
# lovers") instead of reader tags. These patterns (matched against lower-cased
# subjects) put them on the same shelves and themes as the Goodreads books.
# Shelves with a "requires" above keep that rule here too.

OL_SHELF_PATTERNS = {
    "epic-fantasy": r"epic fantasy|high fantasy|fantasy, epic|sword and sorcery|imaginary wars and battles",
    "fantasy": r"\bfantasy\b|romantasy|\bmagic\b|wizards",
    "urban-paranormal": r"urban fantasy|paranormal|supernatural|vampire|werewol|shapeshift|witches|demons|\bfae\b|faerie",
    "science-fiction": r"science fiction|sci-fi|space opera|space warfare|cyberpunk|extraterrestrial|time travel",
    "dystopian": r"dystopia|apocalyp|zombie",
    "mystery": r"myster|detective|whodunit|private investigators",
    "cozy-mystery": r"\bcozy\b",
    "thriller": r"thriller|(?<!romantic )suspense",
    "crime": r"\bcrime\b|police|serial murder|serial killer|\bnoir\b",
    "spy": r"espionage|\bspies\b|\bspy\b|intelligence service",
    "romance": r"romance|love stories|man-woman relationships",
    "rom-com": r"romantic comed|rom-com|chick lit",
    "historical-romance": r"historical romance|romance, historical|regency",
    "paranormal-romance": r"paranormal romance|fantasy romance|romantasy|romance, paranormal|romance, fantasy",
    "steamy": r"erotic|\bsmut\b|spicy|steamy|dark romance|bdsm",
    "memoir": r"biograph|memoir|autobiograph",
    "history": r"\bhistory\b",
    "science": r"\bscience\b(?! fiction)|physics|biology|mathematics|astronomy|evolution|neuroscience|medicine|climat",
    "self-help": r"self-help|self help|personal development|self-actualization|self-realization|success|"
                 r"productivity|\bhabits?\b|business|leadership|management|economics|finance|money|wealth|"
                 r"\binvest(ing|ment|ments|ors?)?\b|motivation",
    "mind": r"psycholog|philosoph|mindfulness|emotional intelligence|mental health",
    "faith": r"religio|spiritual|christian|theology|bible|buddhis",
    "society": r"politic|sociology|social aspects|social justice|feminis|race relations|racism|true crime|"
               r"current events|\bcrime\b|\bmurder|homicide",
    "classics": r"\bclassics?\b",
    "literary-fiction": r"literary",
    "historical-fiction": r"historical fiction|fiction, historical|fiction / historical",
    "poetry-plays": r"poetry|\bpoems\b|\bplays\b",
    "short-stories": r"short stories",
    "young-adult": r"young adult|\bteen|\bya\b",
    "children": r"children's|childrens\b|juvenile (fiction|literature|nonfiction)|^juvenile|picture books?|middle grade|chapter books?",
    "graphic-novels": r"graphic novel|comic|manga",
    "horror": r"horror|scary|creepy|haunted",
    "gothic": r"gothic|\bghost",
    "monsters": r"vampire|werewol|zombie|monsters",
    "funny": r"humor|humour|humorous|funny|comedy|comedic",
    "satire": r"satir",
    "animals": r"\banimals\b|\bdogs\b|\bcats\b|\bpets\b",
    "friendship": r"friendship|families|family life",
}

OL_THEME_PATTERNS = {
    "magic": r"\bmagic|wizards|sorcer",
    "dragons": r"dragon",
    "vampires": r"vampire",
    "werewolves": r"werewol|shapeshift",
    "witches": r"witch",
    "faeries": r"\bfae\b|faerie|fairies",
    "angels-demons": r"\bangels\b|demons",
    "ghosts": r"\bghost|haunted",
    "zombies": r"zombie",
    "aliens": r"extraterrestrial|\baliens?\b",
    "space": r"outer space|space opera|space warfare|interstellar|astronaut",
    "time-travel": r"time travel",
    "dystopia": r"dystopia",
    "post-apocalyptic": r"apocalyp",
    "steampunk": r"steampunk",
    "superheroes": r"superhero",
    "mythology": r"mytholog|\bmyths?\b",
    "fairy-tales": r"fairy tale|retelling|folklore",
    "royalty": r"kings and rulers|princes|queens|royal",
    "assassins": r"assassin",
    "pirates": r"pirate",
    "adventure": r"adventure",
    "survival": r"survival",
    "war": r"\bwar\b|\bwars\b|military|soldiers|battles",
    "wwii": r"world war, 1939-1945|world war ii|wwii|holocaust",
    "spies": r"espionage|\bspies\b|\bspy\b",
    "serial-killers": r"serial murder|serial killer",
    "detective": r"detective|private investigator",
    "psychological": r"psychological",
    "love-triangle": r"love triangle",
    "coming-of-age": r"coming of age|bildungsroman",
    "friendship": r"friendship",
    "family": r"famil",
    "school": r"boarding school",
    "college": r"college|universit",
    "animals": r"\banimals\b|\bdogs\b|\bcats\b",
    "lgbtq": r"lgbt|\bgay\b|lesbian|queer|bisexual|transgender",
    "feminism": r"feminis",
    "race": r"race relations|racism|african american|civil rights",
    "politics": r"politic",
    "religion": r"religio|christian|\bfaith\b",
    "philosophy": r"philosoph",
    "art": r"\bart\b|artists|painting",
    "music": r"music",
    "sports": r"sports|football|hockey|basketball|baseball|soccer",
    "food": r"\bfood|cooking|cookbook|baking",
    "travel": r"(?<!time )\btravel",
    "nature": r"\bnature\b|environment",
    "tearjerker": r"\bgrief|tearjerker",
    "dark": r"\bdark\b",
    "funny": r"humor|humour|humorous|funny|comedy",
    "gothic": r"gothic",
    "regency": r"regency",
    "new-york": r"new york(?! times)",
    "london": r"london",
    "paris": r"\bparis\b",
    "india": r"\bindia\b",
    "japan": r"japan",
    "africa": r"africa",
    "russia": r"russia",
    "american-south": r"southern states",
}

# Fiction versus non-fiction, from the subjects.
OL_FICTION = r"(?<!non-)\bfiction\b|\bnovels?\b|romance|fantasy|thriller|myster"
OL_NONFICTION = (r"nonfiction|non-fiction|biograph|memoir|autobiograph|\bhistory\b|self-help|"
                 r"psycholog|philosoph|business|economics|finance|science(?! fiction)|politic|religio")

# Not single readable books: box sets, study aids, stationery.
OL_EXCLUDE_TITLE = (r"\b(box(ed)? set|boxset|collection set|trilogy set|summary of|study guide|workbook|"
                    r"journal|coloring|colouring|planner|notebook|sticker|puzzles?|activity book|calendar)\b| / ")
OL_EXCLUDE_SUBJECT = r"^book_set$|^anthologie$"

# New York Times bestseller lists (subjects like "nyt:young-adult-hardcover=2019-09-22")
# give a genre when the other subjects don't.
OL_NYT_SHELVES = {
    r"young-adult": "young-adult",
    r"picture-books|childrens|middle-grade|chapter-books|series-books": "children",
    r"graphic-books|manga": "graphic-novels",
    r"advice|business|relationships|money": "self-help",
    r"^science$": "science",
    r"religion|spirituality": "faith",
    r"political|politics": "society",
    r"celebrities": "memoir",
    r"humor": "funny",
}
