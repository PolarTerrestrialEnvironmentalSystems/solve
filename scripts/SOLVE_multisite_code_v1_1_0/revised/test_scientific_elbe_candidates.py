import unittest
import tempfile
from unittest.mock import patch
import prepare_scientific_elbe_candidates as scientific


class ScientificMetadataTests(unittest.TestCase):
    def body(self, name='SCHARMUETZELSEE'):
        return {'waterbody_id':'DELW_DEBB800015828419', 'name_original':name, 'waterbody_type':'lake'}

    def test_only_title_lake_and_topic_matches(self):
        self.assertIsNotNone(scientific.title_match(self.body(), 'Phosphorus in Scharmützelsee'))
        self.assertIsNone(scientific.title_match(self.body(), 'Phosphorus in an unrelated lake'))
        self.assertIsNone(scientific.title_match(self.body(), 'Scharmützelsee: a holiday guide'))
        payload={'data':[{'id':'10.1/abc','attributes':{'creators':[{'name':'Scharmützelsee'}],
            'titles':[{'title':'Phosphorus in an unrelated lake'}], 'url':'https://example.org/work'}}]}
        candidates, report=scientific.candidates_for(self.body(),'datacite',payload,{'url':'https://api.datacite.org/dois','checked_at':'fixture'})
        self.assertEqual(candidates,[])

    def test_query_is_title_scoped_not_author_or_full_metadata(self):
        from urllib.parse import parse_qs,urlsplit
        datacite=parse_qs(urlsplit(scientific.query_for('datacite',['Lake Example'])).query)
        openalex=parse_qs(urlsplit(scientific.query_for('openalex',['Lake Example'])).query)
        self.assertEqual(datacite['query'],['titles.title:("Lake Example")'])
        self.assertIn('search.title',openalex)
        self.assertNotIn('search',openalex)

    def test_doi_title_and_url_duplicates_do_not_inflate_count(self):
        rows=[{'url':'https://example.org/article','doi':'10.1/one','title':'Phosphorus in Lake Example'},
              {'url':'https://example.org/mirror','doi':'https://doi.org/10.1/one','title':'Alternative metadata title'},
              {'url':'https://example.org/chapter','doi':'10.1/two','title':'Phosphorus in Lake Example'}]
        self.assertEqual(len(scientific.deduplicate(rows)),1)
        rows.append({'url':'https://example.org/other-mirror','doi':'10.1/two','title':'Another metadata title'})
        forward=scientific.deduplicate(rows)
        reverse=scientific.deduplicate(list(reversed(rows)))
        self.assertEqual(forward, reverse)
        self.assertEqual(len(forward),1)

    def test_shortened_name_stays_explicitly_unverified(self):
        body={'waterbody_id':'DELW_TEST','name_original':'GROSSER STECHLINSEE','waterbody_type':'lake'}
        hint=scientific.title_match(body,'Lake Stechlin sediment monitoring')
        self.assertTrue(hint['shortened_discovery_name'])
        self.assertEqual(hint['identity_scope'],'title_name_match_requires_source_confirmation')

    def test_offline_cache_miss_never_requests_network(self):
        with tempfile.TemporaryDirectory() as root, patch.object(scientific,'urlopen',side_effect=AssertionError('Network forbidden')):
            client=scientific.MetadataClient(root,offline=True)
            payload, trace=client.get('datacite','https://api.datacite.org/dois?query=fixture')
            self.assertIsNone(payload)
            self.assertEqual(trace['status'],'not_cached')
            self.assertFalse(client.calls)


if __name__=='__main__':
    unittest.main()
