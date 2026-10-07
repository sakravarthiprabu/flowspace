"""FlowSpace local workspace. Python 3.10+, no third-party dependencies."""
import hashlib, hmac, json, os, secrets, sqlite3, time, re
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse

import auth
ROOT = Path(__file__).resolve().parent
auth.load_env(ROOT)
DB = Path(os.environ.get('FLOWSPACE_DB', str(ROOT / 'flowspace.db')))
PAGES = ['overview', 'projects', 'tasks', 'analytics', 'messages', 'team', 'calendar', 'settings']

def conn():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def initialize():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL, theme TEXT DEFAULT 'light');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner INTEGER NOT NULL REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER REFERENCES workspaces(id), user_id INTEGER REFERENCES users(id), PRIMARY KEY(workspace_id,user_id));
        CREATE TABLE IF NOT EXISTS invitations(id INTEGER PRIMARY KEY,workspace_id INTEGER REFERENCES workspaces(id),email TEXT NOT NULL, UNIQUE(workspace_id,email));
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, workspace_id INTEGER REFERENCES workspaces(id), title TEXT NOT NULL, description TEXT, due TEXT, priority TEXT, status TEXT, favorite INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, workspace_id INTEGER REFERENCES workspaces(id), project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL, title TEXT NOT NULL, status TEXT, priority TEXT, due TEXT, assignee INTEGER REFERENCES users(id), completed_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, workspace_id INTEGER REFERENCES workspaces(id), title TEXT NOT NULL, date TEXT, start TEXT, end TEXT, notes TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,workspace_id INTEGER REFERENCES workspaces(id),sender INTEGER REFERENCES users(id), body TEXT, created TEXT DEFAULT (datetime('now')));
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY,workspace_id INTEGER REFERENCES workspaces(id), body TEXT, created TEXT DEFAULT (datetime('now')));
        ''')
        auth.migrate(c)

def password_hash(p):
    salt = secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(p.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()

def check_password(p, hashed):
    salt, value = hashed.split(':')
    actual = hashlib.scrypt(p.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return hmac.compare_digest(value, actual)

def public(row):
    return {k:row[k] for k in ['id','name','email','theme']}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('X-Frame-Options','DENY')
        super().end_headers()
    def out(self,data,status=200,cookie=None):
        raw=json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(raw)))
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(raw)
    def session(self,c):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return None
        token=cookie.get('flowspace_session')
        if not token:return None
        return c.execute('SELECT s.*,u.name,u.email,u.theme FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>? AND u.email_verified=1 AND u.last_otp>?',(token.value,time.time(),int(time.time())-86400)).fetchone()
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/auth/config':return self.out(auth.config())
        if path.startswith('/api/'):
            with conn() as c:
                s=self.session(c)
                if not s:return self.out({'error':'Please log in.'},401)
                user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
                ws=c.execute('SELECT w.* FROM workspaces w JOIN members m ON w.id=m.workspace_id WHERE m.user_id=? ORDER BY w.id LIMIT 1',(s['user_id'],)).fetchone()
                if path=='/api/me':return self.out({'user':{**public(user),'security_configured':bool(json.loads(user['questions'])),'mobile':user['mobile']},'csrf':s['csrf'],'workspace':dict(ws)})
                if path!='/api/data':return self.out({'error':'Not found'},404)
                data={key:[dict(r) for r in c.execute('SELECT * FROM '+key+' WHERE workspace_id=? ORDER BY id',(ws['id'],))] for key in ['projects','tasks','events','messages','activity','invitations']}
                data['team']=[public(r) for r in c.execute('SELECT u.* FROM users u JOIN members m ON u.id=m.user_id WHERE m.workspace_id=?',(ws['id'],))]
                data['workspace']=dict(ws)
                return self.out(data)
        # Only explicit public frontend assets; never serve the database or source.
        target=path.strip('/') or 'index.html'
        if target not in ['index.html','login.html','register.html','forgot.html','style.css','script.js','theme.js']+[p+'.html' for p in PAGES]:
            return self.send_error(404)
        self.path='/'+target
        super().do_GET()
    def do_HEAD(self):
        if urlparse(self.path).path.strip('/') not in ['index.html','login.html','register.html','forgot.html','style.css','script.js','theme.js']+[p+'.html' for p in PAGES]:
            return self.send_error(404)
        super().do_HEAD()
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<50000:raise ValueError('Invalid request size.')
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('Invalid request.')
            with conn() as c:self.mutate(c,urlparse(self.path).path,data)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
            self.out({'error':str(e) or 'Invalid input.'},400)
        except sqlite3.IntegrityError:
            self.out({'error':'This email is already registered, or the selected record is invalid.'},409)
        except Exception:
            self.out({'error':'The server could not complete this request.'},500)
    def mutate(self,c,path,d):
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):return self.out({'error':'Invalid origin.'},403)
        def text(key,maxlen=200,required=False):
            v=str(d.get(key,''))
            if 'password' not in key:v=v.strip()
            if len(v)>maxlen or (required and not v):raise ValueError(f'Please enter a valid {key.replace("_"," ")}.')
            return v
        def date(key='due'):
            v=text(key,10)
            if v:
                from datetime import date as dt
                try:dt.fromisoformat(v)
                except ValueError:raise ValueError('Please choose a valid date.')
            return v
        def choice(key,options):
            v=text(key)
            if v not in options:raise ValueError('Invalid '+key)
            return v
        if auth.handle(self,c,path,d,password_hash,check_password):return
        s=self.session(c)
        if not s:return self.out({'error':'Please log in.'},401)
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']):return self.out({'error':'Refresh the page and try again.'},403)
        uid=s['user_id'];ws=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.id LIMIT 1',(uid,)).fetchone();wid=ws['id']
        def record(table):
            r=c.execute('SELECT * FROM '+table+' WHERE id=? AND workspace_id=?',(d.get('id'),wid)).fetchone()
            if not r:raise ValueError('Record not found in this workspace.')
            return r
        def log(body):c.execute('INSERT INTO activity(workspace_id,body) VALUES(?,?)',(wid,s['name']+' '+body))
        if path=='/api/logout':
            c.execute('UPDATE users SET last_logout=? WHERE id=?',(int(time.time()),uid));c.execute('DELETE FROM sessions WHERE token=?',(s['token'],));c.commit()
            return self.out({'ok':True},cookie='flowspace_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        elif path=='/api/project/save':
            values=(text('title',120,True),text('description',1000),date(),choice('priority',['Low','Medium','High']),choice('status',['On track','At risk','In review','Completed']))
            if d.get('id'):record('projects');c.execute('UPDATE projects SET title=?,description=?,due=?,priority=?,status=? WHERE id=?',values+(d['id'],))
            else:c.execute('INSERT INTO projects(title,description,due,priority,status,workspace_id) VALUES(?,?,?,?,?,?)',values+(wid,))
            log('saved project '+values[0])
        elif path=='/api/project/delete':
            record('projects');c.execute('DELETE FROM projects WHERE id=?',(d['id'],));log('deleted a project')
        elif path=='/api/project/favorite':
            r=record('projects');c.execute('UPDATE projects SET favorite=? WHERE id=?',(not r['favorite'],r['id']))
        elif path=='/api/task/save':
            pid=d.get('project_id') or None;assignee=d.get('assignee') or uid
            if pid and not c.execute('SELECT id FROM projects WHERE id=? AND workspace_id=?',(pid,wid)).fetchone():raise ValueError('Invalid project.')
            if not c.execute('SELECT user_id FROM members WHERE user_id=? AND workspace_id=?',(assignee,wid)).fetchone():raise ValueError('Invalid assignee.')
            status=choice('status',['To Do','In Progress','Review','Completed'])
            completed=time.strftime('%Y-%m-%d') if status=='Completed' else None
            if d.get('id'):
                old=record('tasks')
                if status=='Completed' and old['completed_at']:completed=old['completed_at']
            values=(text('title',160,True),status,choice('priority',['Low','Medium','High']),date(),pid,assignee,completed)
            if d.get('id'):c.execute('UPDATE tasks SET title=?,status=?,priority=?,due=?,project_id=?,assignee=?,completed_at=? WHERE id=?',values+(d['id'],))
            else:c.execute('INSERT INTO tasks(title,status,priority,due,project_id,assignee,completed_at,workspace_id) VALUES(?,?,?,?,?,?,?,?)',values+(wid,))
            log('saved task '+values[0])
        elif path=='/api/task/delete':record('tasks');c.execute('DELETE FROM tasks WHERE id=?',(d['id'],));log('deleted a task')
        elif path=='/api/event/save':
            start=text('start',5);end=text('end',5)
            if not re.fullmatch(r'\d{2}:\d{2}',start) or not re.fullmatch(r'\d{2}:\d{2}',end) or start>=end or start>'23:59' or end>'23:59' or start[3:]>'59' or end[3:]>'59':raise ValueError('Choose valid times; the end must be after the start.')
            eventdate=date('date')
            if not eventdate:raise ValueError('Choose a date.')
            values=(text('title',120,True),eventdate,start,end,text('notes',1000))
            if d.get('id'):record('events');c.execute('UPDATE events SET title=?,date=?,start=?,end=?,notes=? WHERE id=?',values+(d['id'],))
            else:c.execute('INSERT INTO events(title,date,start,end,notes,workspace_id) VALUES(?,?,?,?,?,?)',values+(wid,))
            log('saved event '+values[0])
        elif path=='/api/event/delete':record('events');c.execute('DELETE FROM events WHERE id=?',(d['id'],));log('deleted a calendar event')
        elif path=='/api/message':c.execute('INSERT INTO messages(workspace_id,sender,body) VALUES(?,?,?)',(wid,uid,text('body',2000,True)))
        elif path=='/api/invite':
            if uid!=ws['owner']:return self.out({'error':'Only the workspace owner can add members.'},403)
            email=text('email',254,True).lower()
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise ValueError('Enter a valid email.')
            u=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
            if u:
                # Accounts have one workspace in this local version.
                raise ValueError('Invite an unregistered email. Existing accounts keep their own workspace.')
            c.execute('INSERT OR IGNORE INTO invitations(workspace_id,email) VALUES(?,?)',(wid,email));log('invited '+email)
        elif path=='/api/invite/delete':
            if uid!=ws['owner']:return self.out({'error':'Only the owner can revoke invitations.'},403)
            record('invitations');c.execute('DELETE FROM invitations WHERE id=?',(d['id'],))
        elif path=='/api/member/remove':
            if uid!=ws['owner'] or d.get('id')==uid:return self.out({'error':'Only the owner can remove other members.'},403)
            member=c.execute('SELECT u.* FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=? AND u.id=?',(wid,d.get('id'))).fetchone()
            if not member:raise ValueError('Member not found.')
            c.execute('UPDATE tasks SET assignee=? WHERE workspace_id=? AND assignee=?',(uid,wid,d['id']))
            c.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?',(wid,d['id']))
            newwid=c.execute('INSERT INTO workspaces(name,owner) VALUES(?,?)',(member['name']+"'s workspace",d['id'])).lastrowid
            c.execute('INSERT INTO members VALUES(?,?)',(newwid,d['id']));log('removed '+member['name'])
        elif path=='/api/security/questions':
            u=c.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()
            if not check_password(text('current_password',256,True),u['password']):raise ValueError('Current password is incorrect.')
            q,a=auth.answers_payload(d,password_hash)
            c.execute('UPDATE users SET questions=?,answers=? WHERE id=?',(json.dumps(q),json.dumps(a),uid))
        elif path=='/api/theme':
            c.execute('UPDATE users SET theme=? WHERE id=?',(choice('theme',['light','dark']),uid))
        elif path=='/api/settings':
            theme=choice('theme',['light','dark']);name=text('name',80,True)
            if 'mobile' in d:c.execute('UPDATE users SET mobile=? WHERE id=?',(auth.mobile_value(d['mobile']),uid))
            c.execute('UPDATE users SET name=?,theme=? WHERE id=?',(name,theme,uid))
            if uid==ws['owner']:c.execute('UPDATE workspaces SET name=? WHERE id=?',(text('workspace',80,True),wid))
            if d.get('new_password'):
                u=c.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()
                if not check_password(text('current_password',256,True),u['password']):raise ValueError('Current password is incorrect.')
                p=text('new_password',256,True)
                if len(p)<8:raise ValueError('Use at least 8 characters.')
                c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(p),uid))
                c.execute('DELETE FROM sessions WHERE user_id=? AND token!=?',(uid,s['token']))
        else:return self.out({'error':'Not found'},404)
        c.commit();self.out({'ok':True})

if __name__=='__main__':
    initialize()
    port=int(os.environ.get('PORT','8000'))
    host=os.environ.get('FLOWSPACE_HOST','127.0.0.1')
    try:server=ThreadingHTTPServer((host,port),Handler)
    except OSError as error:
        if error.errno in [48,98]:
            print(f'Port {port} is already in use. Try: PORT={port+1} python3 server.py',flush=True)
            raise SystemExit(1)
        raise
    print(f'FlowSpace is ready: http://localhost:{port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nFlowSpace stopped.',flush=True)
    finally:server.server_close()
