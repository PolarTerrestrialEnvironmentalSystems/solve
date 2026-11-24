import json,tempfile,unittest
from pathlib import Path
import download as d

class FakeClient:
    def __init__(self,truncated=False,wrong=False):self.truncated=truncated;self.wrong=wrong
    def get(self,url,kind='json'):
        if 'returnIdsOnly' in url:return {'objectIds':[1],'objectIdFieldName':'OBJECTID'},{}
        if '/query?' in url:return {'type':'FeatureCollection','exceededTransferLimit':self.truncated,'features':[{'properties':{'OBJECTID':1,'code':'wrong' if self.wrong else 'DELW_X'}}]},{}
        return {'fields':[{'name':'code'}]},{}

class Tests(unittest.TestCase):
    def test_selection(self):
        self.assertEqual(len(d.load_config(d.HERE/'lakes.json')['lakes']),30)
    def test_html_is_not_pdf(self):
        with self.assertRaises(ValueError):d.Client.validate(b'<html>error</html>','pdf')
    def test_html_is_not_zip(self):
        with self.assertRaises(Exception):d.Client.validate(b'<html>error</html>','zip')
    def test_api_error(self):
        with self.assertRaises(ValueError):d.Client.validate(b'{"error":{"code":400}}','json')
    def test_truncation(self):
        with self.assertRaises(ValueError):d.layer(FakeClient(truncated=True),'https://example.test/0','code','DELW_X')
    def test_wrong_identity(self):
        with self.assertRaises(ValueError):d.layer(FakeClient(wrong=True),'https://example.test/0','code','DELW_X')
    def test_exact_identity(self):
        data,_=d.layer(FakeClient(),'https://example.test/0','code','DELW_X')
        self.assertEqual(len(data['features']),1)
    def test_resume_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);url='https://example.test/data';folder=root/'_requests'/d.digest(url.encode());folder.mkdir(parents=True)
            body=b'{"value":1}';(folder/'body').write_bytes(body)
            d.write_json(folder/'source.json',{'url':url,'sha256':d.digest(body)})
            client=d.Client(root);self.assertEqual(client.get(url)[0],{'value':1})
            (folder/'body').write_bytes(b'changed')
            with self.assertRaises(ValueError):client.get(url)
    def test_lock_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            with d.workspace_lock(Path(directory)):
                with self.assertRaises(OSError):
                    with d.workspace_lock(Path(directory)):pass

if __name__=='__main__':unittest.main()
