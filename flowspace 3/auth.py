"""Verified signup, email OTP and OTP + security-answer password recovery."""
import hashlib,hmac,json,os,re,secrets,smtplib,time
from email.message import EmailMessage
from pathlib import Path
QUESTIONS=['What was the name of your first school?','What city were you born in?','What was your childhood nickname?','What is the name of your first pet?','What is a memorable place from your childhood?']

def load_env(root):
    p=root/'.env'
    if p.exists():
        for line in p.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def migrate(c):
    cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
    for name,typ in [('email_verified','INTEGER DEFAULT 0'),('last_otp','INTEGER DEFAULT 0'),('last_logout','INTEGER DEFAULT 0'),('questions','TEXT DEFAULT \'[]\''),('answers','TEXT DEFAULT \'[]\''),('mobile',"TEXT DEFAULT ''")]:
        if name not in cols:c.execute('ALTER TABLE users ADD COLUMN '+name+' '+typ)
    c.executescript('''CREATE TABLE IF NOT EXISTS challenges(token TEXT PRIMARY KEY,email TEXT,purpose TEXT,code_hash TEXT,payload TEXT,expires INTEGER,attempts INTEGER DEFAULT 0,created INTEGER,verified INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, count INTEGER, expires INTEGER);''')

def mobile_value(value):
    v=re.sub(r'[ ()-]','',str(value).strip())
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}',v):raise ValueError('Enter your mobile number with country code, such as +91 followed by 10 digits.')
    if v.startswith('+91') and not re.fullmatch(r'\+91[6-9][0-9]{9}',v):raise ValueError('Enter a valid 10-digit Indian mobile number after +91.')
    return v

def demo():return os.environ.get('FLOWSPACE_DEMO_MAIL','0')=='1'
def config():return {'demo':demo(),'mail_ready':demo() or bool(os.environ.get('SMTP_HOST') and os.environ.get('SMTP_FROM')),'questions':QUESTIONS}
def send(email,code,purpose):
    if demo():return
    if not os.environ.get('SMTP_HOST') or not os.environ.get('SMTP_FROM'):raise ValueError('Email delivery is not configured. Follow EMAIL_SETUP.md to enable SMTP or explicit local demo mode.')
    msg=EmailMessage();msg['Subject']='FlowSpace — your verification code';msg['From']=os.environ['SMTP_FROM'];msg['To']=email
    msg.set_content(f'Your FlowSpace {purpose} code is {code}.\n\nIt expires in 10 minutes. Never share it. If you did not request it, ignore this email.')
    try:
        host=os.environ['SMTP_HOST'];port=int(os.environ.get('SMTP_PORT','587'));mode=os.environ.get('SMTP_SECURITY','starttls')
        if mode not in ['starttls','ssl']:raise ValueError('SMTP_SECURITY must be starttls or ssl.')
        import ssl
        if mode=='ssl':smtp=smtplib.SMTP_SSL(host,port,timeout=15,context=ssl.create_default_context())
        else:
            smtp=smtplib.SMTP(host,port,timeout=15);smtp.ehlo();smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
        with smtp:
            if os.environ.get('SMTP_USERNAME'):smtp.login(os.environ['SMTP_USERNAME'],os.environ.get('SMTP_PASSWORD',''))
            smtp.send_message(msg)
    except (OSError,smtplib.SMTPException,ValueError) as e:raise ValueError('Email could not be sent. Check the SMTP configuration and try again.') from e

def rate(c,key,limit=8,seconds=900):
    now=int(time.time());r=c.execute('SELECT * FROM rate_limits WHERE key=?',(key,)).fetchone()
    if r and r['expires']>now:
        if r['count']>=limit:raise ValueError('Too many attempts. Try again in 15 minutes.')
        c.execute('UPDATE rate_limits SET count=count+1 WHERE key=?',(key,))
    else:c.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)',(key,1,now+seconds))
    c.commit()

