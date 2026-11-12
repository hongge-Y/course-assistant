"""通过 HTTP 接口验证确认流程（不依赖网页）。"""
import json
import urllib.request

BASE = 'http://127.0.0.1:8765'


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode())


sid = post('/api/sessions', {'user_id': 'S002'})['session_id']
print('session', sid)
for message in ('我想选 C004', '我想选 C001', '确认', '我的选课'):
    result = post('/api/chat', {'session_id': sid, 'message': message, 'use_web': False})
    print(f'\n> {message}')
    print(json.dumps(result, ensure_ascii=False)[:500])
