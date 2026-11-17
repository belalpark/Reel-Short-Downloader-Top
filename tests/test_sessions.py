import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app import EXTENSION_VERSION
from app.automatic import AutomaticDownloads
from app.bridge import Bridge
from app.catalog import Episode
from app.downloader import DownloadQueue, build_command
from app.library_engine import LibraryEngine
from app.library_store import LibraryStore
from app.media_check import SourceCheck
from app.paths import write_json


def sid(n):
    return f'{n:024x}'


def page(n,ep=1):
    return f'https://www.reelshort.com/ar/episodes/episode-{ep}-fixture-{sid(n)}'


def state(n,total=3,run='',captured=(),finished=False):
    return {'channel':run,'desktopCommandId':run,'seriesId':sid(n),'title':f'Session {n}',
        'total':total,'current':total+1 if finished else 1,'running':not finished,'finished':finished,
        'rows':{str(ep):{'pageUrl':page(n,ep),'mediaUrl':f'https://v-mps.crazymaplestudios.com/{sid(n)}/{ep}.mp4' if ep in captured else '',
                       'captureId':run+'-'+str(ep),'playbackStatus':'playback_verified' if ep in captured else 'not_checked'} for ep in range(1,total+1)}}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=LibraryStore(self.root/'Data/library.sqlite')
        config=self.root/'connection.json';write_json(config,{'token':'s'*40,'extension_id':'a'*32})
        self.bridge=Bridge(config,inbox=self.root/'Inbox',library=self.store)
        self.bridge.snapshot_handler=self.store.ingest

    def tearDown(self):
        self.tmp.cleanup()

    def request(self,path,body,token='s'*40):
        return self.bridge.receive(path,'chrome-extension://'+'a'*32,'Bearer '+token,
            {'version':EXTENSION_VERSION,'extensionId':'a'*32,**body},'Edg/157')

    def test_three_sessions_do_not_mix_identical_episode_numbers_or_accept_late_senders(self):
        jobs=[]
        for n in range(1,4):
            self.store.ingest(state(n))
            book=next(b for b in self.store.series() if b['id']==sid(n))
            jobs.append(self.bridge.sessions.assign(n,book,targets=[1]))
        self.assertEqual(self.request('/sessions/poll',{},token='bad')[0],401)
        for n,job in enumerate(jobs,1):
            body={'sessionId':job['id'],'seriesId':sid(n),'tabId':n,'state':state(n,run=job['id'],captured=[1])}
            self.assertEqual(self.request('/sessions/snapshot',body)[0],200)
            self.assertEqual(self.store.episodes(sid(n))[0]['generation'],job['id']+'-1')
            self.assertEqual(self.request('/sessions/snapshot',{**body,'tabId':n+10})[0],409)
            self.assertEqual(self.request('/sessions/snapshot',{**body,'state':state(9,run=job['id'],captured=[1])})[0],409)
        old=jobs[0];self.bridge.sessions.retire(1)
        book=next(b for b in self.store.series() if b['id']==sid(1))
        new=self.bridge.sessions.assign(1,book,targets=[2])
        self.assertNotEqual(new['id'],old['id'])
        late={'sessionId':old['id'],'seriesId':sid(1),'tabId':1,'state':state(1,run=old['id'],captured=[2])}
        self.assertEqual(self.request('/sessions/snapshot',late)[0],409)
        self.assertEqual(self.store.episodes(sid(1))[1]['media_url'],'')

    def engine(self):
        auto=AutomaticDownloads(self.root,self.root/'Inbox',self.root/'Output',self.root/'Data',lambda _:None)
        verifier=Mock();verifier.check.return_value=SourceCheck(True,'verified_video',time.time())
        engine=LibraryEngine(self.store,self.bridge,auto,self.root,self.root/'Data',self.root/'Output',lambda _:None,
            session_hub=self.bridge.sessions,verifier=verifier,opener=Mock())
        engine.queue=Mock(running=False)
        self.bridge.snapshot_handler=engine.on_snapshot
        return engine

    def test_unresponsive_series_releases_its_slot_for_another_series_without_manual_relink(self):
        for n in range(1,5):self.store.ingest(state(n,total=1))
        engine=self.engine();engine.collecting=True;engine.scheduler.tick()
        jobs=self.bridge.sessions.snapshot();self.assertEqual(len(jobs),3)
        original={j['slot']:j for j in jobs}
        with self.bridge.sessions.lock:self.bridge.sessions.jobs[1]['started']-=200
        engine.scheduler.tick()
        now={j['slot']:j for j in self.bridge.sessions.snapshot()}
        self.assertEqual(now[1]['seriesId'],sid(4))
        self.assertEqual(now[2]['id'],original[2]['id'])
        self.assertEqual(now[3]['id'],original[3]['id'])
        self.assertGreater(self.store.episodes(sid(1))[0]['next_try'],time.time())

    def test_completed_capture_only_schedules_the_next_missing_batch(self):
        self.store.ingest(state(1,total=8));engine=self.engine();engine.collecting=True;engine.scheduler.tick()
        job=self.bridge.sessions.snapshot()[0];self.assertEqual(job['targets'],[1,2,3,4,5,6])
        body={'sessionId':job['id'],'seriesId':sid(1),'tabId':1,'state':state(1,total=8,run=job['id'],captured=range(1,7),finished=True)}
        self.assertEqual(self.request('/sessions/snapshot',body)[0],200)
        engine.scheduler.tick()
        with patch('app.session_scheduler.time.time',return_value=time.time()+2):engine.scheduler.tick()
        self.assertEqual(self.bridge.sessions.snapshot()[0]['targets'],[7,8])
        self.assertFalse(engine.queue.start.called,'Collection never waits for or fakes a completed file')

    def test_same_url_recapture_does_not_clear_retry_cooldown_or_overwrite_completed_files(self):
        self.store.ingest(state(1,total=1,run='first',captured=[1]));item=self.store.episodes(sid(1))[0]
        future=time.time()+60;self.store.record_failure(item,'http_forbidden',next_try=future)
        self.store.ingest(state(1,total=1,run='second',captured=[1]))
        row=self.store.episodes(sid(1))[0];self.assertEqual(row['next_try'],future);self.assertEqual(row['attempts'],1)
        self.store.complete(sid(1),1,'saved.webm')
        self.store.ingest(state(1,total=1,run='third',captured=[1]))
        self.assertEqual(self.store.episodes(sid(1))[0]['status'],'done')

    def test_database_upgrade_keeps_the_previous_library_and_completion_records(self):
        self.store.ingest(state(1,total=1));self.store.complete(sid(1),1,'saved.webm')
        with self.store.db() as db:db.execute('ALTER TABLE episodes DROP COLUMN user_agent')
        upgraded=LibraryStore(self.store.path)
        row=upgraded.episodes(sid(1))[0]
        self.assertEqual((row['status'],row['file'],row['user_agent']),('done','saved.webm',''))

    def test_resume_considers_older_incomplete_series_before_new_catalog_entries(self):
        self.store.ingest(state(1,total=1))
        with self.store.db() as db:db.execute('UPDATE series SET scanned=? WHERE id=?',(time.time()-10,sid(1)))
        self.store.ingest(state(2,total=1))
        engine=self.engine();engine.scheduler.slots=1;engine.collecting=True;engine.scheduler.tick()
        self.assertEqual(self.bridge.sessions.snapshot()[0]['seriesId'],sid(1))

    def test_failure_diagnostics_persist_the_reason_without_a_signed_media_url(self):
        self.store.ingest(state(1,total=1,run='source',captured=[1]))
        engine=self.engine();item=self.store.episodes(sid(1))[0]
        engine.diagnostic(item,'http_forbidden',http_status=403,detail='HTTP Error 403 https://v-mps.crazymaplestudios.com/1.mp4?secret=token')
        saved=json.loads((self.root/'Data/download_diagnostics.json').read_text())
        self.assertEqual(saved['events'][0]['http_status'],403)
        self.assertNotIn('secret',json.dumps(saved));self.assertNotIn('https://',json.dumps(saved))
        reopened=self.engine();self.assertEqual(reopened.diagnostics[0]['reason'],'http_forbidden')

    def test_stop_during_file_recovery_cannot_launch_a_late_browser_session(self):
        self.store.ingest(state(1,total=1));engine=self.engine();engine.collecting=engine.downloading=True
        entered,released=threading.Event(),threading.Event()
        def recover(*_):entered.set();released.wait(timeout=3);return []
        engine.automatic.recover_completed=recover
        thread=threading.Thread(target=engine.scheduler.tick);thread.start()
        self.assertTrue(entered.wait(timeout=2));engine.stop();released.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive());self.assertEqual(self.bridge.sessions.snapshot(),[])
        engine.opener.assert_not_called()


