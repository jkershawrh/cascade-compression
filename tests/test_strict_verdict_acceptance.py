"""Local TLS gateway fixture; the classifier result and ledger service are synthetic."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import certifi
import pytest

from cascade_compression.bridge import CascadeBridge
from tests.test_positive_lifecycle import activate
from tests.test_positive_lifecycle import bridge as qualified_bridge


@pytest.fixture
def tls_gateway(tmp_path):
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local acceptance run CA")])
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    base = x509.CertificateBuilder().issuer_name(issuer).not_valid_before(
        now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
    ca = (base.subject_name(issuer).public_key(ca_key.public_key()).serial_number(1)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                         critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (base.subject_name(subject).public_key(server_key.public_key()).serial_number(2)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
            .sign(ca_key, hashes.SHA256()))
    ca_path, leaf_path, key_path = [tmp_path / name for name in ("ca.crt", "leaf.crt", "leaf.key")]
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
    rows, calls = [], []
    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer local-test":
                self.send_error(401)
                return
            url = urlsplit(self.path)
            if url.path != "/api/entries":
                self.send_error(404)
                return
            q = {key: values[0] for key, values in parse_qs(url.query).items()}
            calls.append(q)
            selected = [r for r in rows if r["entry_type"].startswith(q["entry_type"])
                        and r["source_id"] == q["source_id"] and r["agent_id"] == q["agent_id"]
                        and int(q["from_ts"]) <= r["written_ts"] <= int(q["to_ts"])]
            pos = int(q.get("page_token", "0"))
            entries = selected[pos:pos + int(q["page_size"])]
            end = pos + len(entries)
            data = json.dumps(dict(entries=entries, total_count=len(selected),
                                   next_page_token=str(end) if end < len(selected) else "")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", str(ca_path), rows, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("minutes", [5, 25])
def test_authenticated_saved_watermark_demotes_and_proves_entry_after_restart(
        minutes, tls_gateway, qualified_bridge, monkeypatch):
    url, ca, rows, calls = tls_gateway
    monkeypatch.setenv("CASCADE_VERDICT_PAGE_SIZE", "1")
    monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    first = qualified_bridge
    activate(first, monkeypatch)
    ts = int(time.time() * 1000) - minutes * 60000
    first._verdict_watermark = ts - 1
    assert first._save_state()
    first.close()
    for position, outcome in enumerate(("SURVIVES", "FAILS"), start=1):
        rows.append(dict(entry_id=str(uuid4()), entry_type="audit.verdict", agent_id="governed-cognitive-loop",
                         source_id="gcl-cascade-audit", correlation_id=str(uuid4()), written_ts=ts,
                         chain_position=position, content=dict(verdict=outcome, domain=first.domain,
                         original_agent="synthetic_rule", subject_type="routine", decision_ref=str(uuid4()))))
    passing, failing = rows

    def restore():
        return CascadeBridge(domain=first.domain, ledger_url=url, ledger_token="local-test")

    b = restore()
    try:
        assert b._activated_types == {"routine"} and not b._verdict_authority_ready()
        monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
        assert not b._poll_gcl_verdicts() and not calls  # Wrong CA stops before GET.
        monkeypatch.setenv("SSL_CERT_FILE", ca)
        assert not b._poll_gcl_verdicts()
        assert b.get_stats()["verdict_catchup_error"] == "page_limit"
        assert int(calls[0]["from_ts"]) == ts - 1 and calls[0]["include_content_raw"] == "false"
        assert b.get_verdict_receipt(passing["entry_id"])["disposition"] == "observed_survives"
        assert b.get_verdict_receipt(failing["entry_id"]) is None
        assert b._activated_types == {"routine"}
    finally:
        b.close()

    b = restore()
    try:
        # The producer for outgoing promotion telemetry is independent of this reader.
        monkeypatch.setattr(b, "_enqueue_ledger_entry", lambda entry: True)
        assert b._poll_gcl_verdicts()
        assert calls[1]["page_token"] == "1" and calls[1]["to_ts"] == calls[0]["to_ts"]
        assert b._fn_count == 1 and b._agent_metrics["synthetic_rule"].deactivated
        assert not b._activated_types and not b._qualification_evidence.get("agent:synthetic_rule")
        receipt = b.get_verdict_receipt(failing["entry_id"])
        assert receipt["disposition"] == "applied_fails" and receipt["verdict"] == "FAILS"
        assert receipt["decision_ref"] == failing["content"]["decision_ref"]
        assert receipt["correlation_id"] == failing["correlation_id"]
        snapshot = json.loads(Path(b._state_file).read_text())
        assert snapshot["verdict_watermark"] == ts and snapshot["verdict_cursor"] is None
        assert failing["entry_id"] in snapshot["verdict_seen_ids"]
        assert "routine" not in snapshot["activated_agents"]
    finally:
        b.close()

    b = restore()
    try:
        assert not b._verdict_authority_ready()
        assert b.get_verdict_receipt(failing["entry_id"]) == receipt
        monkeypatch.setenv("CASCADE_VERDICT_MAX_PAGES", "2")
        assert b._poll_gcl_verdicts() and b._fn_count == 0
        b._discover_and_promote()
        assert not b._activated_types and not any(b._activated_contexts.values())
    finally:
        b.close()
