"""Real session protocol/extension/SQLite scheduler with synthetic player and CDN."""
import base64
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from app.automatic import AutomaticDownloads, provision_extension
from app.bridge import Bridge
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.media_check import SourceCheck

OUTPUT_LOCK=threading.Lock()


def send(value):
    with OUTPUT_LOCK:
        sys.stdout.write(json.dumps(value)+'\n');sys.stdout.flush()


class Verifier:
    def check(self,*_):
        return SourceCheck(True,'verified_video',time.time())


class QueueSpy:
    running=False
    cancelled=False

    def __init__(self,engine,payload):
        self.engine,self.payload=engine,payload
        self.failed=False

    def start(self,entries,destination):
        self.running=True;self.cancelled=False
        send({'queued':[{'series_id':e.series_id,'episode':e.episode,'capture_id':e.capture_id,'media_url':e.media_url} for e in entries]})
        def finish():
            try:
                time.sleep(0.05)
                for entry in entries:
                    bad=entry.series_id==f'{1:024x}' and entry.episode==2 and not self.failed
                    if bad:self.failed=True
                    path=destination/(entry.title+'_'+entry.series_id)/f'Episode_{entry.episode:03d}.webm'
                    if not bad:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(self.payload)
                    result={'series_id':entry.series_id,'episode':entry.episode,'capture_id':entry.capture_id,
                        'status':'فشل' if bad else 'مكتمل ومفحوص','file':'' if bad else str(path),
                        'reason':'http_forbidden' if bad else '', 'http_status':403 if bad else 0,'detail':'HTTP Error 403' if bad else ''}
                    self.engine._download_event(('result',result))
                    send({'download_result':{'series_id':entry.series_id,'episode':entry.episode,'success':not bad}})
            finally:self.running=False
        threading.Thread(target=finish,daemon=True).start()

    def cancel(self):
        self.cancelled=True


with tempfile.TemporaryDirectory() as name:
    root=Path(name);package=Path(__file__).resolve().parents[1]
    provision_extension(package/'extension',root/'Extension',root/'connection.json')
    store=LibraryStore(root/'Data/library.sqlite')
    for n in range(1,4):
        sid=f'{n:024x}'
        store.ingest({'seriesId':sid,'title':f'Trigger {n}','total':3,
            'rows':{str(ep):{'pageUrl':f'https://www.reelshort.com/ar/episodes/episode-{ep}-trigger-{sid}'} for ep in range(1,4)}})
    auto=AutomaticDownloads(package,root/'Inbox',root/'Output',root/'Data',lambda _:None)
    auto.recover_completed=lambda sid,*_:auto.completed_episodes(sid)
    bridge=Bridge(root/'connection.json',inbox=root/'Inbox',library=store)
    engine=LibraryEngine(store,bridge,auto,package,root/'Data',root/'Output',lambda _:None,
        verifier=Verifier(),opener=lambda url:send({'open_request':url}),session_hub=bridge.sessions)
    payload=base64.b64decode((package/'tests/fixture.webm.b64').read_text())
    engine.queue=QueueSpy(engine,payload)
    stats={'snapshots':0,'source_sessions':{},'maximum_concurrent_sessions':0}
    def snapshot(value):
        sid=value['seriesId'];channel=value['channel']
        assert channel not in stats['source_sessions'] or stats['source_sessions'][channel]==sid
        for row in value.get('rows',{}).values():
            if row.get('mediaUrl'):assert sid in row['mediaUrl'],'Wrong-series source reached the receiver'
        stats['source_sessions'][channel]=sid;stats['snapshots']+=1
        stats['maximum_concurrent_sessions']=max(stats['maximum_concurrent_sessions'],len(bridge.sessions.snapshot()))
        engine.on_snapshot(value)
    bridge.snapshot_handler=snapshot
    bridge.start();engine.start_worker()
    send({'extension_path':str(root/'Extension')})
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    try:
        for line in sys.stdin:
            request=json.loads(line)
            if request['command']=='collect':engine.start_collection()
            elif request['command']=='download':engine.start_downloads()
            elif request['command']=='stop':engine.stop()
            elif request['command']=='status':
                send({'status':{'series':store.series(),'jobs':bridge.sessions.snapshot(),
                    'downloading':engine.downloading,'collecting':engine.collecting,'stats':stats}})
    finally:
        engine.close();bridge.close()
