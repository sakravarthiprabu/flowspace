"""Authentication integration tests plus a mocked SMTP transport test."""
import json,os,secrets,sqlite3,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from test_server import Client,Integration
import auth
ROOT=Path(__file__).resolve().parent
class Verification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        cls.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as s:s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1]
        cls.base=f'http://127.0.0.1:{cls.port}';cls.database=Path(cls.tmp.name)/'test.db'
        cls.env={**os.environ,'PORT':str(cls.port),'FLOWSPACE_DB':str(cls.database),'FLOWSPACE_DEMO_MAIL':'1'}
        Integration.start.__func__(cls)
    @classmethod
    def tearDownClass(cls):cls.process.terminate();cls.process.wait();cls.tmp.cleanup()
    def setUp(self):
        with sqlite3.connect(self.database) as c:c.execute('DELETE FROM rate_limits')
    def sql(self,sql,args=()):
        with sqlite3.connect(self.database) as c:return c.execute(sql,args).fetchall()
    def pending(self,email):
        client=Client(self.base);status,data=client.call('/api/register',{'email':email,'name':'Test user','password':'Testing123!','workspace':'Secure workspace','mobile':'+91 98765 43210','questions':[0,1],'answers':['Green School','Madurai']});self.assertEqual(status,200);return client,data
    def test_signup_and_code_guards(self):
        client,d=self.pending('pending@example.com')
        self.assertEqual(self.sql('SELECT id FROM users WHERE email=?',('pending@example.com',)),[])
        self.assertEqual(client.call('/api/me')[0],401)
        self.assertEqual(client.call('/api/auth/resend',{'challenge':d['challenge']})[0],400)
        bad='000000' if d['demo_code']!='000000' else '111111'
        self.assertEqual(client.call('/api/auth/verify',{'challenge':d['challenge'],'code':bad})[0],400)
        self.assertEqual(client.call('/api/auth/verify',{'challenge':d['challenge'],'code':d['demo_code']})[0],200)
        self.assertEqual(client.call('/api/auth/verify',{'challenge':d['challenge'],'code':d['demo_code']})[0],400)
        user=self.sql('SELECT email_verified,password,answers FROM users WHERE email=?',('pending@example.com',))[0];self.assertEqual(user[0],1);self.assertNotIn('Testing123!',user[1]);self.assertNotIn('Green School',user[2])
        locked,x=self.pending('locked@example.com');bad='000000' if x['demo_code']!='000000' else '111111'
        for _ in range(5):self.assertEqual(locked.call('/api/auth/verify',{'challenge':x['challenge'],'code':bad})[0],400)
        self.assertEqual(locked.call('/api/auth/verify',{'challenge':x['challenge'],'code':x['demo_code']})[0],400)
        expired,x=self.pending('expired@example.com');self.sql('UPDATE challenges SET expires=? WHERE token=?',(int(time.time())-1,x['challenge']))
        self.assertEqual(expired.call('/api/auth/verify',{'challenge':x['challenge'],'code':x['demo_code']})[0],400)
        self.sql('UPDATE challenges SET created=? WHERE token=?',(int(time.time())-61,x['challenge']))
        status,new=expired.call('/api/auth/resend',{'challenge':x['challenge']});self.assertEqual(status,200);self.assertNotEqual(new['challenge'],x['challenge'])
        self.assertEqual(expired.call('/api/auth/verify',{'challenge':x['challenge'],'code':x['demo_code']})[0],400)
        self.assertEqual(expired.call('/api/auth/verify',{'challenge':new['challenge'],'code':new['demo_code']})[0],200)
    def test_24_hour_login_and_recovery(self):
        client=Client(self.base);client.register('Owner','24hour@example.com');uid=client.user['id'];client.call('/api/logout',{})
        self.assertEqual(client.call('/api/login',{'email':'24hour@example.com','password':'Testing123!'})[1]['ok'],True)
        client.csrf=client.call('/api/me')[1]['csrf'];client.call('/api/logout',{})
        self.sql('UPDATE users SET last_otp=?,last_logout=? WHERE id=?',(int(time.time())-86401,int(time.time())-86401,uid));self.sql('DELETE FROM challenges WHERE email=?',('24hour@example.com',))
        status,d=client.call('/api/login',{'email':'24hour@example.com','password':'Testing123!'});self.assertEqual(status,200);self.assertEqual(d['step'],'otp');self.assertEqual(client.call('/api/me')[0],401)
        self.assertEqual(client.call('/api/auth/verify',{'challenge':d['challenge'],'code':d['demo_code']})[0],200)
        self.assertLessEqual(self.sql('SELECT expires FROM sessions WHERE user_id=?',(uid,))[0][0],int(time.time())+86400)
        self.sql('DELETE FROM challenges WHERE email=?',('24hour@example.com',));status,r=client.call('/api/recovery/start',{'email':'24hour@example.com'});self.assertEqual(status,200)
        self.assertEqual(client.call('/api/recovery/reset',{'challenge':r['challenge'],'password':'Updated123!','answers':['Green School','Madurai']})[0],400)
        status,verified=client.call('/api/auth/verify',{'challenge':r['challenge'],'code':r['demo_code']});self.assertEqual(status,200);self.assertEqual(len(verified['questions']),2)
        self.assertEqual(client.call('/api/recovery/reset',{'challenge':verified['challenge'],'password':'Updated123!','answers':['wrong','wrong']})[0],400)
        self.assertEqual(client.call('/api/recovery/reset',{'challenge':verified['challenge'],'password':'Updated123!','answers':['  GREEN   School ','madurai']})[0],200)
        self.assertEqual(client.call('/api/me')[0],401)
        self.assertEqual(client.call('/api/recovery/reset',{'challenge':verified['challenge'],'password':'Updated123!','answers':['Green School','Madurai']})[0],400)
        self.assertEqual(client.call('/api/login',{'email':'24hour@example.com','password':'Testing123!'})[0],401)
        self.assertEqual(client.call('/api/login',{'email':'24hour@example.com','password':'Updated123!'})[0],200)
        client.csrf=client.call('/api/me')[1]['csrf'];self.assertEqual(client.call('/api/security/questions',{'current_password':'Updated123!','questions':[1,2],'answers':['Dindigul','Prabu']})[0],200)
    def test_security_questions_and_rate_limit(self):
        client=Client(self.base)
        self.assertEqual(client.call('/api/register',{'email':'bad@example.com','name':'Test','password':'Testing123!','questions':[0,0],'answers':['aaa','bbb']})[0],400)
        for _ in range(12):self.assertEqual(client.call('/api/login',{'email':'absent@example.com','password':'WrongPassword'})[0],401)
        self.assertEqual(client.call('/api/login',{'email':'absent@example.com','password':'WrongPassword'})[0],400)
    def test_mobile_and_theme_preferences(self):
        client=Client(self.base);client.register('Mobile user','mobile@example.com')
        self.assertEqual(client.call('/api/me')[1]['user']['mobile'],'+919876543210')
        self.assertEqual(client.call('/api/theme',{'theme':'dark'})[0],200)
        self.assertEqual(client.call('/api/me')[1]['user']['theme'],'dark')
        self.assertEqual(client.call('/api/theme',{'theme':'light'})[0],200)
        self.assertEqual(client.call('/api/theme',{'theme':'invalid'})[0],400)
        self.assertEqual(client.call('/api/theme',{'theme':'dark'},csrf=False)[0],403)
        profile={'name':'Mobile user','workspace':'Workspace','theme':'light','mobile':'+91 91234 56789'}
        self.assertEqual(client.call('/api/settings',profile)[0],200)
        self.assertEqual(client.call('/api/me')[1]['user']['mobile'],'+919123456789')
        self.assertEqual(client.call('/api/settings',{**profile,'mobile':'12345'})[0],400)
        self.assertEqual(client.call('/api/me')[1]['user']['mobile'],'+919123456789')
        self.assertNotIn('mobile',client.call('/api/data')[1]['team'][0])
        import auth
        for number in ['+91 98765 43210','+1 (415) 555-0123']:
            self.assertTrue(auth.mobile_value(number).startswith('+'))
        for number in ['9876543210','+91 12345 67890','+012345678','+91999']:
            with self.assertRaises(ValueError):auth.mobile_value(number)
    def test_legacy_migration(self):
        import server
        uid=self.sql('INSERT INTO users(name,email,password) VALUES(?,?,?) RETURNING id',('Legacy','legacy@example.com',server.password_hash('Testing123!')))[0][0]
        wid=self.sql('INSERT INTO workspaces(name,owner) VALUES(?,?) RETURNING id',('Legacy workspace',uid))[0][0];self.sql('INSERT INTO members VALUES(?,?)',(wid,uid))
        client=Client(self.base);status,d=client.call('/api/login',{'email':'legacy@example.com','password':'Testing123!'});self.assertEqual(status,200);self.assertEqual(d['step'],'otp')
        self.assertEqual(client.call('/api/auth/verify',{'challenge':d['challenge'],'code':d['demo_code']})[0],200)
        self.assertFalse(client.call('/api/me')[1]['user']['security_configured'])

class MailTransport(unittest.TestCase):
    def test_actual_smtp_path_without_sending_email(self):
        smtp=MagicMock();smtp.__enter__.return_value=smtp
        with patch.dict(os.environ,{'FLOWSPACE_DEMO_MAIL':'0','SMTP_HOST':'smtp.example.test','SMTP_FROM':'sender@example.test','SMTP_USERNAME':'user','SMTP_PASSWORD':'secret','SMTP_SECURITY':'starttls','SMTP_PORT':'587'}),patch('auth.smtplib.SMTP',return_value=smtp):
            auth.send('recipient@example.test','123456','signup')
            smtp.starttls.assert_called_once();smtp.login.assert_called_once_with('user','secret');msg=smtp.send_message.call_args.args[0]
            self.assertEqual(msg['To'],'recipient@example.test');self.assertIn('123456',msg.get_content())
    def test_missing_configuration_blocks_email(self):
        with patch.dict(os.environ,{'FLOWSPACE_DEMO_MAIL':'0','SMTP_HOST':'','SMTP_FROM':''}):
            with self.assertRaises(ValueError):auth.send('recipient@example.test','123456','signup')

if __name__=='__main__':unittest.main(verbosity=2)