class DownloadWatchdogTests(unittest.TestCase):
    def test_silent_process_is_cancelled_and_the_next_series_still_downloads(self):
        real_popen=subprocess.Popen
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);good=root/('Fixture_'+sid(2))/'Episode_001.webm';good.parent.mkdir();good.write_bytes(b'x'*512)
            entries=[Episode(sid(n),'Fixture',1,1,page(n),f'https://v-mps.crazymaplestudios.com/{n}.webm') for n in (1,2)]
            def child(args,**kwargs):
                script='import time;time.sleep(30)' if args[-1].endswith('/1.webm') else 'print('+repr('RS_FILE:'+str(good))+')'
                return real_popen([sys.executable,'-u','-c',script],**kwargs)
            events=[];queue=DownloadQueue(root,root/'report.json',events.append,idle_timeout=0.3,total_timeout=3)
            started=time.monotonic()
            with patch('app.downloader.subprocess.Popen',side_effect=child),patch('app.downloader.validate_video',return_value=True):
                queue.start(entries,root);queue.thread.join(timeout=6)
            self.assertFalse(queue.running);self.assertLess(time.monotonic()-started,6)
            results=[e[1] for e in events if e[0]=='result']
            self.assertEqual(len(results),2);self.assertEqual(results[0]['reason'],'download_stalled')
            self.assertEqual(results[1]['series_id'],sid(2));self.assertEqual(results[1]['status'],'مكتمل ومفحوص')

    def test_media_headers_share_the_observed_user_agent_and_never_take_control_characters(self):
        item=Episode(sid(1),'Fixture',1,1,page(1),'https://v-mps.crazymaplestudios.com/1.mp4',user_agent='ObservedBrowser/1.0')
        args=build_command(Path('.'),Path('.'),item)
        self.assertIn('User-Agent:ObservedBrowser/1.0',args)
        self.assertIn('Origin:https://www.reelshort.com',args)
        bad=Episode(sid(1),'Fixture',1,1,page(1),item.media_url,user_agent='bad\nAuthorization:secret')
        self.assertNotIn('secret',' '.join(build_command(Path('.'),Path('.'),bad)))
