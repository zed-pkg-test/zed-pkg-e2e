"""Local HTTP fault-injection singleflight oracle, NOT the real zed-cli resolver."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter
import hashlib
import threading
import time
import urllib.error
import urllib.request

class ProbeError(Exception):pass
class DeadlineExceeded(ProbeError):pass
class FixtureHandler(BaseHTTPRequestHandler):
    counts=Counter()
    mutex=threading.Lock()
    def do_GET(self):
        name=self.path.removeprefix("/")
        with self.mutex:self.counts[name]+=1
        if name.startswith("fail-"):
            self.send_response(503);self.end_headers();return
        content=("immutable:"+name).encode()
        self.send_response(200);self.send_header("Content-Length",str(len(content)));self.end_headers()
        self.wfile.write(content)
    def log_message(self,*args):pass

class Singleflight:
    def __init__(self,origin,deadline_seconds=3):
        self.origin=origin
        self.deadline=time.monotonic()+deadline_seconds
        self.mutex=threading.Lock()
        self.events={}
        self.results={}
    def fetch(self,identity):
        if time.monotonic()>=self.deadline:raise DeadlineExceeded("end_to_end_deadline")
        with self.mutex:
            if identity in self.events:
                event=self.events[identity];leader=False
            else:
                event=threading.Event();self.events[identity]=event;leader=True
        if leader:
            try:
                remaining=self.deadline-time.monotonic()
                if remaining<=0:raise DeadlineExceeded("end_to_end_deadline")
                with urllib.request.urlopen(self.origin+"/"+identity,timeout=max(.01,remaining)) as response:
                    raw=response.read(1024)
                self.results[identity]=(raw,hashlib.sha256(raw).hexdigest(),None)
            except Exception as e:
                self.results[identity]=(None,None,e)
            finally:event.set()
        else:
            if not event.wait(timeout=max(0,self.deadline-time.monotonic())):
                raise DeadlineExceeded("end_to_end_deadline")
        raw,digest,error=self.results[identity]
        if error:raise ProbeError("source_failed:"+identity) from error
        return raw,digest

def run_fault_graph():
    FixtureHandler.counts=Counter()
    srv=ThreadingHTTPServer(("127.0.0.1",0),FixtureHandler)
    runner=threading.Thread(target=srv.serve_forever,daemon=True);runner.start()
    try:
        r=Singleflight("http://127.0.0.1:"+str(srv.server_address[1]))
        graph=["shared-a","shared-a","shared-b","fail-primary","fail-primary","fail-secondary"]*12
        def invoke(name):
            try:return r.fetch(name)
            except ProbeError:return None
        with ThreadPoolExecutor(max_workers=24) as pool:
            outcomes=list(pool.map(invoke,graph))
        return {"requests":dict(FixtureHandler.counts),"successes":sum(x is not None for x in outcomes),
                "unique_artifacts":len({result[1] for result in outcomes if result is not None}),
                "fixture_count":len(graph)}
    finally:srv.shutdown();srv.server_close();runner.join(timeout=2)