def issue(c,email,purpose,payload=None,previous=None):
    now=int(time.time());last=c.execute('SELECT created FROM challenges WHERE email=? ORDER BY created DESC LIMIT 1',(email,)).fetchone()
    if last and now-last['created']<60:raise ValueError('Please wait 60 seconds before requesting another code.')
    rate(c,'mail:'+email,8,3600)
    token=secrets.token_urlsafe(32);code=f'{secrets.randbelow(1000000):06d}'
    send(email,code,purpose)
    # Resending invalidates previous codes. Hash binds the short code to its unguessable challenge.
    c.execute('DELETE FROM challenges WHERE email=? AND purpose=?',(email,purpose))
    c.execute('INSERT INTO challenges(token,email,purpose,code_hash,payload,expires,created) VALUES(?,?,?,?,?,?,?)',(token,email,purpose,hashlib.sha256((token+code).encode()).hexdigest(),json.dumps(payload or {}),now+600,now));c.commit()
    return {'step':'otp','challenge':token,'email':email,'expires_in':600,'resend_after':60,'demo':demo(),**({'demo_code':code} if demo() else {})}

def challenge(c,d,verified=False):
    r=c.execute('SELECT * FROM challenges WHERE token=?',(d.get('challenge',''),)).fetchone()
    if not r or r['expires']<time.time() or r['attempts']>=5:raise ValueError('This code has expired or too many attempts were made. Request a new code.')
    if verified and not r['verified']:raise ValueError('Verify your email code first.')
    return r

def verify_code(c,d):
    c.execute('BEGIN IMMEDIATE')
    r=challenge(c,d)
    if r['verified']:raise ValueError('This code has already been used.')
    c.execute('UPDATE challenges SET attempts=attempts+1 WHERE token=?',(r['token'],))
    if not hmac.compare_digest(r['code_hash'],hashlib.sha256((r['token']+str(d.get('code','')).strip()).encode()).hexdigest()):
        c.commit();raise ValueError('Incorrect verification code. Please try again.')
    c.execute('UPDATE challenges SET verified=1 WHERE token=?',(r['token'],));c.commit()
    return r

def session(h,c,uid):
    u=c.execute('SELECT last_otp FROM users WHERE id=?',(uid,)).fetchone();now=int(time.time());token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    remaining=max(1,min(86400,u['last_otp']+86400-now))
    c.execute('DELETE FROM sessions WHERE expires<?',(now,));c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token,uid,csrf,now+remaining));c.commit()
    secure='; Secure' if os.environ.get('FLOWSPACE_SECURE_COOKIE')=='1' else ''
    h.out({'ok':True},cookie=f'flowspace_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={remaining}{secure}')

def answers_payload(d,hash_password):
    q=d.get('questions',[]);a=d.get('answers',[])
    if not isinstance(q,list) or len(q)!=2 or len(set(str(x) for x in q))!=2 or any(not isinstance(x,int) or x<0 or x>=len(QUESTIONS) for x in q):raise ValueError('Choose two different security questions.')
    if not isinstance(a,list) or len(a)!=2 or any(not isinstance(x,str) or not 3<=len(x.strip())<=100 for x in a):raise ValueError('Enter answers between 3 and 100 characters.')
    return q,[hash_password(' '.join(x.lower().split())) for x in a]

