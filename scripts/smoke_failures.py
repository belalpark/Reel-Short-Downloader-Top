"""Real bundled downloader: required headers, HTTP 403, a hung transfer, next-series progress."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from app.catalog import Episode
from app.downloader import DownloadQueue
from app.media_check import MediaVerifier
from app.paths import write_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    payload=base64.b64decode((Path(__file__).resolve().parents[1]/'tests/fixture.webm.b64').read_text())
    expected_agent='ReelDownloadFixture/4.0';headers_seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass

        def do_GET(self):
            name=urlsplit(self.path).path
            headers_seen.append((name,self.headers.get('User-Agent'),self.headers.get('Origin')))
            if name=='/denied.webm' or self.headers.get('User-Agent')!=expected_agent or self.headers.get('Origin')!='https://www.reelshort.com':
                self.send_error(403);return
            self.send_response(200);self.send_header('Content-Type','video/webm')
            self.send_header('Content-Length',str(1000000 if name=='/stalled.webm' else len(payload)));self.end_headers()
            if name=='/stalled.webm':time.sleep(25);return
            self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);base=f'http://127.0.0.1:{server.server_port}'
            verifier=MediaVerifier(allow_loopback=True)
            check=verifier.check(base+'/good.webm','',expected_agent);assert check.ok,check
            denied=verifier.check(base+'/denied.webm','',expected_agent);assert denied.reason=='http_forbidden',denied
            entries=[Episode(f'{n:024x}','Failure fixture',1,1,'',base+'/'+file,user_agent=expected_agent) for n,file in enumerate(['denied.webm','stalled.webm','good.webm'],1)]
            events=[];queue=DownloadQueue(args.root.resolve(),root/'report.json',events.append,idle_timeout=8,total_timeout=25)
            started=time.monotonic();queue.start(entries,root/'Output');queue.thread.join(timeout=65)
            assert not queue.running,'Failure fixture queue remained stuck'
            results=[e[1] for e in events if e[0]=='result'];assert len(results)==3,results
            assert results[0]['reason']=='http_forbidden',results
            assert results[1]['reason']=='download_stalled',results
            assert results[2]['status']=='مكتمل ومفحوص',results
            file=Path(results[2]['file']);assert hashlib.sha256(file.read_bytes()).digest()==hashlib.sha256(payload).digest()
            assert time.monotonic()-started<60
            assert all(agent==expected_agent and origin=='https://www.reelshort.com' for _,agent,origin in headers_seen)
            write_json(args.report,{'actual_bundled_ytdlp':True,'actual_bundled_ffprobe':True,
                'same_non_secret_headers_for_probe_and_download':True,'http_403_classified':True,
                'silent_transfer_cancelled_by_watchdog':True,'next_series_completed_without_relink':True,
                'good_video_sha256_matches':True,'browser_cookies_read':False,'real_account_tested':False})
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
