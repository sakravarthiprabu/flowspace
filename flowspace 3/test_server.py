"""Integration checks against a temporary local server and database."""
import json, os, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from http.cookiejar import CookieJar
ROOT=Path(__file__).resolve().parent

class Client:
    def __init__(self,base):self.base=base;self.opener=build_opener(HTTPCookieProcessor(CookieJar()));self.csrf=''
    def call(self,path,data=None,csrf=True):
        headers={'Content-Type':'application/json'}
        if csrf:headers['X-CSRF-Token']=self.csrf
        request=Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with self.opener.open(request) as response:return response.status,json.loads(response.read())
        except HTTPError as e:
            raw=e.read()
            try:return e.code,json.loads(raw)
            except ValueError:return e.code,raw.decode()
    def register(self,name,email):
        status,data=self.call('/api/register',{'name':name,'email':email,'password':'Testing123!','workspace':'Test workspace','mobile':'+91 98765 43210','questions':[0,1],'answers':['Green School','Madurai']})
        assert status==200,(status,data)
        status,verified=self.call('/api/auth/verify',{'challenge':data['challenge'],'code':data['demo_code']})
        assert status==200,(status,verified)
        status,data=self.call('/api/me');self.csrf=data['csrf'];self.user=data['user'];return data

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as s:s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1]
        cls.base=f'http://127.0.0.1:{cls.port}'
        cls.env={**os.environ,'PORT':str(cls.port),'FLOWSPACE_DB':str(Path(cls.tmp.name)/'test.db'),'FLOWSPACE_DEMO_MAIL':'1'}
        cls.start()
    @classmethod
    def start(cls):
        cls.process=subprocess.Popen([sys.executable,str(ROOT/'server.py')],env=cls.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',cls.port),timeout=.1):return
            except OSError:time.sleep(.05)
        raise RuntimeError('Server did not start')
    @classmethod
    def tearDownClass(cls):cls.process.terminate();cls.process.wait();cls.tmp.cleanup()
    def test_full_workflow(self):
        owner=Client(self.base);self.assertEqual(owner.call('/api/data')[0],401)
        owner.register('Owner','owner@example.com')
        self.assertEqual(owner.call('/api/project/save',{},False)[0],403)
        p={'title':'LicenseHub','description':'Renewal test','due':'2026-10-21','priority':'High','status':'On track'}
        self.assertEqual(owner.call('/api/project/save',p)[0],200)
        pid=owner.call('/api/data')[1]['projects'][0]['id']
        p.update(id=pid,title='LicenseHub revised');self.assertEqual(owner.call('/api/project/save',p)[0],200)
        self.assertEqual(owner.call('/api/project/favorite',{'id':pid})[0],200)
        t={'title':'Renewal check','status':'To Do','priority':'Medium','due':'2026-10-07','project_id':pid,'assignee':owner.user['id']}
        self.assertEqual(owner.call('/api/task/save',t)[0],200)
        tid=owner.call('/api/data')[1]['tasks'][0]['id'];t.update(id=tid,status='Completed');owner.call('/api/task/save',t)
        self.assertTrue(owner.call('/api/data')[1]['tasks'][0]['completed_at'])
        event={'title':'Planning','date':'2026-10-07','start':'09:00','end':'10:00','notes':'Agenda'}
        self.assertEqual(owner.call('/api/event/save',event)[0],200)
        eid=owner.call('/api/data')[1]['events'][0]['id'];event.update(id=eid,title='Planning revised');owner.call('/api/event/save',event)
        invalid={**event,'end':'08:00'};self.assertEqual(owner.call('/api/event/save',invalid)[0],400)
        outsider=Client(self.base);outsider.register('Other','other@example.com')
        self.assertEqual(outsider.call('/api/project/delete',{'id':pid})[0],400)
        self.assertEqual(len(outsider.call('/api/data')[1]['projects']),0)
        self.assertEqual(owner.call('/api/invite',{'email':'member@example.com'})[0],200)
        member=Client(self.base);member.register('Member','member@example.com')
        self.assertEqual(len(member.call('/api/data')[1]['projects']),1)
        self.assertEqual(member.call('/api/invite',{'email':'third@example.com'})[0],403)
        self.assertEqual(member.call('/api/message',{'body':'Hello <script>test</script>'})[0],200)
        self.assertEqual(owner.call('/api/data')[1]['messages'][0]['body'],'Hello <script>test</script>')
        self.assertEqual(owner.call('/api/settings',{'name':'New owner','workspace':'New workspace','theme':'dark','current_password':'Wrong','new_password':'Updated123!'})[0],400)
        self.assertEqual(owner.call('/api/me')[1]['user']['name'],'Owner') # Failed changes roll back.
        self.assertEqual(owner.call('/api/settings',{'name':'New owner','workspace':'New workspace','theme':'dark','current_password':'Testing123!','new_password':'Updated123!'})[0],200)
        self.assertEqual(owner.call('/api/me')[1]['user']['theme'],'dark')
        # Restart proves sessions and data survive server restarts.
        type(self).process.terminate();type(self).process.wait();type(self).start()
        self.assertEqual(owner.call('/api/data')[1]['events'][0]['title'],'Planning revised')
        self.assertEqual(owner.call('/api/project/delete',{'id':pid})[0],200)
        self.assertIsNone(owner.call('/api/data')[1]['tasks'][0]['project_id'])
        self.assertEqual(owner.call('/api/event/delete',{'id':eid})[0],200)
        self.assertEqual(owner.call('/api/task/delete',{'id':tid})[0],200)
        self.assertEqual(owner.call('/api/member/remove',{'id':member.user['id']})[0],200)
        self.assertEqual(len(member.call('/api/data')[1]['messages']),0)
        self.assertEqual(owner.call('/flowspace.db')[0],404)
        self.assertEqual(owner.call('/server.py')[0],404)
        for slug in ['overview','projects','tasks','analytics','messages','team','calendar','settings','login','register']:
            with owner.opener.open(self.base+'/'+slug+'.html') as response:self.assertEqual(response.status,200)
        self.assertEqual(owner.call('/api/logout',{})[0],200)
        self.assertEqual(owner.call('/api/me')[0],401)
        self.assertEqual(owner.call('/api/login',{'email':'owner@example.com','password':'Testing123!'})[0],401)
        self.assertEqual(owner.call('/api/login',{'email':'owner@example.com','password':'Updated123!'})[0],200)

if __name__=='__main__':unittest.main(verbosity=2)
