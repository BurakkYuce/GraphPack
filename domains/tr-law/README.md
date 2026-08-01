# tr-law — Turkish labour case law

Decisions of the 9th Civil Chamber of the Court of Cassation, 2025. The chamber
hears labour disputes: dismissal, severance, overtime, union rights.

This pack exists to try to break the abstraction. It was chosen to share as
little as possible with `oss`, and if adding it had needed a change to shared
code, the project's claim would be false. It did not.

## Building it

```bash
graphpack backbone fetch tr-law    # 18 paginated requests, ~1 minute
graphpack backbone load tr-law     # ~20 seconds
graphpack backbone check tr-law
```

Source: [`aketen0654/9_yargitay_kararlari_2025`](https://huggingface.co/datasets/aketen0654/9_yargitay_kararlari_2025),
1,578 decisions, Apache-2.0. Judgments of the Turkish courts are public.

## What makes it different

| | `oss` | `tr-law` |
|---|---|---|
| Backbone | published dependency metadata | citations read out of prose |
| Resolution | mostly exact — a package name *is* the name | fuzzy and alias — a statute is named a dozen ways |
| Language | English | Turkish, with case suffixes on the cited term |
| Gold | the index states the relation | the decision states it, in its own text |

The third column is the one that costs. Turkish attaches suffixes to whatever is
being cited, so the same statute is `Kanun`, `Kanun'un`, `Kanunda`, `Kanuna`
depending on the grammar of the sentence, and the resolver has to strip them
before anything matches. A decision introduces a statute by number and full
title once and then refers to it by abbreviation or as "the aforementioned Law"
for the rest of the judgment.

## What gets built

| Nodes | | Relationships | |
|---|---:|---|---:|
| `Decision` | 1,847 | `CITES` | 2,367 |
| `Article` | 125 | `CITES_ARTICLE` | 1,639 |
| `Statute` | 69 | `ISSUED_BY` | 1,538 |
| `Chamber` | 1 | `REFERS_TO` | 236 |
| | | `HAS_ARTICLE` | 125 |

1,628 of the decisions are the corpus itself; the other 219 are decisions cited
from within it that the slice does not contain. Unlike a package outside a
top-N slice, a cited decision is fully identified by the citation, so it is a
real node with real edges and no text.

Most cited statutes, which is the check that the patterns are catching
citations rather than numbers:

| Statute | Decisions | |
|---|---:|---|
| 6100 | 1,279 | Code of Civil Procedure |
| 696 | 307 | decree on subcontracted public workers |
| 6356 | 121 | Trade Unions and Collective Agreements |
| 5718 | 116 | Private International Law — overseas contracts |
| 2709 | 77 | the Constitution |
| 4857 | 76 | the Labour Law |

That is what a labour chamber's docket looks like.

## Decisions worth knowing

**The backbone is inferred, not published.** Every other pack property follows
from this. The citations were read out with patterns before any model ran, which
makes them deterministic and reproducible — but they are still an inference, and
a citation the patterns miss is not in the gold. Recall is therefore measured
against what the patterns caught, not against what is in the text. The
evaluation says so rather than quietly claiming otherwise.

**Statute numbers needed a digit boundary.** The first version matched
`(\d{3,4})\s*sayılı` and produced statutes numbered 7898, 8509, 9619 — above the
range Turkish statute numbers occupy. The text was
`09.06.2022 tarihli ve 137898 sayılı`, an official gazette number, and the
pattern was taking its last four digits. `checks.cypher` caught it on the first
run; nothing else would have. Statute count 98 → 69.

**A bare article reference is left unresolved.** "369. madde" belongs to
whichever statute the paragraph was discussing, and attaching it to the last one
mentioned would manufacture citations — the exact thing the evaluation exists to
detect. Only mentions naming both statute and article resolve.

**Parties and claims have no backbone.** Decisions are published pseudonymised
(`davacı`, `davalı`), and heads of relief are not enumerated anywhere. Those
mentions stay as extraction produced them, and `packs validate` says so on every
run rather than letting the omission pass for coverage.

**4.6% of decisions cite no statute at all** — 73 of 1,578. Consistent with the
5.3% measured on a sample before the pack was written. Mostly short procedural
rulings.
