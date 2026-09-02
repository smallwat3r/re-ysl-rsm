import app
from app import PORT, Handler


class Sock:
    def __init__(self, ip):
        self.ip = ip

    def getsockname(self):
        return (self.ip, PORT)


class Stub(Handler):
    # skip the socket machinery, only headers and the local address matter
    def __init__(self, headers, local_ip="127.0.0.1"):
        self.headers = headers
        self.connection = Sock(local_ip)


def check(headers, local_ip="127.0.0.1"):
    return Stub(headers, local_ip)._local_only()


def test_local_only():
    host = f"127.0.0.1:{PORT}"
    assert check({"Host": host})  # curl-style, no Origin
    assert check({"Host": host, "Origin": f"http://{host}"})  # browser same-origin POST
    assert check({"Host": f"localhost:{PORT}", "Origin": f"http://localhost:{PORT}"})
    assert not check({"Host": host, "Origin": "http://evil.test"})  # cross-site
    assert not check({"Host": "rebind.evil.test"})  # DNS rebinding


def test_lan_hosts_need_no_config():
    lan = f"{app.HOSTNAME}.local:{PORT}"
    assert check({"Host": lan, "Origin": f"http://{lan}"})  # mDNS name
    assert check({"Host": f"10.42.0.1:{PORT}"}, "10.42.0.1")  # IP we were reached on
    assert not check({"Host": f"10.42.0.1:{PORT}"}, "192.168.1.5")  # not this address
    assert not check({"Host": lan, "Origin": "http://evil.test"})  # rebinding blocked
    assert not check({"Host": "null"}, "10.42.0.1")  # opaque origin / junk host


class Routed(Handler):
    """Drive do_POST without a socket: capture the reply code, never run hardware."""

    def __init__(self, path, body=b"{}"):
        import io

        self.path = path
        self.headers = {"Host": f"127.0.0.1:{PORT}", "Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.connection = Sock("127.0.0.1")
        self.code = None

    def _reply(self, code, body, ctype="application/json"):
        self.code = code


def post(path, body=b"{}"):
    h = Routed(path, body)
    h.do_POST()
    return h.code


def test_post_routing(monkeypatch):
    ran = []
    monkeypatch.setattr(app.DEV, "run", lambda coro: (coro.close(), ran.append(1)))
    assert post("/nope") == 404
    assert post("/refresh") == 409  # not connected
    assert post("/connect") == 202
    app.DEV.state.update(connected=True)
    assert post("/dispense", b'{"amounts": "x"}') == 400
    app.DEV.state.update(busy=True)
    assert post("/connect") == 409
    app.DEV.state.update(connected=False, busy=False)
    assert ran == [1]
