# Search, records and full text

<img src="assets/icon-search.svg" width="64" height="64" align="right" alt="">

The search tools find papers and authors, normalise their identifiers, and
read into the text where your access allows it. Large result sets go to disk
as JSON and CSV, so a later analysis can start from exactly the same records.

## Finding papers

`search_scopus` returns a handful of results for a quick look. `search_all`
pages through results for you, up to `max_results` (default 200). It moves to
cursor paging past 5,000 records, and writes anything over 50 records to JSON
and CSV.

On Scopus, both take full Scopus syntax: `TITLE-ABS-KEY(...)`, `PUBYEAR`,
`SRCID(...)`. `search_all` also runs on OpenAlex, where plain words are
matched against title and abstract and quoted phrases stay together.

`scope` restricts a search to chosen journals by ISSN. Pass a list of ISSNs,
or `basket_of_eight` (also `ais8`) for the AIS Senior Scholars' Basket of
Eight. ISSNs are used because Scopus spells journal names inconsistently.
`scope` also takes the path to a CSV file with an ISSN column: the output of
`find_journals`, a Litbaskets export, or a list you keep locally. Columns
whose header contains "issn" are read, and a cell may hold several ISSNs. On
OpenAlex, a scope of more than 50 journals runs as several requests that are
merged and de-duplicated.

### Topics and work types (OpenAlex)

With `source="openalex"`, `topics` keeps papers about OpenAlex topics,
subfields or fields. OpenAlex files every work under one of 4,516 topics,
which sit in 252 subfields and 26 fields. Name them by ID (`T10346`,
`subfield:1710`, `field:17`) or by exact name. A name that matches more than
one entity, or none exactly, is refused with the candidates listed. Several
entries combine as OR and must sit at one level. Each name costs one extra
request, and the 26 fields and 252 subfields are fetched once and cached.

`topic_match` decides what is matched. `primary` (the default) uses the one
topic OpenAlex ranks first for a paper and gives a tighter corpus. `any`
matches a paper when any of its assigned topics is in the scope, which
catches papers that sit between fields.

`work_types` defaults to articles, reviews, books and book chapters whenever
`topics` is set. Since October 2026 OpenAlex also gives topics to datasets,
specimens and proposals, and this default keeps them out. Pass `all` to keep
every type. `topics` combines with `scope` and with the quality cut as AND.
With `source="scopus"`, `topics` raises an error. Scopus subject areas and
OpenAlex topics do not map onto each other.

The reply starts with the resolved scope: topic names and IDs, level,
primary or any, and record types. `publication_counts`, `citation_network`,
`research_fronts`, `rpys`, `historiograph` and `thematic_evolution` take the
same `topics`, `topic_match` and `work_types`. `min_percentile` and its
companions (see [bibliometrics](bibliometrics.md#which-journals-clear-the-bar))
take the same route.

When results go to files, `inline` decides what comes back in the reply:
`sample` (the first 10), `compact` (every record as one line of key fields)
or `full`. Use `compact` when the assistant cannot read the server's files,
for example in a cloud session.

## Records and identifiers

`get_abstract_details` returns the full record for a Scopus ID.
`resolve_identifier` turns any one of Scopus ID, EID, DOI or PII into all of
them plus the title. Use it to get a DOI before working with OpenAlex or
Crossref.

`import_records` reads exports you already have: Scopus CSV, RIS or BibTeX,
and Web of Science plain text or tab-delimited. It merges them into one
deduplicated corpus file and looks up Scopus IDs for records without one.
Pass that file as `corpus_file` to `citation_network`, `rpys`,
`historiograph`, `research_fronts` or `thematic_evolution` to analyse exactly
those records again later.

## Citations

<img src="assets/icon-citations.svg" width="48" height="48" align="right" alt="">

`get_references` returns what a paper cites, and `get_citing_papers` returns
who cites it. Both run on Scopus or OpenAlex. `get_references` always states
how many references the paper has and whether the list was cut. With
`filter_ids` it returns only references to papers in a list you give, which
is the quick way to get the edges inside a corpus. With
`check_completeness=true` it compares the list with the count the publisher
deposited at Crossref (see [audit](audit.md#are-the-reference-lists-complete)).

To follow citations further than one step, use `citation_lineage` or
`citation_network` (see [networks](networks.md)).

## Authors

`search_authors` finds authors by name, optionally narrowed by affiliation.
On Scopus it returns author IDs, document counts, current affiliation and
name variants. On OpenAlex it adds ORCID, citation counts and h-index.
`get_author_profile` returns the Scopus profile for an author ID.

## Full text

<img src="assets/icon-fulltext.svg" width="48" height="48" align="right" alt="">

`search_fulltext` searches the body of Elsevier (ScienceDirect) journal
articles. It finds papers that use a construct in their text without naming
it in the title or abstract. With `context=true` it opens the top results
(default 10, up to 25) and reports how often each one mentions the term in
the body, separately from the reference list, where those mentions fall in
the article, and example sentences. That separates papers that engage with a
construct from papers that only cite it. It needs ScienceDirect subscriber
access and covers Elsevier content only.

`get_fulltext` retrieves one paper's full text by DOI and tries three
sources in order:

1. ScienceDirect, when your subscription allows it.
2. An open-access copy: every open location in OpenAlex, Semantic Scholar's
   open PDF, arXiv, Europe PMC, and Unpaywall or CORE when configured.
   Published versions come first, and the reply names the source and the
   version (preprint, accepted manuscript or published).
3. The Scopus abstract.

The text is written to disk and the reply carries a sample. Retrieval is for
your own non-commercial text and data mining. Do not redistribute the files.

Parameters for every tool: [tool reference](tools.md#search-and-records).
