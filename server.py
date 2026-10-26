"""Private household planner: WSGI API and SQLite storage."""
import base64
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import shlex
import time
from contextlib import closing

MAX_BODY = 1_000_000
SCHEDULES = {'daily','mwf','mon','tue','wed','thu','fri','sat','sun','weekly','monthly','quarterly'}



def load_runtime_env(path='/data/.env', environ=None):
    """Read literal dotenv values; explicit process environment takes precedence."""
    config = {}
    allowed = {'AUTH_ENABLED', 'APP_USER', 'APP_PASSWORD', 'DB_PATH', 'PORT', 'TZ'}
    source = Path(path)
    if source.exists():
        for number, raw in enumerate(source.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, separator, value = line.partition('=')
            key = key.strip()
            if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                raise ValueError(f'Invalid configuration at {source}, line {number}.')
            value = value.strip()
            if value.startswith(('"', "'")):
                try:
                    parts = shlex.split(value, comments=True, posix=True)
                    if len(parts) != 1:
                        raise ValueError()
                    value = parts[0]
                except ValueError:
                    raise ValueError(f'Invalid quoted value at {source}, line {number}.') from None
            else:
                value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
            if key in allowed:
                config[key] = value
    config.update(dict(os.environ if environ is None else environ))
    return config


def configured_app(config):
    value = config.get('AUTH_ENABLED', 'true').strip().lower()
    if value not in {'true', '1', 'yes', 'on', 'false', '0', 'no', 'off'}:
        raise ValueError('AUTH_ENABLED must be true or false.')
    auth_enabled = value in {'true', '1', 'yes', 'on'}
    password = config.get('APP_PASSWORD', '')
    if auth_enabled and not password:
        raise ValueError('Set APP_PASSWORD in /data/.env (or provide it as an environment variable).')
    return create_app(config.get('DB_PATH', '/data/chores.sqlite3'), config.get('APP_USER', 'family'), password, auth_enabled=auth_enabled)


def valid_state(s):
    if not isinstance(s, dict) or s.get('version') != 1:
        return False
    kids, chores, checks = s.get('kids'), s.get('chores'), s.get('checks')
    if not isinstance(kids, list) or len(kids) > 30 or not isinstance(chores, list) or len(chores) > 50:
        return False
    for k in kids:
        if not isinstance(k, dict) or not isinstance(k.get('name'), str) or not 1 <= len(k['name'].strip()) <= 40:
            return False
        if k.get('birthDate'):
            from datetime import date
            try:
                if date.fromisoformat(k['birthDate']) > date.today() or k['birthDate'] < '1900-01-01':
                    return False
            except (ValueError, TypeError):
                return False
        elif type(k.get('age')) is not int or not 0 <= k['age'] <= 150:
            return False
        if not isinstance(k.get('id'), str) or not 1 <= len(k['id']) <= 100 or '|' in k['id']:
            return False
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', k.get('color', '#28594a')):
            return False
    if len({k['id'] for k in kids}) != len(kids):
        return False
    ids = {k['id'] for k in kids}
    for c in chores:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not 1 <= len(c['id']) <= 100 or '|' in c['id']:
            return False
        if not isinstance(c.get('name'), str) or not 1 <= len(c['name'].strip()) <= 120:
            return False
        if type(c.get('min')) is not int or not 1 <= c['min'] <= 99 or c.get('when') not in SCHEDULES:
            return False
        a = c.get('assignment', 'rotate')
        if not isinstance(a, str) or (a not in ('rotate','everyone') and a not in {'child:'+i for i in ids}):
            return False
        if c.get('day') is not None and (type(c['day']) is not int or not 0 <= c['day'] <= 6):
            return False
    return (len({c['id'] for c in chores}) == len(chores) and isinstance(checks, dict)
            and len(checks) <= 20000 and all(isinstance(k,str) and len(k) <= 400 and type(v) is bool for k,v in checks.items()))


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)')
            db.execute('INSERT OR IGNORE INTO schema_version VALUES (1)')
            db.execute('CREATE TABLE IF NOT EXISTS planner (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, state TEXT NOT NULL)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA busy_timeout=10000')
        return db

    def read(self):
        with closing(self.connect()) as db:
            row = db.execute('SELECT revision,state FROM planner WHERE id=1').fetchone()
        return {'revision':row[0], 'state':json.loads(row[1])} if row else {'revision':0,'state':None}

    def write(self, revision, state):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM planner WHERE id=1').fetchone()
            current = row[0] if row else 0
            if current != revision:
                return None
            db.execute('INSERT INTO planner VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state=excluded.state',
                       (revision+1,json.dumps(state,separators=(',',':'))))
        return revision+1

    def backup(self, destination):
        with closing(self.connect()) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)


