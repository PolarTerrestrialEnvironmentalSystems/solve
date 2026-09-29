import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import parse_directory as batch
import run_parsing_tests as runner
from blablador_client import BlabladorClient, Clock, Response, Settings
from common import read_json, read_jsonl
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


class FakeClock(Clock):
    elapsed = 0
    def now(self): return datetime(2026,9,25,tzinfo=timezone.utc)+timedelta(seconds=self.elapsed)
    def monotonic(self): return self.elapsed
    def sleep(self,seconds): self.elapsed += max(seconds,0.00001)


def pdf(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    writer=PdfWriter();page=writer.add_blank_page(595,842)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 750 Td (Der Abfluss sank.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for n in ['ontologie_fein.json','Event_headers.xlsx','relationship_headers.xlsx','requirements.lock.txt']:
            shutil.copyfile(ROOT/n,self.root/n)
        pdf(self.root/'input/a.pdf');pdf(self.root/'input/b.PDF')
        self.calls=[];self.clock=FakeClock();self.gets=0;self.change_model=False;self.fail_post=None
        self.env=patch.dict(os.environ,{'BLABLADOR_API_KEY':'fake-test-key','BLABLADOR_MODEL':'mock'})
        self.env.start()
        self.patches=[patch.object(batch,'BASE_DIR',self.root),patch.object(runner,'BASE_DIR',self.root),patch.object(runner,'BlabladorClient',self.client)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.env.stop();self.temp.cleanup()

    def client(self,settings,logs,ledger,run_id):
        return BlabladorClient(settings,logs,ledger,run_id,self.transport,self.clock)

    def transport(self,method,url,headers,body,timeout):
        self.calls.append(method)
        if method=='GET':
            self.gets+=1
            root='other' if self.change_model and self.gets>1 else 'root'
            data={'data':[{'id':'mock','root':root}]}
        else:
            if self.fail_post==self.calls.count('POST'):raise RuntimeError('simulated interruption')
            primary=json.loads(body['messages'][1]['content'])['primary_blocks'][0]
            event={'event_id':'e1','record_type':'Prozess','statement':'Der Abfluss sank.',
                   'ontology_concept':'core:Process','assertion_mode':'reported','polarity':'affirmed',
                   'categories':['topic:hydrology.level_flow'],
                   'evidence':[{'quote':'Der Abfluss sank.','block_id':primary['block_id'],'page':primary['page'],'field_names':['statement']}]}
            relationship={'relationship_id':'r1','source_event_id':'e1','target_event_id':'e1',
                          'relationship_type':'causes','statement':'Der Abfluss sank.',
                          'relationship_scope':'general_relationship','assertion_mode':'reported',
                          'polarity':'affirmed','categories':event['categories'],'evidence':event['evidence']}
            data={'model':'root','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'events':[event],'relationships':[relationship]})}}]}
        return Response(200,{},json.dumps(data).encode())

    def run_batch(self,*extra):
        with contextlib.redirect_stdout(io.StringIO()):
            return batch.main(['--output','results/test',*extra])

    def test_two_pdfs_fine_only_outputs_and_idempotent_resume(self):
        self.assertEqual(self.run_batch(),0)
        m=read_json(self.root/'results/test/batch_manifest.json')
        self.assertEqual(m['totals']['events'],2)
        all_events=read_jsonl(self.root/'results/test/combined/events.jsonl')
        self.assertEqual(len({e['event_id'] for e in all_events}),2)
        all_relations=read_jsonl(self.root/'results/test/combined/relationships.jsonl')
        self.assertEqual(len({x['relationship_id'] for x in all_relations}),2)
        ids={e['event_id'] for e in all_events}
        self.assertTrue(all(x['source_event_id'] in ids and x['target_event_id'] in ids for x in all_relations))
        self.assertTrue(all(x['status']=='complete' for x in m['documents']))
        for d in m['documents']:
            folder=self.root/'results/test'/d['document_key']
            self.assertTrue((folder/'fein/events.xlsx').exists());self.assertFalse((folder/'grob').exists())
        saved=(self.root/'results/test/combined/events.jsonl').read_bytes();calls=len(self.calls)
        self.assertEqual(self.run_batch('--resume'),0)
        self.assertEqual(len(self.calls),calls)
        self.assertEqual(saved,(self.root/'results/test/combined/events.jsonl').read_bytes())
        self.assertNotIn('fake-test-key',(self.root/'results/test/batch_manifest.json').read_text())
        rows=read_jsonl(self.root/'results/test/logs/api_requests.jsonl')
        times=[datetime.fromisoformat(x['started_at_utc']).timestamp() for x in rows if x['event']=='request_started']
        self.assertTrue(all(b-a>=2 for a,b in zip(times,times[1:])))

    def test_inventory_change_rejected_on_resume(self):
        self.run_batch('--dry-run');pdf(self.root/'input/c.pdf')
        with self.assertRaisesRegex(ValueError,'PDF-Bestand'):self.run_batch('--dry-run','--resume')

    def test_changed_pdf_rejected_on_resume(self):
        self.run_batch('--dry-run')
        with (self.root/'input/a.pdf').open('ab') as f:f.write(b'\n% changed')
        with self.assertRaisesRegex(ValueError,'PDF-Bestand'):self.run_batch('--dry-run','--resume')

    def test_settings_change_rejected_on_resume(self):
        self.run_batch('--dry-run')
        with self.assertRaisesRegex(ValueError,'Einstellungen'):self.run_batch('--dry-run','--resume','--workers','2')

    def test_modified_export_rejected_on_resume(self):
        self.run_batch()
        m=read_json(self.root/'results/test/batch_manifest.json')
        path=self.root/'results/test'/m['documents'][0]['document_key']/'fein/events.jsonl'
        path.write_text('',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Ergebnis fehlt oder wurde geändert'):self.run_batch('--resume')

    def test_dependency_mismatch_rejected_before_network(self):
        with patch.object(batch.importlib.metadata,'version',return_value='0.0.0'):
            with self.assertRaisesRegex(ValueError,'benötigt'):self.run_batch()
        self.assertEqual(self.calls,[])

    def test_empty_input_has_actionable_error(self):
        (self.root/'empty').mkdir()
        with self.assertRaisesRegex(ValueError,'Keine PDF'):batch.discover(self.root/'empty',False)

    def test_interrupted_second_pdf_resumes_without_repeating_first(self):
        self.fail_post=2
        with self.assertRaisesRegex(RuntimeError,'simulated'):self.run_batch()
        self.fail_post=None
        self.assertEqual(self.run_batch('--resume'),0)
        self.assertEqual(self.calls.count('POST'),3)
        self.assertEqual(read_json(self.root/'results/test/batch_manifest.json')['totals']['events'],2)

    def test_model_change_between_pdfs_stops_before_second_post(self):
        self.change_model=True
        with self.assertRaisesRegex(ValueError,'Modellbasis'):self.run_batch()
        self.assertEqual(self.calls.count('POST'),1)

    def test_recursive_duplicate_filenames_have_distinct_keys(self):
        pdf(self.root/'input/sub/a.pdf')
        entries=batch.discover(self.root/'input',True)
        self.assertEqual(len(entries),3)
        self.assertEqual(len({x['document_key'] for x in entries}),3)
        self.assertEqual(len(batch.discover(self.root/'input',False)),2)

    def test_dry_run_no_key_no_network(self):
        with patch.dict(os.environ,{'BLABLADOR_API_KEY':''}):self.assertEqual(self.run_batch('--dry-run'),0)
        self.assertEqual(self.calls,[])

    def test_fine_only_cli_rejects_other_ontology(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            runner.parser().parse_args(['--ontology','grob'])

    def test_four_concurrent_calls_and_shared_thirty_rpm(self):
        barrier=threading.Barrier(4)
        def transport(*args):
            barrier.wait(timeout=10)
            return Response(200,{},b'{"model":"mock"}')
        client=BlabladorClient(Settings(api_key='test',rpm=30),self.root/'logs',self.root/'ledger','test',transport,self.clock)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:client.complete([],{}),range(36)))
        client.close()
        rows=read_jsonl(client.log_path);starts=[x for x in rows if x['event']=='request_started']
        ends=[x for x in rows if x['event']=='request_finished']
        self.assertEqual({x['request_id'] for x in starts},{x['request_id'] for x in ends})
        self.assertLessEqual(max(x['requests_last_60s'] for x in starts),30)
        times=[datetime.fromisoformat(x['started_at_utc']).timestamp() for x in starts]
        self.assertTrue(all(b-a>=2 for a,b in zip(times,times[1:])))


if __name__=='__main__':unittest.main()
