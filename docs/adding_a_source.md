# Adding a new source

## 1. Decide the collection mechanism

- **Government/open dataset?** Prefer an official API/CSV/JSON/XML download
  over HTML scraping, always. Subclass in the style of
  `app/source_adapters/government_dataset_adapter.py`.
- **Company website / directory / marketplace?** Confirm robots.txt and
  ToS actually permit automated collection and your intended reuse *before*
  writing any adapter code -- see `docs/compliance.md`. Subclass in the
  style of `app/source_adapters/website_adapter.py`.

## 2. Implement a `SourceAdapter`

`app/source_adapters/base.py` defines the interface:

```python
class SourceAdapter(ABC):
    source_name: str
    source_type: str
    collector_version: str

    def fetch(self, target: str) -> FetchResult: ...
    def parse(self, fetch_result: FetchResult) -> list[ParsedRecord]: ...
    def normalize(self, record: ParsedRecord) -> list[ObservationDraft]: ...
    def validate(self, record: ParsedRecord) -> bool: ...  # optional override
```

- `fetch()`: use `ScraplingCollector.fetch_static()` for the HTTP GET
  (whether the payload is HTML, CSV, or JSON) -- see
  `app/ingestion/collectors/scrapling_collector.py`. Only use
  `fetch_dynamic()` if the source genuinely requires JS rendering.
- `parse()`: extract zero or more `ParsedRecord`s (one per real-world
  entity) from the raw payload. HTML sources use Scrapling's `Selector`
  (CSS/XPath); CSV/JSON sources use the standard library.
- `normalize()`: convert each raw field into an `ObservationDraft` with a
  standardized `normalized_value` (see `app/ingestion/normalization/`) and
  an honest `verification_type` (see `docs/confidence_engine.md` -- a
  self-reported website fact is `OBSERVED`, not `VERIFIED`).
- `validate()`: cheap sanity checks before anything is persisted (e.g. the
  government adapter checks CIN length/format).

## 3. Register the `Source` row

```python
db.add(Source(
    name="my_new_source",
    source_type="website",       # or government_dataset / directory / marketplace / public_filing
    collection_enabled=False,    # stays False until compliance review is done -- see docs/compliance.md
    rate_limit_per_minute=10,
    max_concurrency=1,
    reliability_weight=40,       # 0-100, used by the confidence engine
    license_notes="...",
    robots_notes="...",
))
```

`collection_enabled=False` by default is deliberate: a source is never
fetched until someone has actually confirmed collection is permitted (see
`app/compliance/source_policy.py`).

## 4. Wire it into the job layer

Add a branch to `_adapter_for()` in `app/ingestion/jobs/tasks.py` mapping
`source.source_type` (or `source.name`, for a bespoke per-site adapter) to
your adapter class.

## 5. Write tests

- Save a representative fixture (HTML page, CSV sample, JSON response) under
  `tests/fixtures/`.
- Test `parse()` and `normalize()` against the fixture, no network.
- Add cases for malformed/missing fields -- `validate()` should reject them,
  not let bad data reach `raw_observations`.

## 6. Do not

- Import `scrapling` anywhere outside `app/ingestion/collectors/scrapling_collector.py`.
- Write directly to a `Company` row from an adapter -- only
  `app/ingestion/pipeline.py` does that, after entity resolution + the
  confidence engine.
- Set `verification_type="verified"` unless the source is genuinely
  authoritative (a statutory registry, an official API) -- see
  `docs/confidence_engine.md`.
