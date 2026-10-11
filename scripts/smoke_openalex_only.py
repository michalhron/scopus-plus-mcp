"""Run every tool once with no Scopus key (OpenAlex-only check). Needs network.
Usage: env -u SCOPUS_API_KEY SCOPUS_DISABLE_SECRET_STORE=1 uv run python scripts/smoke_openalex_only.py [tool ...]
Expected: the Scopus-only tools FAIL with the missing-key message, the rest are ok."""
import asyncio, os, sys, re
from scopus_mcp import server
Q='"digital transformation"'
D1='10.1287/isre.2022.0321'
D2='10.1016/j.jsis.2019.01.003'
CASES=[
 ('search_scopus',{'query':Q,'count':3}),
 ('search_all',{'query':Q,'source':'openalex','max_results':5,'inline':'compact'}),
 ('search_fulltext',{'query':Q,'max_results':3}),
 ('get_abstract_details',{'scopus_id':D1}),
 ('resolve_identifier',{'identifier':D1}),
 ('search_authors',{'name':'Vial','source':'openalex','count':3}),
 ('get_author_profile',{'author_id':'A5023888391'}),
 ('get_fulltext',{'doi':D1}),
 ('get_references',{'scopus_id':D1,'source':'openalex','count':5}),
 ('get_citing_papers',{'scopus_id':D1,'source':'openalex','count':5}),
 ('bibliographic_coupling',{'seed_ids':[D1,D2],'source':'openalex'}),
 ('co_citation',{'seed_ids':[D1,D2],'source':'openalex','max_citing_per_seed':20}),
 ('citation_lineage',{'seed_id':D2,'source':'openalex','generations':1,'max_per_node':5}),
 ('citation_network',{'query':Q,'source':'openalex','max_results':40,'check_completeness':False,'check_retractions':False}),
 ('resolve_citers',{'seed_ids':[D1],'source':'openalex','max_results':10}),
 ('citation_context',{'citing':D1,'cited':D2}),
 ('rpys',{'query':Q,'source':'openalex','max_results':40}),
 ('historiograph',{'query':Q,'source':'openalex','max_results':40}),
 ('research_fronts',{'query':Q,'source':'openalex','max_results':40}),
 ('thematic_evolution',{'query':Q,'source':'openalex','max_results':40,'cut_years':[2020]}),
 ('check_retractions',{'dois':[D1,D2]}),
 ('index_coverage',{'seed_ids':[D1]}),
 ('publication_counts',{'query':Q,'source':'openalex','from_year':2020,'to_year':2022}),
 ('topic_landscape',{'query':Q,'max_papers':20}),
 ('get_journal_metrics',{'issns':['0742-1222'],'source':'openalex'}),
 ('find_journals',{'categories':['Information Systems']}),
 ('get_bibtex',{'identifiers':[D1]}),
 ('topic_profile',{'query':Q,'from_year':2021,'to_year':2022}),
 ('diagnose_connection',{}),
 ('get_quota_status',{}),
 ('get_server_info',{}),
]
only=set(sys.argv[1:])
async def main():
    for name,args in CASES:
        if only and name not in only: continue
        try:
            r=await server.handle_call_tool(name,args)
            t=r[0].text.replace('\n',' | ')
        except Exception as e:
            t=f'EXC {type(e).__name__}: {e}'
        bad = t.startswith('Error') or t.startswith('EXC')
        print(('FAIL ' if bad else 'ok   ')+name+': '+t[:230])
asyncio.run(main())
