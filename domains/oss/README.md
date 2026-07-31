# oss — Python packaging ecosystem

The first of two deliberately dissimilar packs. Its backbone arrives ready-made
from published metadata, entity resolution is close to exact because package
names *are* canonical identifiers, and the corpus is English. `tr-law` is the
opposite on all three counts, which is what makes the pair a test of the pack
abstraction rather than a demonstration of one domain.

## Building it

```bash
graphpack backbone fetch oss     # ~3 minutes, 1001 requests, no credentials
graphpack backbone load oss      # ~10 seconds
graphpack backbone check oss     # sanity queries
```

Fetched payloads are gitignored; `data/MANIFEST.txt` records line counts and
SHA-256 digests so the numbers below can be traced to specific inputs.

## Sources

| What | Where | Why |
|---|---|---|
| Package ranking | [`hugovk/top-pypi-packages`](https://github.com/hugovk/top-pypi-packages) | download-ranked, refreshed monthly. Only the *ranking* comes from here. |
| Package facts | [PyPI JSON API](https://docs.pypi.org/api/json/) | name, version, licence, dependencies, project URLs |

**Not deps.dev/BigQuery**, which the plan named first. That dataset needs a
billed Google Cloud project, and the cost guard was supposed to be a `--dry_run`
byte count before spending anything. In the event the guard fired earlier and
harder — no `gcloud`, no billing account — so the question became whether the
data was reachable another way. It was: these two sources give the same facts
for the top 1000 packages, cost nothing, need no credentials, and can be re-run
by anyone reading this file. The tradeoff is reach: deps.dev covers every
package and several ecosystems, while this covers a download-ranked slice of
one.

## What gets built

Measured on the run recorded in `data/MANIFEST.txt`:

| Nodes | | Relationships | |
|---|---:|---|---:|
| `Package` | 1,000 | `DEPENDS_ON` | 2,437 |
| `Release` | 1,000 | `HAS_RELEASE` | 1,000 |
| `Repository` | 779 | `HOSTED_IN` | 961 |

Reference queries, verified by hand against PyPI:

- **`urllib3` direct dependents: 25** — `requests`, `botocore`, `kubernetes`,
  `selenium`, `sentry-sdk`, `docker`, `twine` and others. Cross-checked against
  the raw records independently of the graph; the two agree exactly.
- **Within two hops: 99 packages.**
- **Most depended upon:** `typing-extensions` (145), `packaging` (76),
  `requests` (74), `protobuf` (69), `google-auth` (57).

## Decisions worth knowing

**Optional extras are excluded.** A requirement carrying an `extra ==` marker is
only installed when someone asks for that extra. The question this backbone
exists to answer is "if this package broke, what breaks with it", and an
optional extra does not. Keeping them would roughly double the edge count with
relationships that mostly do not hold at runtime.

**Edges to packages outside the slice are dropped, not invented.** 146
dependencies name something outside the top 1000. Creating bare nodes for them
would make the graph look more complete while making it useless for evaluation:
a missing edge could no longer be read as a real absence.

**Repositories are found by searching every project URL**, not by reading chosen
key names. Publishers file the repository under `Source`, `source`,
`Source Code`, `Repository`, `Homepage`, `Home`, `Code` and more; the first
version of this pack enumerated three of those keys and found repositories for
490 of 1,000 packages. Normalising *every* URL to `owner/repo` and letting MERGE
collapse the duplicates finds 905. The remaining 95 are genuinely elsewhere —
GitLab, Heptapod, self-hosted.

**113 packages have no dependency edge in either direction.** Expected: a
download-ranked slice includes leaf libraries with no dependencies whose own
dependents are outside the slice.