def create_app(db_path, username, password, auth_enabled=True):
    if auth_enabled and (not username or not password or ':' in username):
        raise ValueError('Set APP_USER and APP_PASSWORD; username must not contain a colon.')
    store = Store(db_path)
    index = Path(__file__).with_name('dist').joinpath('index.html')

    def app(env, start_response):
        def respond(status, data, content_type='application/json', extra=()):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            start_response(status, [('Content-Type',content_type),('Content-Length',str(len(body))),
                                    ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),
                                    ('X-Frame-Options','DENY'),*extra])
            return [body]
        path, method = env.get('PATH_INFO','/'), env.get('REQUEST_METHOD','GET')
        if path == '/healthz' and method == 'GET':
            try:
                store.read()
                return respond('200 OK', {'ok':True})
            except sqlite3.Error:
                return respond('503 Service Unavailable', {'error':'Database unavailable'})
        try:
            scheme, token = env.get('HTTP_AUTHORIZATION','').split(' ',1)
            credentials = base64.b64decode(token,validate=True).decode()
            authenticated = scheme.lower() == 'basic' and hmac.compare_digest(credentials.encode(),(username+':'+password).encode())
        except (ValueError,UnicodeError):
            authenticated = False
        if auth_enabled and not authenticated:
            return respond('401 Unauthorized',{'error':'Sign in required'},extra=[('WWW-Authenticate','Basic realm="Family planner", charset="UTF-8"')])
        try:
            if path in ('/','/index.html') and method == 'GET':
                return respond('200 OK',index.read_bytes(),'text/html; charset=utf-8')
            if path == '/api/state' and method == 'GET':
                return respond('200 OK', store.read())
            if path == '/api/state' and method == 'PUT':
                # Custom header + JSON requests cannot be issued by a cross-origin HTML form.
                if env.get('HTTP_X_PLANNER_REQUEST') != '1' or env.get('CONTENT_TYPE','').split(';')[0] != 'application/json':
                    return respond('403 Forbidden',{'error':'Invalid request'})
                try:
                    length = int(env.get('CONTENT_LENGTH','0'))
                except ValueError:
                    return respond('400 Bad Request',{'error':'Invalid request length'})
                if not 0 < length <= MAX_BODY:
                    return respond('413 Payload Too Large',{'error':'Chart must be under 1 MB'})
                try:
                    data = json.loads(env['wsgi.input'].read(length))
                    if not isinstance(data,dict) or type(data.get('revision')) is not int or data['revision'] < 0 or not valid_state(data.get('state')):
                        raise ValueError()
                except (ValueError,TypeError,KeyError):
                    return respond('400 Bad Request',{'error':'Invalid chart data'})
                revision = store.write(data['revision'],data['state'])
                if revision is None:
                    return respond('409 Conflict',{'error':'Another device changed this chart. Back up unsaved changes, then reload.'})
                return respond('200 OK',{'revision':revision})
            return respond('404 Not Found',{'error':'Not found'})
        except (sqlite3.Error,OSError):
            return respond('503 Service Unavailable',{'error':'Storage unavailable; your changes were not saved.'})
    return app


if __name__ == '__main__':
    from waitress import serve
    config = load_runtime_env()
    if config.get('TZ'):
        os.environ['TZ'] = config['TZ']
        if hasattr(time, 'tzset'):
            time.tzset()
    application = configured_app(config)
    serve(application,host='0.0.0.0',port=int(config.get('PORT','8080')),threads=4,max_request_body_size=MAX_BODY)
