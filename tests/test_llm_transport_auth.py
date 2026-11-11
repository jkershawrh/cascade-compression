"""Exercise HTTP/1.1 framing, not MockTransport, without external services."""
import http.server
import json
import threading

import httpx
import pytest

from cascade_compression.cascade.protocol import llm_complete


@pytest.fixture
def local_server():
    received = []
    settings = {'required_key': None}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            auth = self.headers.get('Authorization')
            received.append({'path': self.path, 'authorization': auth, 'body': json.loads(raw)})
            required = settings['required_key']
            status = 200 if required is None or auth == 'Bearer ' + required else 401
            body = json.dumps({'choices': [{'message': {'content': 'routine_noise'}}]}).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), received, settings
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_no_key_omits_authorization_on_real_http(local_server):
    url, received, _ = local_server
    with httpx.Client(timeout=2, trust_env=False) as client:
        assert llm_complete(client, url, '', 'fixture-model', [{'role': 'user', 'content': 'fixture'}]) == 'routine_noise'
    assert len(received) == 1
    assert received[0]['authorization'] is None
    assert received[0]['path'] == '/v1/chat/completions'


def test_supplied_key_is_preserved(local_server):
    url, received, settings = local_server
    settings['required_key'] = 'synthetic-test-key'
    with httpx.Client(timeout=2, trust_env=False) as client:
        assert llm_complete(client, url + '/v1', 'synthetic-test-key', 'fixture-model', []) == 'routine_noise'
    assert len(received) == 1
    assert received[0]['authorization'] == 'Bearer synthetic-test-key'


def test_missing_key_does_not_bypass_authentication(local_server):
    url, received, settings = local_server
    settings['required_key'] = 'synthetic-test-key'
    with httpx.Client(timeout=2, trust_env=False) as client, pytest.raises(httpx.HTTPStatusError) as caught:
        llm_complete(client, url, '', 'fixture-model', [])
    assert caught.value.response.status_code == 401
    assert len(received) == 1
    assert received[0]['authorization'] is None


def test_rejected_key_is_not_removed_or_retried(local_server):
    url, received, settings = local_server
    settings['required_key'] = 'synthetic-test-key'
    with httpx.Client(timeout=2, trust_env=False) as client, pytest.raises(httpx.HTTPStatusError) as caught:
        llm_complete(client, url, 'wrong-synthetic-key', 'fixture-model', [])
    assert caught.value.response.status_code == 401
    assert len(received) == 1
    assert received[0]['authorization'] == 'Bearer wrong-synthetic-key'
