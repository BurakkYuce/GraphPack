# bench-wiki — MultiHop-RAG as a pack

The third vertical, and the only one whose numbers can be compared to somebody
else's.

`oss` and `tr-law` grade their own homework: both build gold out of structured
data they already hold, which makes the evaluation honest but leaves no way to
say whether the result is good. [MultiHop-RAG][paper] (Tang & Yang, 2024) ships
2,556 multi-hop queries over 609 news articles, each query naming the articles a
correct answer must rest on, and other people have published retrieval numbers
on exactly that corpus.

[paper]: https://arxiv.org/abs/2401.15391

It is also the domain least like the first two. Not packaging, not case law; and
its graph is neither metadata published about software nor citations dug out of
prose — it is bibliographic. Who published what, who wrote it, under which
topic.

## What it holds

| | |
|---|---|
| articles | 609 |
| outlets | 49 |
| bylines | 299 (68 articles carry none) |
| categories | 6 |
| queries | 2,556 |
| evidence entries | 6,084 |

Licence: ODC-BY, from `yixuantt/MultiHopRAG` on Hugging Face. Both files are
fetched whole; there is no pagination and no key.

## The gold links completely

Every one of the 6,084 evidence entries matches a corpus article by title **and**
by URL, and all 609 articles the gold names exist in the loaded graph:

```
graph articles      : 609
distinct gold articles: 609
linked              : 609  (100.0%)
gold rows linked    : 6084/6084
```

That is not luck, it is the `url_key` normaliser: the corpus and the evidence
lists spell the same article's URL with different schemes and trailing slashes,
and matching on the raw string would have silently lost rows. A benchmark whose
ground truth points at articles the graph does not hold measures nothing, so
this is the check that runs before any number is taken.

2,255 of the 2,556 queries carry evidence. The other 301 are `null_query` — no
answer exists in the corpus — and they are the reason `gold.jsonl` has 2,255
distinct questions rather than 2,556. Their correct behaviour is to say so,
which is measured differently from retrieval and comes from `queries.jsonl`.

## No extraction, and why

This pack sets `extract: false` — the first to do so, and the one new knob the
pack contract needed to accommodate a third vertical.

Every edge it declares comes from a metadata field. There is nothing in the
article bodies for a model to find that this graph then uses, and the benchmark
measures retrieval rather than extraction. Running one anyway would cost days of
GPU on this hardware and change no number. The corpus is still chunked, embedded
and indexed for search: that is the half of the pipeline being measured.

## The dataset's outlets are sections, not publishers

`source` carries a section suffix, so one publisher appears several times:

```
The Independent - Life and Style     27
The Independent - Sports             ...
The Independent - Travel             ...
FOX News - Entertainment / Health / Lifestyle
BBC News - Technology / Entertainment & Arts
```

So "49 outlets" is really about 44 publishers, and any question about *which
outlet* inherits that. The graph keeps the dataset's own labelling rather than
merging them — merging would be a judgement about the world, and the benchmark's
numbers are computed against the labels as published.

The same suffixes are why `aliases.csv` exists here at all: a question naming
CNBC shares almost no characters with `Cnbc | World Business News Leader`, and
fuzzy matching at 88 will not reach it. Every id in that file was checked
against the loaded graph — the first version pointed at `src:verge`, which does
not exist, because the loader slugs "The Verge" to `src:the-verge` while a
question's `name_key` strips the leading article.

## What the graph adds

The benchmark's own queries are answered from the corpus. The intents in
`retrieval.yaml` are the other half — questions a graph answers and a retriever
cannot, because the answer is a fact about publication rather than a passage:

```bash
uv run graphpack ask bench-wiki "Which outlets covered technology?"
uv run graphpack ask bench-wiki "Which articles did TC publish?"
uv run graphpack viz bench-wiki --id outlets-technology -o run.html
```

| category | outlets | articles |
|---|---:|---:|
| sports | 17 | 211 |
| technology | 8 | 172 |
| entertainment | 10 | 114 |
| business | 15 | 81 |
| science | 7 | 21 |
| health | 1 | 10 |

Nine questions in `questions.jsonl` cover the four intents; all nine route and
reach their entity, and the one naming an outlet that does not exist returns
nothing rather than the nearest match.

## Running it

```bash
uv run graphpack packs validate bench-wiki
uv run graphpack backbone fetch bench-wiki     # ~12 MB, two files
uv run graphpack backbone load bench-wiki      # no model involved
uv run graphpack backbone check bench-wiki     # 5 assertions
uv run graphpack ask-all bench-wiki --no-llm
```
