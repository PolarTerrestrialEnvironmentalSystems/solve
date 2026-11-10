"""Prepared official PDF page evidence: real synthetic PDFs, no live network."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import run_web_enrichment as core
from test_collector import config


def fixture_pdf(path, labels=None):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
    for label in labels or ('FIRST_LEGEND', 'SECOND_BODY_A', 'THIRD_BODY_B'):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 72 720 Td (' + label + ' monitoring data and original source context.) Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


class PreparedPdfPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'regional.pdf'
        fixture_pdf(self.pdf)
        self.cfg = config(self.root)
        self.cfg['start_links']['prepared'] = True
        self.url = 'https://example.org/regional.pdf'
        self.options = dict(max_pdf_pages=300, max_ocr_pages=0, max_links=50, ocr=False, language='deu')

    def tearDown(self):
        self.tmp.cleanup()

    def body(self, identifier='EU_A', pages=None):
        return dict(id=identifier, name='Lake A', identifiers=[identifier], aliases=[], seed_urls=[self.url],
                    seed_evidence={self.url: dict(catalogue_verified_waterbody_id=identifier,
                        source_url=self.url, catalogue_pages=[2] if pages is None else pages)})

    def worker(self, **options):
        output = self.root / 'worker.json'
        core.parse_pdf_worker(self.pdf, output, dict(self.options, **options))
        return json.loads(output.read_text(encoding='utf-8'))

    def test_selected_pages_keep_original_locators_and_do_not_modify_original(self):
        before = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        result = self.worker(page_selection=[1, 3])
        self.assertEqual([f['locator'] for f in result['fragments']], ['PDF page 1', 'PDF page 3'])
        self.assertNotIn('SECOND_BODY_A', result['text'])
        self.assertIn('THIRD_BODY_B', result['text'])
        self.assertEqual((result['pages_total'], result['pages_parsed']), (3, 2))
        self.assertEqual(result['status'], 'partial_selected_pages')
        self.assertTrue(result['selection_complete'])
        self.assertEqual(hashlib.sha256(self.pdf.read_bytes()).hexdigest(), before)
        metadata = core.parse_metadata(result)
        self.assertEqual(metadata['selected_pages'], [1, 3])
        self.assertEqual(metadata['parse_scope'], 'selected_pages')
        self.assertNotIn('text', metadata)

    def test_out_of_range_pages_fall_back_with_explicit_partial_warning(self):
        result = self.worker(page_selection=[1, 99])
        self.assertEqual(result['pages_parsed'], 3)
        self.assertEqual(result['status'], 'partial_invalid_page_selection')
        self.assertEqual(result['parse_scope'], 'full_document_fallback')
        self.assertFalse(result['selection_complete'])
        self.assertIn('invalid_prepared_page_selection', result['warnings'])

    def test_selected_original_page_above_300_respects_selected_page_count(self):
        fixture_pdf(self.pdf, ['FIRST_LEGEND'] + ['OTHER_BODY'] * 299 + ['TARGET_AT_PAGE_301'])
        self.cfg['limits']['max_pdf_pages'] = 2
        selection = core.prepared_pdf_selection(self.cfg, self.body(pages=[301]), self.url)
        self.assertEqual(selection['page_selection'], [1, 301])
        result = self.worker(max_pdf_pages=2, **selection)
        self.assertEqual((result['pages_total'], result['pages_parsed']), (301, 2))
        self.assertEqual(result['selected_pages'], [1, 301])
        self.assertIn('TARGET_AT_PAGE_301', result['text'])
        self.assertNotIn('OTHER_BODY', result['text'])
        self.assertEqual(result['status'], 'partial_selected_pages')

    def test_selection_requires_exact_body_document_and_prepared_mode(self):
        wb = self.body()
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wb, self.url)['page_selection'], [1, 2])
        wrong = copy.deepcopy(wb)
        wrong['seed_evidence'][self.url]['catalogue_verified_waterbody_id'] = 'EU_OTHER'
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wrong, self.url), {})
        # The catalogue page for a link is not a page of the linked document.
        wrong = copy.deepcopy(wb)
        wrong['seed_evidence'][self.url]['source_url'] = 'https://example.org/catalogue.pdf'
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wrong, self.url), {})
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wb, self.url + '?other=1'), {})
        self.cfg['start_links']['prepared'] = False
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wb, self.url), {})

    def test_separate_legend_context_is_not_claimed_as_an_identifier_match(self):
        wb = self.body(pages=[2])
        wb['seed_evidence'][self.url]['context_pages'] = [3]
        selection = core.prepared_pdf_selection(self.cfg, wb, self.url)
        self.assertEqual(selection, dict(page_selection=[1, 2, 3], evidence_pages=[2], context_pages=[1, 3]))
        result = self.worker(**selection)
        self.assertEqual(core.parse_metadata(result)['evidence_pages'], [2])
        self.assertEqual(core.parse_metadata(result)['context_pages'], [1, 3])
        wb['seed_evidence'][self.url]['context_pages'] = ['3']
        self.assertEqual(core.prepared_pdf_selection(self.cfg, wb, self.url), {'selection_warning': 'invalid_prepared_page_selection'})

    def test_invalid_page_types_do_not_silently_select_a_prefix(self):
        for pages in ([], [True], ['2'], [0], [-1], [2, '3']):
            self.assertEqual(core.prepared_pdf_selection(self.cfg, self.body(pages=pages), self.url),
                             {'selection_warning': 'invalid_prepared_page_selection'})

    def test_cache_keys_separate_body_page_sets_and_full_parse(self):
        research = core.Research(self.cfg, self.root / 'dossier')
        try:
            sha, relative, _ = research.fetcher.save_bytes(self.pdf.read_bytes(), 'application/pdf')
            source = dict(sha=sha, path=relative, url=self.url, final_url=self.url, mime='application/pdf', origin='http')
            key_a, parsed_a = core.parse_source(research.store, source, self.cfg, self.body('EU_A', [2]))
            key_b, parsed_b = core.parse_source(research.store, source, self.cfg, self.body('EU_B', [3]))
            wrong = self.body()
            wrong['seed_evidence'][self.url]['catalogue_verified_waterbody_id'] = 'WRONG'
            key_full, parsed_full = core.parse_source(research.store, source, self.cfg, wrong)
            self.assertEqual(len({key_a, key_b, key_full}), 3)
            self.assertNotIn('THIRD_BODY_B', parsed_a['text'])
            self.assertNotIn('SECOND_BODY_A', parsed_b['text'])
            self.assertEqual(parsed_full['status'], 'ok')
            self.assertEqual(parsed_full['pages_parsed'], 3)
            with patch.object(core.subprocess, 'run', side_effect=AssertionError('Cached parse must not rerun')):
                self.assertEqual(core.parse_source(research.store, source, self.cfg, self.body('EU_A', [2]))[0], key_a)
        finally:
            research.close()


if __name__ == '__main__':
    unittest.main()
