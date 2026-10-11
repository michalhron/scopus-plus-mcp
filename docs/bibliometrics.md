# Bibliometrics and bibliography

<img src="assets/icon-bibliometrics.svg" width="64" height="64" align="right" alt="">

These tools measure a topic and its venues: how much is published and when,
where and at what prestige, which themes it holds and how they change, and
which journals clear a quality bar. They also produce the BibTeX for whatever
you found.

## How much, and when

`publication_counts` counts publications per year for a query. On Scopus it
sends one request per year, so `from_year` and `to_year` are required (at
most 60 years). On OpenAlex one request covers all years. The two sources
count differently. Compare trends within one source, and do not compare
totals across sources.

## Where, and at what prestige

`topic_landscape` runs a Scopus query and reports papers per broad subject
area, and then, per subject category, how many papers appear in Q1, Q2, Q3
and Q4 journals of that category, with the main journals. A journal can be
Q1 in one category and Q3 in another, so each paper counts in every category
of its journal.

Quartiles count journal papers only by default. Proceedings series such as
IFAC-PapersOnLine also carry CiteScore ranks, and they are reported
separately with book series. Large topics are analysed on a sample of up to
2,000 papers, the most recent by default or the most cited
(`sample="cited"`) to see where influential work appears. The reply states
the coverage.

`topic_profile` answers the same question for OpenAlex, by discipline. For a
title-and-abstract query it reports papers and shares by field, subfield and
topic (top 20 by default), and per year by field. It names the first year in
which each field made up at least 5% of that year's papers (`share_threshold`
changes the 5%), so you can see when a discipline joined the debate. It takes
`topics`, `scope` and the quality cut below, and counts articles, reviews,
books and book chapters unless `work_types="all"`. It uses grouped requests,
four plus one per year in the range, and never one per paper. It writes a
JSON file and a field-by-year CSV.

## Which journals clear the bar

`find_journals` lists the journals in one or more Scopus subject categories
at or above a CiteScore percentile within the category. 75 is Q1 and 90 is
the top 10%. Categories are ASJC names or codes, such as Information Systems
(1710) or Management Information Systems (1404). The reply gives each
journal's rank, percentile, quartile and CiteScore, a CSV, and a ready
`SRCID(...)` fragment to paste into a `search_all` query.

`get_journal_metrics` returns SJR, SNIP, CiteScore and CiteScore Tracker with
their years, plus subject areas, for up to 200 journals by ISSN or Scopus
source ID. With `source="openalex"` it returns OpenAlex's own measures
(2-year mean citedness, h-index, i10-index) and needs no subscription.
Journals that are not found are listed in the reply.

### A quality cut across fields

A single journal list cannot serve a corpus that spans information systems,
strategy and marketing. A top marketing journal and a top IS journal sit in
different Scopus categories with different citation norms. On OpenAlex,
`min_percentile` applies the bar inside each journal's own category.

It works in four steps. First one grouped request finds the venues the corpus
appears in. Then OpenAlex gives their ISSNs and Scopus gives their CiteScore
percentiles per category. A journal is kept when its percentile is at or above
`min_percentile`. `percentile_rule="best"` (the default) uses its best
category and is generous to multidisciplinary journals. `"all"` needs the
bar in every category the journal is listed in. `categories` restricts which
Scopus categories count. Last, the search runs again on the kept journals, in
chunks of 50.

`min_percentile=0` keeps every journal Scopus indexes and nothing else, a
floor that removes most junk venues. The reply lists journals kept and
dropped with their percentile and category, the share of the corpus the cut
removed, and every venue with no CiteScore. Those venues are listed, never
silently dropped or kept. A CSV holds all of them with their status.

Conference proceedings have no journal CiteScore, so the cut drops them, and
the reply says how many papers that was. ICIS, ECIS and HICSS matter in IS.
`include_venues` takes ISSNs or OpenAlex source IDs that bypass the cut.

The cut needs the Scopus Serial Title API. Without it the cut refuses and
says so. OpenAlex's own journal measures cannot stand in. Mean citedness
differs by field by an order of magnitude, and a fair percentile would need
every source in each journal's field. A journal list you trust, passed as
`scope`, works without Scopus.

Request cost: one OpenAlex request per 200 venues, one per 50 for their
ISSNs, one Scopus request per 25 ISSNs, then the search itself.

## Themes over time

`thematic_evolution` follows the method of Cobo et al. (2011), as in
bibliometrix's thematic map. For each period it clusters co-occurring
keywords and places each cluster in the strategic diagram by Callon
centrality and density: motor, basic, niche, or emerging and declining
themes. Between periods it reports which themes continue, split, merge,
appear or vanish.

With `construct_terms` it follows one construct through the periods: the
theme that holds it, where that theme sits, and the keywords around it. That
shows whether the construct stays central, drifts, or dissolves into another
theme. Set periods with `cut_years` or let `n_periods` split the corpus
evenly. The tool writes JSON, CSV and a PNG of the strategic diagrams.

## Bibliography

`get_bibtex` writes BibTeX for up to 200 papers by DOI, Scopus ID or
OpenAlex ID. Entries come from the publisher's metadata, errors included, so
check author names. Papers without a DOI, such as many AIS conference papers,
get a minimal entry built from Scopus or OpenAlex metadata, marked with a
note.

Parameters for every tool: [tool reference](tools.md#bibliometrics-and-bibliography).
