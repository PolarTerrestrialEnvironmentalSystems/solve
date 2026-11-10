"""Exact catalogue mapping, HTTP content checks and resume; no live requests."""
import tempfile
import unittest
import contextlib
import io
import json
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import prepare_official_elbe_seeds as official


class Object(dict):
    def get_object(self):
        return self


class Page(Object):
    def extract_text(self):
        return 'Spree-001 DERW_DEBB582_001'


def body(code='DERW_DEBB582_001', category='RW', state='BB'):
    return dict(waterbody_id=code, name_original='Spree', wfd_category=category,
                waterbody_type='river' if category == 'RW' else 'lake', reporting_state=state)


class CatalogueTests(unittest.TestCase):
    def test_interrupt_cancels_queued_checks_and_exports_completed_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            inventory = folder / 'inventory.json'
            inventory.write_text(json.dumps([body('a1'), body('a2')]), encoding='utf-8')
            done, pending = Future(), Future()
            done.set_result(dict(waterbody_id='a1', accepted=[], rejected=[], reporting_state='BB', waterbody_type='river'))
            candidates = {code: [dict(url='https://example.org/' + code + '.pdf')] for code in ('a1', 'a2')}
            with patch.object(official, 'acquire_candidates', return_value=candidates), \
                    patch.object(official, 'ThreadPoolExecutor') as pool, \
                    patch.object(official, 'as_completed', side_effect=KeyboardInterrupt), \
                    contextlib.redirect_stdout(io.StringIO()):
                pool.return_value.__enter__.return_value.submit.side_effect = [done, pending]
                code = official.main(['--inventory', str(inventory), '--output', str(folder / 'result')])
            self.assertEqual(code, 130)
            self.assertTrue(pending.cancelled())
            self.assertTrue(json.loads((folder / 'result/summary.json').read_text())['interrupted'])
            self.assertEqual(json.loads((folder / 'result/official_seed_manifest.json').read_text())[0]['waterbody_id'], 'a1')

    def test_interleaved_queue_keeps_every_waterbody_once(self):
        records = [body('a1'), body('a2'), body('b1'), body('b2'), body('b3')]
        candidates = {w['waterbody_id']: [dict(url='https://' + w['waterbody_id'][0] + '.example/x')] for w in records}
        result = official.interleave_hosts(records, candidates)
        self.assertEqual([w['waterbody_id'] for w in result], ['a1', 'b1', 'a2', 'b2', 'b3'])

    def test_bb_requires_code_on_page_and_in_actual_link(self):
        links = ['https://mluk.brandenburg.de/w/Steckbriefe/WRRL2021/RWBODY/DERW_DEBB582_001.pdf',
                 'https://mluk.brandenburg.de/w/Steckbriefe/WRRL2021/RWBODY/DERW_DEBB582_002.pdf']
        page = Page({'/Annots': [Object({'/A': Object({'/URI': url})}) for url in links]})
        with patch('pypdf.PdfReader') as reader:
            reader.return_value.pages = [page]
            result = official.bb_candidates(b'fixture', [body(), body('DERW_DEBB582_002')])
        self.assertEqual(set(result), {'DERW_DEBB582_001'})
        candidate = result['DERW_DEBB582_001'][0]
        self.assertEqual(candidate['catalogue_url'], links[0])
        self.assertEqual(candidate['url'], links[0].replace('mluk.brandenburg.de', 'mleuv-daten.brandenburg.de'))
        self.assertEqual(candidate['catalogue_page'], 1)

    def test_sn_uses_exact_namespace_id_category_and_url_field(self):
        features = [dict(attributes=dict(OBJECTID=1, OWK_ID='DESN_582-2', GEW_ART='Fließgewässer',
                    OWK_NAME='Spree-2', STECKBRIEF='https://example.org/opaque-document?id=27')),
                    dict(attributes=dict(OBJECTID=2, OWK_ID='DESN_582-20', GEW_ART='RW',
                    STECKBRIEF='https://example.org/wrong.pdf'))]
        records = [body('DERW_DESN_582-2', state='SN'), body('DELW_DESN_582-2', category='LW', state='SN')]
        result = official.sn_candidates(features, records, 1, 'https://example.org/layer')
        self.assertEqual(set(result), {'DERW_DESN_582-2'})
        self.assertEqual(result['DERW_DESN_582-2'][0]['url'], features[0]['attributes']['STECKBRIEF'])
        self.assertEqual(official.sn_candidates(features, records, 0, 'https://example.org/layer'), {})

    def test_sn_historical_uses_link_and_marks_period(self):
        features = [dict(attributes=dict(OBJECTID=1, OWK_ID='DESN_073', LINK='https://example.org/historical.pdf',
                                       PFAD='https://example.org/ignored.pdf'))]
        result = official.sn_candidates(features, [body('DELW_DESN_073', category='LW', state='SN')], 0,
                                        'https://example.org/2016', historical=True)
        candidate = result['DELW_DESN_073'][0]
        self.assertEqual(candidate['url'], 'https://example.org/historical.pdf')
        self.assertEqual(candidate['reporting_period'], '2016-2021')

    def test_official_url_spaces_are_encoded_without_decoding_encoded_slashes(self):
        url = 'https://example.org/Speicher Lohsa%2FI.pdf?name=x%2Fy'
        self.assertEqual(official.http_url(url), 'https://example.org/Speicher%20Lohsa%2FI.pdf?name=x%2Fy')

    def test_bb_limnological_uses_exact_numeric_id_and_official_host(self):
        page = b'<a href="/daten/w/seen/80001592239.pdf">Baalsee</a><a href="https://wrong.test/daten/w/seen/8000159223.pdf">x</a>'
        result = official.bb_limnological_candidates(page, [body('DELW_DEBB80001592239', category='LW'), body('DELW_DEBB8000159223', category='LW')])
        self.assertEqual(set(result), {'DELW_DEBB80001592239'})

    def test_historical_identifier_only_normalizes_documented_namespace_and_wrap(self):
        text = 'DE_LW_DEBB800015381\n723\nSenftenberger See\nDE_LW_DESN_073 Talsperre Saidenbach\n'
        wanted = {'DELW_DEBB800015381723', 'DELW_DEBB800015381', 'DELW_DESN_073', 'DELW_DESN073'}
        self.assertEqual(official.table_identifiers(text, wanted, historical=True), {'DELW_DEBB800015381723', 'DELW_DESN_073'})

    def test_national_identifier_requires_unique_category_and_complete_token(self):
        inventory = [body('DELW_DEST_MEL06OW17-00', category='LW', state='ST'),
                     body('DERW_DESN_5-1', state='SN'), body('DERW_DESN_5-11', state='SN'),
                     body('DELW_DEXX_123', category='LW'), body('DERW_DEXX_123')]
        text = 'DEST_MEL06O\nW17-00\nArendsee\nDESN_5-11 River\nDEXX_123 ambiguous\n'
        self.assertEqual(official.national_table_identifiers(text, inventory),
                         {'DELW_DEST_MEL06OW17-00', 'DERW_DESN_5-11'})
        self.assertEqual(official.national_table_identifiers('DESN_5-1\n1\nRiver', inventory), set())
        self.assertEqual(official.national_table_identifiers('DESN_5-1\n999\nRiver', inventory), set())

    def test_regional_table_requires_exact_complete_code_and_records_page(self):
        with patch('pypdf.PdfReader') as reader:
            reader.return_value.pages = [Page()]
            result = official.table_pdf_candidates(b'fixture', [body(), body('DERW_DEBB582_00')],
                'https://example.org/table.pdf', 'Official provider', 'regional_assessment_table', 'Assessment')
        self.assertEqual(set(result), {'DERW_DEBB582_001'})
        self.assertEqual(result['DERW_DEBB582_001'][0]['catalogue_pages'], [1])
        self.assertIn('DERW_DEBB582_001', result['DERW_DEBB582_001'][0]['title'])

    def test_table_cache_is_bound_to_document_and_inventory(self):
        with tempfile.TemporaryDirectory() as directory, patch('pypdf.PdfReader') as reader:
            reader.return_value.pages = [Page()]
            kwargs = dict(url='https://example.org/a.pdf', provider='Official', report_type='table', title='Table', cache_dir=directory)
            official.table_pdf_candidates(b'fixture', [body()], **kwargs)
            official.table_pdf_candidates(b'fixture', [body()], **kwargs)
            self.assertEqual(reader.call_count, 1)
            official.table_pdf_candidates(b'changed', [body()], **kwargs)
            self.assertEqual(reader.call_count, 2)

    def test_mv_uses_only_catalogue_links_and_exact_state_and_local_identifier(self):
        row = ['DEMV', 'EMEL-0100', 'Tarnitz', None, None, None, None, 'Elbe',
               '<a href="https://fis-wasser-mv.de/charts/steckbriefe/rw/rw_wk.php?fg=EMEL-0100&amp;schema=reporting_bp3">link</a>',
               '<a href="https://www.fis-wasser-mv.de/doku/EMEL-0100.pdf">pdf</a>', None]
        other = list(row)
        other[0] = 'DEBB'
        result = official.mv_river_candidates([row, other], [body('DERW_DEMV_EMEL-0100', state='MV'), body('DERW_DEMV_EMEL-010', state='MV')])
        self.assertEqual(set(result), {'DERW_DEMV_EMEL-0100'})
        self.assertEqual(len(result['DERW_DEMV_EMEL-0100']), 2)
        self.assertIn('&schema=', result['DERW_DEMV_EMEL-0100'][0]['url'])
        self.assertEqual(result['DERW_DEMV_EMEL-0100'][0]['content_kind'], 'html')

    def test_html_requires_bounded_exact_identifier_and_factsheet_not_challenge(self):
        with tempfile.TemporaryDirectory() as directory:
            client = official.Client(directory)
            record = dict(http_status=200, content_type='text/html', checked_at='fixture', url='https://example.org/report')
            good = b'<h1>WRRL-Steckbrief</h1><td>EMEL-0100</td><h2>Belastungssituation</h2>'
            with patch.object(client, 'request', return_value=(good, record)):
                self.assertTrue(client.validate_html(record['url'], 'EMEL-0100')['validation_ok'])
                self.assertFalse(client.validate_html(record['url'], 'EMEL-010')['validation_ok'])
            with patch.object(client, 'request', return_value=(good + b' CAPTCHA', record)):
                self.assertFalse(client.validate_html(record['url'], 'EMEL-0100', refresh=True)['validation_ok'])

    def test_mv_lake_catalogue_does_not_turn_map_portal_into_extra_seed(self):
        row = ['1700101', 'Lake', 'Elbe', 'Community', '<a href="https://fis-wasser-mv.de/lw.php?sg=1700101">x</a>',
               '<a href="https://fis-wasser-mv.de/kvwmap/">map</a>']
        result = official.mv_lake_candidates([row], [body('DELW_DEMV_1700101', category='LW', state='MV')])
        self.assertEqual(len(result['DELW_DEMV_1700101']), 1)
        self.assertEqual(result['DELW_DEMV_1700101'][0]['source_local_id'], '1700101')

    def test_pdf_head_can_validate_without_downloading_document(self):
        with tempfile.TemporaryDirectory() as directory:
            client = official.Client(directory)
            record = dict(http_status=200, content_type='application/pdf', checked_at='fixture', url='https://example.org/a.pdf')
            with patch.object(client, 'request', return_value=(b'', record)) as request:
                result = client.validate_pdf(record['url'])
                cached = client.validate_pdf(record['url'])
            self.assertTrue(result['validation_ok'])
            self.assertFalse(result['original_bytes_downloaded'])
            self.assertEqual(cached, result)
            request.assert_called_once()

    def test_html_challenge_is_not_a_pdf_even_with_http_200(self):
        with tempfile.TemporaryDirectory() as directory:
            client = official.Client(directory)
            record = dict(http_status=200, content_type='text/html', checked_at='fixture', url='https://example.org/a.pdf')
            with patch.object(client, 'request', side_effect=[(b'', record), (b'<html>CAPTCHA</html>', record)]):
                result = client.validate_pdf(record['url'])
            self.assertFalse(result['validation_ok'])
            self.assertEqual(result['error'], 'captcha_or_access_challenge')

    def test_unsupported_head_requires_pdf_signature_not_only_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            client = official.Client(directory)
            head = dict(http_status=405, content_type='text/html')
            sample = dict(http_status=206, content_type='application/octet-stream', checked_at='fixture')
            with patch.object(client, 'request', side_effect=[(b'', head), (b'%PDF-1.7\nfixture', sample)]):
                self.assertTrue(client.validate_pdf('https://example.org/download')['validation_ok'])

    def test_per_waterbody_resume_preserves_evidence_without_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            client = official.Client(directory)
            candidate = dict(url='https://example.org/a.pdf', title='Official source', evidence_basis='exact code',
                             source_url='https://example.org/catalogue', identity_scope='exact_wfd_waterbody')
            check = dict(url=candidate['url'], final_url=candidate['url'], http_status=200,
                         content_type='application/pdf', checked_at='fixture', validation_ok=True,
                         validation_basis='catalogue_identity_and_http_head_pdf_mime')
            with patch.object(client, 'validate_pdf', return_value=check) as request:
                first = official.validate_waterbody(client, body(), [candidate])
                second = official.validate_waterbody(client, body(), [candidate])
            request.assert_called_once()
            self.assertEqual(first, second)
            self.assertEqual(first['accepted'][0]['http']['http_status'], 200)
            self.assertTrue((Path(directory) / 'by_waterbody/DERW_DEBB582_001.json').exists())


if __name__ == '__main__':
    unittest.main()
