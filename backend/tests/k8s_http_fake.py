import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.schema_check import problems

RESOURCES = {
    "secrets": "V1Secret",
    "configmaps": "V1ConfigMap",
    "services": "V1Service",
    "persistentvolumeclaims": "V1PersistentVolumeClaim",
    "resourcequotas": "V1ResourceQuota",
    "limitranges": "V1LimitRange",
    "statefulsets": "V1StatefulSet",
    "deployments": "V1Deployment",
    "jobs": "V1Job",
    "ingresses": "V1Ingress",
}
PATH = re.compile(
    r"^/(?:api/v1|apis/[a-z0-9.]+/v1)/namespaces/(?P<ns>[^/]+)/(?P<res>[a-z]+)"
    r"(?:/(?P<name>[^/]+)(?:/(?P<sub>status|log))?)?$"
)
NS_PATH = re.compile(r"^/api/v1/namespaces(?:/(?P<name>[^/]+))?$")


class FakeApiServer:
    def __init__(self, ready_after: float = 0.0) -> None:
        self.ready_after = ready_after
        self.namespaces: dict[str, dict] = {}
        self.objects: dict[tuple[str, str, str], tuple[dict, float]] = {}
        self.requests: list[tuple[str, str]] = []
        self.rejections: list[str] = []
        self.job_fails = False
        self._lock = threading.Lock()
        handler = self._handler()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def start(self) -> "FakeApiServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def kinds(self, namespace: str) -> set[str]:
        return {kind for kind, ns, _ in self.objects if ns == namespace}

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict) -> None:
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _status(self, code: int, reason: str, message: str = "") -> None:
                self._send(
                    code,
                    {"kind": "Status", "status": "Failure", "reason": reason,
                     "message": message or reason, "code": code},
                )

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length", 0))
                return json.loads(self.rfile.read(length)) if length else {}

            def do_POST(self):
                with server._lock:
                    server.requests.append(("POST", self.path))
                    self._create(urlparse(self.path).path, self._body())

            def do_GET(self):
                with server._lock:
                    parsed = urlparse(self.path)
                    server.requests.append(("GET", parsed.path))
                    self._read(parsed.path, parse_qs(parsed.query))

            def do_DELETE(self):
                with server._lock:
                    path = urlparse(self.path).path
                    server.requests.append(("DELETE", path))
                    self._delete(path)

            def _create(self, path: str, body: dict) -> None:
                match = NS_PATH.match(path)
                if match:
                    found = problems(body, "V1Namespace")
                    if found:
                        return self._reject(found)
                    name = body["metadata"]["name"]
                    if name in server.namespaces:
                        return self._status(409, "AlreadyExists")
                    server.namespaces[name] = body
                    return self._send(201, body)

                match = PATH.match(path)
                if not match or match["res"] not in RESOURCES:
                    return self._status(404, "NotFound", path)
                ns, res = match["ns"], match["res"]
                found = problems(body, RESOURCES[res])
                if found:
                    return self._reject(found)
                if ns not in server.namespaces:
                    return self._status(404, "NotFound", f"namespace {ns}")
                if body["metadata"].get("namespace") not in (None, ns):
                    return self._status(400, "BadRequest", "namespace mismatch")
                key = (RESOURCES[res][2:], ns, body["metadata"]["name"])
                if key in server.objects:
                    return self._status(409, "AlreadyExists")
                server.objects[key] = (body, time.monotonic())
                self._send(201, body)

            def _reject(self, found: list[str]) -> None:
                server.rejections.extend(found)
                self._status(422, "Invalid", "; ".join(found))

            def _elapsed_ready(self, created: float) -> bool:
                return time.monotonic() - created >= server.ready_after

            def _read(self, path: str, query: dict) -> None:
                match = NS_PATH.match(path)
                if match and match["name"]:
                    ns = server.namespaces.get(match["name"])
                    if ns is None:
                        return self._status(404, "NotFound")
                    return self._send(200, ns)

                match = PATH.match(path)
                if not match:
                    return self._status(404, "NotFound", path)
                ns, res, name, sub = match["ns"], match["res"], match["name"], match["sub"]

                if res == "pods" and name is None:
                    items = [{"metadata": {"name": "wordpress-init-abcde"}}]
                    return self._send(200, {"kind": "PodList", "items": items})
                if res == "pods" and sub == "log":
                    return self._send_text("Error: could not download WooCommerce")
                if res not in RESOURCES:
                    return self._status(404, "NotFound", path)

                kind = RESOURCES[res][2:]
                entry = server.objects.get((kind, ns, name))
                if entry is None:
                    return self._status(404, "NotFound")
                body, created = entry
                if sub == "status":
                    ready = self._elapsed_ready(created)
                    if kind in ("StatefulSet", "Deployment"):
                        body = {**body, "status": {"replicas": 1, "readyReplicas": 1 if ready else 0}}
                    elif kind == "Job":
                        body = {**body, "status": self._job_status(ready)}
                if kind == "Secret":
                    import base64
                    data = {k: base64.b64encode(v.encode()).decode()
                            for k, v in body.get("stringData", {}).items()}
                    body = {**body, "data": data}
                self._send(200, body)

            def _job_status(self, ready: bool) -> dict:
                if server.job_fails:
                    return {"failed": 1, "conditions": [{"type": "Failed", "status": "True"}]}
                return {"succeeded": 1} if ready else {"active": 1}

            def _send_text(self, text: str) -> None:
                payload = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _delete(self, path: str) -> None:
                match = NS_PATH.match(path)
                if match and match["name"]:
                    name = match["name"]
                    if name not in server.namespaces:
                        return self._status(404, "NotFound")
                    del server.namespaces[name]
                    server.objects = {k: v for k, v in server.objects.items() if k[1] != name}
                    return self._send(200, {"kind": "Namespace", "metadata": {"name": name}})
                match = PATH.match(path)
                if match and match["res"] in RESOURCES and match["name"]:
                    key = (RESOURCES[match["res"]][2:], match["ns"], match["name"])
                    if server.objects.pop(key, None) is None:
                        return self._status(404, "NotFound")
                    return self._send(200, {"kind": "Status", "status": "Success"})
                self._status(404, "NotFound", path)

        return Handler
