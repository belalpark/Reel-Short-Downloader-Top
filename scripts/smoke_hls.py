"""Download a synthetic HLS VOD with bundled tools and compare its decoded video frames."""
from __future__ import annotations
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

from app.catalog import Episode
from app.downloader import DownloadQueue
from app.paths import write_json
from app.runtime import process_options


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    fixture=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/hls.json').read_text())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            name=urlsplit(self.path).path.lstrip('/')
            if name=='index.m3u8':body=fixture['playlist'].encode();kind='application/vnd.apple.mpegurl'
            elif name in fixture['segments']:body=base64.b64decode(fixture['segments'][name]);kind='video/mp2t'
            else:self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/index.m3u8?fixture=signed-query'
    try:
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);events=[]
            queue=DownloadQueue(args.root.resolve(),folder/'queue.json',events.append)
            item=Episode('a'*24,'Synthetic HLS',9,12,'',url,'playback_verified','media_found','playback_hls')
            queue.start([item],folder/'output');queue.thread.join(timeout=75)
            if queue.running:queue.cancel();raise RuntimeError('HLS download timed out')
            done=[e for e in events if e[0]=='done'][-1]
            assert done[1][0]['status']=='مكتمل ومفحوص', events
            file=Path(done[1][0]['file'])
            probe=subprocess.run([str(args.root/'tools/ffprobe.exe'),'-v','error','-show_entries','stream=codec_type,codec_name','-show_entries','format=duration','-of','json',str(file)],capture_output=True,text=True,timeout=30,check=True,**process_options())
            info=json.loads(probe.stdout)
            assert {'video','audio'}.issubset({s['codec_type'] for s in info['streams']})
            assert abs(float(info['format']['duration'])-8)<0.2,info
            def frames(source):
                result=subprocess.run([str(args.root/'tools/ffmpeg.exe'),'-v','error','-i',source,'-map','0:v:0','-f','framemd5','-'],capture_output=True,text=True,timeout=30,check=True,**process_options())
                return [line.rsplit(',',1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith('#')]
            original=frames(url);downloaded=frames(str(file))
            assert original==downloaded and len(downloaded)==80,(len(original),len(downloaded))
            write_json(args.report,{'actual_ytdlp_hls':True,'actual_ffmpeg':True,'actual_ffprobe':True,'video_and_audio_present':True,'decoded_frames_match_source':True,'matched_frames':len(downloaded),'episode':9,'duration_seconds':float(info['format']['duration']),'real_account_download_tested':False})
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
