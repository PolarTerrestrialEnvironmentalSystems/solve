"""Focused regression tests. Fixtures are synthetic and never research results."""
import contextlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_web_enrichment as s

SENTINEL = 'PRIVATE_FULLTEXT_SENTINEL_72619'

def config(root, **overrides):
    raw = dict(context='Source collection only',
        waterbodies=[dict(id='lake',name='Example Lake',region='Northshire',
            keywords=['Northshire'],preferred_domains=['example.org'])],
        topics=[dict(name='Water quality',keywords=['phosphorus','monitoring'])],
        review=dict(mode='rules'),search=dict(provider='external'),
        limits=dict(max_fetches_per_run=0,max_searches_per_run=0,max_tasks_per_run=20,delay_seconds=0))
    raw.update(overrides)
    p=Path(root)/'config.json';p.write_text(json.dumps(raw))
    return s.load_config(p)

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
        self.cfg=config(self.root);self.r=s.Research(self.cfg,self.root/'dossier')
        self.wb=self.cfg['waterbodies'][0]
    def tearDown(self):
        self.r.close();self.t.cleanup()
    def parsed(self, text):
        return dict(title='Annual report',text=text,fragments=[dict(locator='PDF page 2',text=text)],links=[],status='ok',warnings=[])
    def decision(self,text,**fields):
        wb=dict(self.wb,**fields)
        return s.assess_rules(wb,self.cfg['topics'],self.parsed(text),dict(parent_useful=0,source_hint='',priority=90))
    def import_html(self):
        html=f'<html><title>Annual report</title><main><p>Example Lake Northshire phosphorus monitoring. {SENTINEL}</p><a href="/download?id=7">Report appendix</a></main></html>'
        p=self.root/'original.html';p.write_text(html)
        manifest=self.root/'manifest.jsonl';manifest.write_text(json.dumps(dict(url='https://example.org/report',waterbody_ids=['lake'],original_file=str(p),mime_type='text/html'))+'\n')
        self.r.import_content(manifest)
        with contextlib.redirect_stdout(io.StringIO()):self.r.run()
        return html
    def test_defaults_keep_no_text(self):
        self.assertFalse(s.keep_text(self.cfg))
    def test_async_review_requires_explicit_text_opt_in(self):
        with self.assertRaisesRegex(ValueError,'assistant'):
            config(self.root,review=dict(mode='assistant'))
    def test_pdf_magic_opaque_url_and_wrong_mime(self):
        sha,path,kind=self.r.fetcher.save_bytes(b'%PDF-1.7\nsynthetic marker', 'application/octet-stream')
        self.assertEqual(kind,'pdf');self.assertTrue(path.endswith('.pdf'))
    def test_pdf_magic_with_leading_bytes(self):
        self.assertEqual(self.r.fetcher.save_bytes(b'\xef\xbb\xbf\n%PDF-1.4\nfixture','text/plain')[2],'pdf')
    def test_html_error_is_not_pdf(self):
        self.assertEqual(self.r.fetcher.save_bytes(b'<html><body>Unavailable</body></html>','application/pdf')[2],'html')
    def test_same_original_is_stored_once(self):
        a=self.r.fetcher.save_bytes(b'%PDF-1.4\nx','application/pdf')
        b=self.r.fetcher.save_bytes(b'%PDF-1.4\nx','application/octet-stream')
        self.assertEqual(a,b);self.assertEqual(len(list((self.r.store.root/'archive/pdf').glob('*'))),1)
    def test_body_match_does_not_require_title_match(self):
        self.assertEqual(self.decision('Example Lake Northshire phosphorus')['decision'],'relevant')
    def test_ambiguous_identity_cannot_be_promoted(self):
        self.assertEqual(self.decision('Example Lake Northshire phosphorus',identity_requires_confirmation=True)['decision'],'uncertain')
    def test_required_geography_gate(self):
        self.assertEqual(self.decision('Example Lake phosphorus',require_geography=True,geography_terms=['Northshire'])['decision'],'uncertain')
    def test_partial_pdf_cannot_be_rejected_for_missing_lake(self):
        parsed=self.parsed('Other regional phosphorus measurements');parsed['status']='partial_page_limit_or_error'
        x=s.assess_rules(self.wb,self.cfg['topics'],parsed,dict(parent_useful=0,source_hint='',priority=90))
        self.assertEqual(x['decision'],'uncertain')
    def test_river_and_locality_group(self):
        wb=dict(self.wb,name='River Place',identity_groups=[['River','Place']])
        self.assertTrue(s.identity_matches(wb,'The River crosses the monitored reach near Place.'))
        self.assertFalse(s.identity_matches(wb,'A park near Place.'))
        self.assertFalse(s.identity_matches(wb,'River '+('x'*700)+' Place'))
    def test_hyphenated_water_name(self):
        self.assertTrue(s.identity_matches(dict(self.wb,name='Elbeumflut'),'Elbe-Umflutkanal'))
    def test_empty_alias_does_not_match_every_document(self):
        self.assertFalse(s.identity_matches(dict(self.wb,aliases=['']), 'Unrelated phosphorus report'))
    def test_minimal_input_gets_id_topics_and_private_defaults(self):
        import start_research
        cfg=s.load_config(start_research.prepare_input(dict(context='Historical source search',waterbodies=[dict(name='Example Lake',region='Northshire',keywords=['water quality'])])))
        self.assertTrue(cfg['waterbodies'][0]['id']);self.assertEqual(len(cfg['topics']),19)
        self.assertFalse(s.keep_text(cfg))
    def test_inline_secret_rejected_before_config_write(self):
        import start_research
        raw=start_research.prepare_input(dict(waterbodies=[dict(name='Example Lake')],search=dict(provider='brave',api_key='fake-test-key')))
        with self.assertRaisesRegex(ValueError,'Zugangsschlüssel'):s.load_config(raw)
    def test_link_without_lake_name_from_useful_parent(self):
        link=dict(url='https://example.org/download?id=17',label='Appendix methods',context='')
        self.assertGreaterEqual(s.link_priority(self.wb,self.cfg['topics'],link,True),28)
    def test_embedded_and_metadata_links(self):
        p=s.PageParser();p.feed('<title>Report</title><meta name="citation_pdf_url" content="/get/1"><object data="/get/2"></object><script type="application/ld+json">{"contentUrl":"/get/3"}</script>')
        self.assertEqual({x['url'] for x in p.result('https://example.org/',99)['links']},{f'https://example.org/get/{n}' for n in range(1,4)})
    def test_spam_search_forms_deferred_not_opaque_documents(self):
        self.assertEqual(s.search_hit_priority(dict(url='https://example.org/search/?q=unrelated',title='Search'),self.wb,0),5)
        self.assertGreaterEqual(s.search_hit_priority(dict(url='https://example.org/get?id=234',title='Monitoring report'),self.wb,0),28)
    def test_pipeline_retains_original_but_no_extracted_text(self):
        html=self.import_html()
        root=self.r.store.root
        self.assertEqual(len(list((root/'archive/html').glob('*.html'))),1)
        self.assertFalse((root/'extracted').exists())
        self.r.store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        for p in root.rglob('*'):
            if p.is_file() and 'archive' not in p.parts:
                self.assertNotIn(SENTINEL.encode(),p.read_bytes(),str(p))
        rows=list(s.read_jsonl(root/'results/alle_quellen_inkl_verworfene.jsonl'))
        archived=[x for x in rows if x['original_bytes']]
        self.assertEqual(archived[0]['decision'],'relevant')
        self.assertEqual(archived[0]['group_basis'],'original_bytes')
        self.assertIsNone(archived[0]['extracted_file'])
        self.assertTrue(list(s.read_jsonl(root/'results/lake/02_seiteninhalte.jsonl')))
    def test_repeated_run_does_not_duplicate_originals(self):
        self.import_html()
        with contextlib.redirect_stdout(io.StringIO()):self.r.run()
        self.assertEqual(self.r.store.one("SELECT COUNT(*) n FROM urls WHERE status='ok'")['n'],1)
    def test_search_import_discards_supplied_excerpt(self):
        self.r.plan();row=self.r.store.one('SELECT * FROM searches LIMIT 1')
        self.r.search_result(row,[dict(url='https://example.org/a',title='Annual report',snippet=SENTINEL)])
        self.assertNotIn(SENTINEL,self.r.store.one('SELECT results FROM searches WHERE id=?',(row['id'],))['results'])
    def test_search_service_error_is_retry_not_zero_results(self):
        self.r.plan();row=self.r.store.one('SELECT * FROM searches LIMIT 1')
        self.r.search_result(row,[],error='503 service unavailable')
        result=self.r.store.one('SELECT * FROM searches WHERE id=?',(row['id'],))
        self.assertEqual(result['status'],'retry');self.assertIsNone(result['result_count'])
    def test_external_text_import_rejected_in_originals_only(self):
        p=self.root/'bad.jsonl';p.write_text(json.dumps(dict(url='https://example.org',waterbody_ids=['lake'],text=SENTINEL))+'\n')
        with self.assertRaisesRegex(ValueError,'Originaldateien'):self.r.import_content(p)
    def test_existing_text_workspace_is_not_silently_called_private(self):
        p=self.root/'old';(p/'extracted').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'frühere Textauszüge'):s.Research(self.cfg,p)
    def test_legacy_external_excerpt_archive_rejected(self):
        p=self.root/'old_external';(p/'archive/external_text').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'frühere Textauszüge'):s.Research(self.cfg,p)
    def test_legacy_database_review_fragments_rejected(self):
        p=self.root/'old_db';store=s.Store(p)
        store.db.execute("INSERT INTO reviews(id,request) VALUES(?,?)",('old',json.dumps(dict(fragments=[dict(text=SENTINEL)]))))
        store.close()
        with self.assertRaisesRegex(ValueError,'frühere Textauszüge'):s.Research(self.cfg,p)
    def test_plan_uses_keywords_and_authority_and_pdf_body_search(self):
        self.r.plan();rows=self.r.store.rows('SELECT * FROM searches')
        self.assertTrue(any(x['family']=='document_body' for x in rows))
        self.assertTrue(any('site:example.org' in x['query'] for x in rows))
        self.assertTrue(any('phosphorus' in x['query'] for x in rows))
    def test_revised_plan_upgrades_existing_pending_pdf_query(self):
        p=self.r.profiles['lake'];q='"Example Lake" Northshire filetype:pdf'
        self.r.add_search(p,q,'documents','old plan',82);self.r.plan()
        x=self.r.store.one('SELECT family,priority FROM searches WHERE query=?',(q,))
        self.assertEqual(x,dict(family='document_body',priority=100))
    def test_pdf_pipeline_reads_later_pages_without_retaining_text(self):
        from reportlab.pdfgen.canvas import Canvas
        p=self.root/'generic_report.pdf';c=Canvas(str(p));c.setTitle('Regional annual report')
        c.drawString(40,780,'This is a synthetic technical report, not a research source.');c.showPage()
        c.drawString(40,780,'Example Lake Northshire phosphorus monitoring '+SENTINEL);c.save()
        m=self.root/'pdf.jsonl';m.write_text(json.dumps(dict(url='https://example.org/download?id=77',waterbody_ids=['lake'],original_file=str(p),mime_type='application/octet-stream'))+'\n')
        self.r.import_content(m)
        with contextlib.redirect_stdout(io.StringIO()):self.r.run()
        rows=list(s.read_jsonl(self.r.store.root/'results/lake/01_pdfs.jsonl'))
        self.assertEqual(rows[0]['decision'],'relevant')
        self.assertIsNone(rows[0]['text_file'])
        meta=json.loads((self.r.store.root/rows[0]['metadata_file']).read_text())
        self.assertEqual(meta['pages_parsed'],2);self.assertNotIn('text',meta)

if __name__=='__main__':unittest.main(verbosity=2)