def handle(h,c,path,d,hash_password,check_password):
    routes=['/api/login','/api/register','/api/auth/verify','/api/auth/resend','/api/recovery/start','/api/recovery/reset']
    if path not in routes:return False
    rate(c,'ip:'+h.client_address[0],100,900)
    def email_value():
        v=str(d.get('email','')).strip().lower()
        if len(v)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v):raise ValueError('Enter a valid email address.')
        return v
    if path in ['/api/login','/api/register']:
        email=email_value();password=d.get('password','')
        if not isinstance(password,str) or not 1<=len(password)<=256:raise ValueError('Please enter a valid password.')
        rate(c,'auth:'+email,12,900)
        u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if path=='/api/register':
            if u:raise ValueError('This email is already registered. Log in or use password recovery.')
            if len(password)<8:raise ValueError('Use at least 8 characters for your password.')
            name=str(d.get('name','')).strip();workspace=str(d.get('workspace','My workspace')).strip() or 'My workspace'
            if not 1<=len(name)<=80 or len(workspace)>80:raise ValueError('Enter a valid name and workspace.')
            q,a=answers_payload(d,hash_password)
            mobile=mobile_value(d.get('mobile',''))
            theme=d.get('theme','light')
            if theme not in ['light','dark']:raise ValueError('Invalid theme.')
            payload={'name':name,'workspace':workspace,'password':hash_password(password),'questions':q,'answers':a,'mobile':mobile,'theme':theme}
            h.out(issue(c,email,'signup',payload));return True
        if not u or not check_password(password,u['password']):h.out({'error':'Email or password is incorrect.'},401);return True
        # Reverify after 24h from the last OTP OR a logout older than 24h.
        if not u['email_verified'] or time.time()-u['last_otp']>=86400 or (u['last_logout'] and time.time()-u['last_logout']>=86400):
            h.out(issue(c,email,'login',{'uid':u['id']}));return True
        session(h,c,u['id']);return True
    if path=='/api/auth/resend':
        r=c.execute('SELECT * FROM challenges WHERE token=?',(d.get('challenge',''),)).fetchone()
        if not r or r['purpose'] not in ['signup','login','recovery'] or r['verified']:raise ValueError('Start the verification process again.')
        h.out(issue(c,r['email'],r['purpose'],json.loads(r['payload'])));return True
    if path=='/api/recovery/start':
        email=email_value();rate(c,'recovery:'+email,8,3600)
        u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if not u:
            h.out({'step':'otp','challenge':secrets.token_urlsafe(32),'email':email,'expires_in':600,'resend_after':60,'demo':demo(),'notice':'If this account exists, a code has been sent.'});return True
        h.out(issue(c,email,'recovery',{'uid':u['id']}));return True
    if path=='/api/auth/verify':
        r=verify_code(c,d);payload=json.loads(r['payload']);now=int(time.time())
        if r['purpose']=='recovery':
            u=c.execute('SELECT * FROM users WHERE id=?',(payload['uid'],)).fetchone()
            q=json.loads(u['questions'])
            # Rotate to a separate, short-lived one-use recovery ticket.
            ticket=secrets.token_urlsafe(32)
            c.execute('DELETE FROM challenges WHERE token=?',(r['token'],))
            c.execute('INSERT INTO challenges VALUES(?,?,?,?,?,?,?,?,?)',(ticket,r['email'],'reset','',r['payload'],now+600,0,now,1));c.commit()
            h.out({'step':'reset','challenge':ticket,'questions':[QUESTIONS[i] for i in q],'legacy':not bool(q)});return True
        if r['purpose']=='signup':
            uid=c.execute('INSERT INTO users(name,email,password,email_verified,last_otp,questions,answers,mobile,theme) VALUES(?,?,?,?,?,?,?,?,?)',(payload['name'],r['email'],payload['password'],1,now,json.dumps(payload['questions']),json.dumps(payload['answers']),payload.get('mobile',''),payload.get('theme','light'))).lastrowid
            invited=c.execute('SELECT workspace_id FROM invitations WHERE email=? ORDER BY id LIMIT 1',(r['email'],)).fetchone()
            if invited:
                wid=invited['workspace_id'];c.execute('DELETE FROM invitations WHERE workspace_id=? AND email=?',(wid,r['email']))
            else:wid=c.execute('INSERT INTO workspaces(name,owner) VALUES(?,?)',(payload['workspace'],uid)).lastrowid
            c.execute('INSERT INTO members VALUES(?,?)',(wid,uid))
        elif r['purpose']=='login':
            uid=payload['uid'];c.execute('UPDATE users SET email_verified=1,last_otp=?,last_logout=0 WHERE id=?',(now,uid))
        else:raise ValueError('Invalid verification step.')
        c.execute('DELETE FROM challenges WHERE token=?',(r['token'],));session(h,c,uid);return True
    if path=='/api/recovery/reset':
        r=challenge(c,d,True)
        if r['purpose']!='reset':raise ValueError('Verify your recovery code first.')
        u=c.execute('SELECT * FROM users WHERE id=?',(json.loads(r['payload'])['uid'],)).fetchone()
        c.execute('UPDATE challenges SET attempts=attempts+1 WHERE token=?',(r['token'],));c.commit()
        stored=json.loads(u['answers']);answers=d.get('answers',[])
        if stored:
            if not isinstance(answers,list) or len(answers)!=len(stored) or any(not isinstance(a,str) or len(a)>100 or not check_password(' '.join(a.lower().split()),b) for a,b in zip(answers,stored)):raise ValueError('Security answers do not match. Please try again.')
        p=d.get('password','')
        if not isinstance(p,str) or not 8<=len(p)<=256:raise ValueError('Use a password between 8 and 256 characters.')
        c.execute('UPDATE users SET password=?,email_verified=1,last_otp=?,last_logout=0 WHERE id=?',(hash_password(p),int(time.time()),u['id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));c.execute('DELETE FROM challenges WHERE email=?',(u['email'],));c.commit()
        h.out({'ok':True,'step':'done'});return True
    return False
