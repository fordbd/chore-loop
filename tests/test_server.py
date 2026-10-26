import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from server import Store, create_app, valid_state


def chart():
    return {'version':1,'kids':[{'id':'kid1','name':'Test child','age':9,'color':'#28594a'}],
            'chores':[{'id':'c1','name':'Test chore','min':9,'assignment':'rotate','when':'weekly','day':None}],
            'checks':{}}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'chores.sqlite3'
        self.app = create_app(self.path,'family','test-only-password')

    def request(self, method='GET', path='/api/state', body=None, auth=True, headers=True):
        payload=json.dumps(body).encode() if body is not None else b''
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'wsgi.input':io.BytesIO(payload),'CONTENT_LENGTH':str(len(payload))}
        if auth:
            env['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(b'family:test-only-password').decode()
        if headers:
            env.update(CONTENT_TYPE='application/json',HTTP_X_PLANNER_REQUEST='1')
        result={}
        def start(status, response_headers):
            result.update(status=status,headers=dict(response_headers))
        raw=b''.join(self.app(env,start))
        result['body']=json.loads(raw) if result['headers']['Content-Type']=='application/json' else raw
        return result

    def save(self, revision=0, state=None):
        return self.request('PUT',body={'revision':revision,'state':state if state is not None else chart()})

    def test_authentication_and_health(self):
        self.assertTrue(self.request(auth=False)['status'].startswith('401'))
        self.assertTrue(self.request(path='/',auth=False)['status'].startswith('401'))
        self.assertEqual(self.request(path='/healthz',auth=False)['body'],{'ok':True})

    def test_empty_database_and_persistent_restart(self):
        self.assertEqual(self.request()['body'],{'revision':0,'state':None})
        self.assertEqual(self.save()['body'],{'revision':1})
        self.app=create_app(self.path,'family','test-only-password')
        self.assertEqual(self.request()['body']['state'],chart())

    def test_revisions_prevent_overwrite(self):
        self.save()
        newer=chart();newer['kids'][0]['name']='New name'
        self.assertEqual(self.save(1,newer)['body'],{'revision':2})
        self.assertTrue(self.save(1)['status'].startswith('409'))
        self.assertEqual(self.request()['body']['state']['kids'][0]['name'],'New name')

    def test_parallel_writes_only_one_succeeds(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:Store(self.path).write(0,chart()),range(2)))
        self.assertEqual(sum(r==1 for r in results),1)
        self.assertEqual(results.count(None),1)

    def test_requires_json_and_custom_header(self):
        self.assertTrue(self.request('PUT',body={'revision':0,'state':chart()},headers=False)['status'].startswith('403'))

    def test_validation_preserves_existing_state(self):
        self.save()
        for invalid in [None,[],{}, {'version':2}, {**chart(),'kids':[{}]}, {**chart(),'checks':{'bad':'yes'}}, {**chart(),'chores':[{**chart()['chores'][0],'when':'invalid'}]}]:
            response=self.request('PUT',body={'revision':1,'state':invalid})
            self.assertTrue(response['status'].startswith('400'),response)
        self.assertEqual(self.request()['body']['revision'],1)

    def test_fixed_child_and_duplicate_identity_validation(self):
        s=chart();s['chores'][0]['assignment']='child:kid1';self.assertTrue(valid_state(s))
        s['chores'][0]['assignment']='child:removed';self.assertFalse(valid_state(s))
        s=chart();s['kids'].append(deepcopy(s['kids'][0]));self.assertFalse(valid_state(s))

    def test_backup_includes_wal_commits(self):
        self.save();s=chart();s['checks']['2026-10-26|c1|0|7']=True;self.save(1,s)
        backup=Path(self.temp.name)/'backup.sqlite3';Store(self.path).backup(backup)
        self.assertEqual(Store(backup).read()['state'],s)

    def test_server_does_not_expose_database_or_code(self):
        for path in ['/server.py','/.env','/data/chores.sqlite3','/../server.py']:
            self.assertTrue(self.request(path=path)['status'].startswith('404'))

    def test_serves_planner(self):
        response=self.request(path='/');self.assertTrue(response['status'].startswith('200'))
        self.assertIn(b'async function boot()',response['body'])

    def test_birthdays_and_new_schedules(self):
        for when in ['monthly','quarterly']:
            s=chart();s['kids'][0].pop('age');s['kids'][0]['birthDate']='2017-10-26'
            s['chores'][0]['when']=when;self.assertTrue(valid_state(s))
        s['kids'][0]['birthDate']='2099-01-01';self.assertFalse(valid_state(s))

    def test_optional_auth_allows_page_and_api_without_credentials(self):
        self.app=create_app(self.path,'','',auth_enabled=False)
        self.assertTrue(self.request(path='/',auth=False)['status'].startswith('200'))
        response=self.request('PUT',auth=False,body={'revision':0,'state':chart()})
        self.assertEqual(response['body'],{'revision':1})
        self.assertEqual(self.request(auth=False)['body']['state'],chart())
        self.assertTrue(self.request('PUT',auth=False,headers=False,body={'revision':1,'state':chart()})['status'].startswith('403'))

    def test_chore_minutes_validation_and_persistence(self):
        s=chart();s['chores'][0]['minutes']=35
        self.assertTrue(self.save(state=s)['status'].startswith('200'))
        self.assertEqual(self.request()['body']['state']['chores'][0]['minutes'],35)
        for value in [0,-1,481,1.5,True,'10',None]:
            s['chores'][0]['minutes']=value
            self.assertFalse(valid_state(s))

    def test_credentials_required(self):
        with self.assertRaises(ValueError):create_app(self.path,'family','')


if __name__=='__main__':
    unittest.main()
