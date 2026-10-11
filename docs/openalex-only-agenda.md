# OpenAlex-only operation: agenda

Written 2026-10-11 so the analysis does not have to be repeated. Branch
`feature/openalex-topics`, draft PR #28. The task list also lives in
`ROADMAP.md` ("OpenAlex-only operation"); this page holds the findings behind it
and how to resume.

## Where it stands

The server starts without a Scopus key. Scopus tools then fail with one message
that names the missing key and points to `source='openalex'`. Of 36 tools:

- **Run on OpenAlex alone (verified live, 2026-10-11):** `search_all`,
  `search_authors`, `get_references`, `get_citing_papers`,
  `bibliographic_coupling`, `co_citation`, `citation_lineage`, `citation_network`,
  `resolve_citers`, `rpys`, `historiograph`, `research_fronts`,
  `thematic_evolution`, `publication_counts`, `get_journal_metrics` (ISSNs only),
  `topic_profile`, `get_fulltext` (open access tier), `check_retractions` (DOIs),
  `coding_agreement`, `get_bibtex` (DOIs), `citation_context` (DOIs),
  `get_abstract_details`, `resolve_identifier`, `get_author_profile`,
  `get_quota_status` (now also shows the OpenAlex budget), `diagnose_connection`,
  `get_server_info`, `job_status`, `job_result`.
- **Fail without Scopus (the work left):** listed below.

## Left to do, easiest first

Each item: what is missing, the suggested route, and what to check. None of
these has been started.

### Easy
1. **Silent `source` on tools without the field.** `search_scopus`,
   `topic_landscape`, `index_coverage`, `find_journals`, `search_fulltext` ignore
   `source='openalex'`. Return an explicit error as `topic_profile` does
   (`tools/profile.py:67`). One shared helper in `tools/common.py`.
2. **`search_fulltext` under OpenAlex.** ScienceDirect only. Either refuse
   cleanly, or try OpenAlex's `fulltext.search` filter (works whose full text
   OpenAlex indexed). Check live that the filter exists and what share of a
   field it covers before promising it.
3. **Data-sources table.** One table in `docs/data-sources.md`: tool by source and
   what differs (author affiliation filter is post-hoc on the top 25 name
   matches; `citation_lineage` refuses `sort='relevancy'`; `get_journal_metrics`
   takes ISSNs only).

### Medium
4. **`topic_landscape`.** Scopus facets today (`bibliometrics.py:326`). Build the
   same view from OpenAlex `group_by` on topics and fields; `topic_profile`
   already does most of it, so this may be a thin wrapper with the Scopus output
   shape.
5. **`index_coverage`.** The Scopus REF side is unguarded
   (`transmission.py:294`), so one Scopus error aborts the call. Return the
   OpenAlex and Semantic Scholar sets and mark the Scopus column unavailable.
6. **`import_records`.** `resolve` defaults to Scopus and aborts on error
   (`imports.py:61-79`). Fall back to DOI matching and OpenAlex.
7. **Scopus-ID inputs elsewhere.** `get_bibtex`, `citation_context`,
   `path_transmission`, `check_retractions`, and the ID path of `get_references`,
   `get_citing_papers` and `citation_network` call Scopus to turn a Scopus ID into
   a DOI (`tools/common.py:54-67`). Resolve once, cache the mapping, and when
   Scopus is down skip that input with a per-item message. `citation_context`,
   `path_transmission` and `get_bibtex` have no `source` parameter yet.
8. **`diagnose_connection` for OpenAlex.** Key source is reported. Add a
   reachability and budget check (a free request, then the `x-ratelimit-*`
   headers).

### Hard (needs a decision, not just code)
9. **`find_journals`.** Selects journals by CiteScore percentile inside a Scopus
   category. OpenAlex has no field-relative percentile. Options: rank by 2-year
   mean citedness within an OpenAlex topic or subfield and label it as not
   comparable to CiteScore, or refuse. Decide which before building.
10. **`get_author_profile` by name.** Done by ID or ORCID only. Name search stays
    in `search_authors`, whose affiliation filter is weak.
11. **Quality cut without Scopus.** The field-relative cut needs Scopus Serial
    Title data. No OpenAlex substitute exists; the design decision was to refuse.
    Revisit only if a field-relative OpenAlex measure appears.
12. **`search_scopus` itself.** Scopus-specific query syntax. No OpenAlex
    equivalent; `search_all(source='openalex')` is the replacement. Leave as is.

### Checks not yet run live
- `--lanes-by-field` in `plot_main_path.py`.
- OR-cap chunking for scopes over 50 journals; `topic_match`; `work_types`.
- `set-key` on Windows and Linux.
- Budget guard: warn before a request-heavy step (quality-cut split, large
  `citation_network`) using the remaining daily budget.

## Resuming

```bash
cd ~/scopus-mcp && git checkout feature/openalex-topics && git pull
uv run pytest -q                       # offline suite
# every tool once with no Scopus key (needs network and OPENALEX_API_KEY):
env -u SCOPUS_API_KEY SCOPUS_DISABLE_SECRET_STORE=1 \
  uv run python scripts/smoke_openalex_only.py [tool ...]
# live topic tests (needs SCOPUS_API_KEY and OPENALEX_API_KEY):
uv run pytest -m integration tests/test_integration_topics.py
```

Facts worth remembering:
- OpenAlex budget: $0.10 per day anonymous, $1 with a free key. One quality cut on
  a 45,000-paper corpus costs about $0.10 and 4 minutes. The key is read from
  `OPENALEX_API_KEY`, the Keychain (`uv run scopus-plus-mcp set-key
  openalex_api_key`), or `config.json`; today it sits in the Claude Desktop
  config under the `scopus-assistant` entry.
- `group_by` on OpenAlex returns page 1 only, 200 groups at most. Anything that
  needs more must split the query (the quality cut splits by year, then month).
- The test fixtures for topics are hand-built, not recorded live.
