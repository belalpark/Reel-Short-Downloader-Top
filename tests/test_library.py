import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from app import EXTENSION_VERSION
from app.automatic import AutomaticDownloads
from app.bridge import Bridge
from app.library_store import LibraryStore, series_page
from app.library_engine import LibraryEngine
from app.media_check import MediaVerifier, SourceCheck, source_expiry, trusted_source
from app.paths import write_json

SID='a'*24
PAGE=f'https://www.reelshort.com/ar/episodes/episode-1-fixture-{SID}'
MEDIA='https://v-mps.crazymaplestudios.com/library/episode1.mp4'


def snapshot(generation='capture-1',media=MEDIA):
    return {'seriesId':SID,'title':'Library fixture','total':2,'rows':{
        '1':{'pageUrl':PAGE,'mediaUrl':media,'captureId':generation,'playbackStatus':'playback_verified'},
        '2':{'pageUrl':PAGE.replace('episode-1-','episode-2-'),'hlsCandidates':[MEDIA]}}}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=LibraryStore(self.root/'library.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_deduplicates_sixty_series_and_rejects_invalid_batches_atomically(self):
        entries=[{'id':f'{n:024x}','title':f'Series {n}','pageUrl':f'https://www.reelshort.com/ar/episodes/episode-1-fixture-{n:024x}'} for n in range(1,61)]
        self.store.add_series(entries);self.store.add_series(entries)
        self.assertEqual(len(self.store.series()),60)
        with self.assertRaises(ValueError):
            self.store.add_series([{'id':'f'*24,'title':'bad','pageUrl':'https://evil.example/'+('f'*24)}])
        self.assertEqual(len(self.store.series()),60)

    def test_candidates_and_unplayed_sources_are_not_promoted_to_library_downloads(self):
        state=snapshot();state['rows']['1']['playbackStatus']='playback_not_started'
        self.store.ingest(state)
        self.assertEqual(self.store.episodes(SID)[0]['status'],'missing')
        self.assertEqual(self.store.episodes(SID)[1]['media_url'],'')

    def test_replayed_snapshot_does_not_reset_verification_or_repeat_capture(self):
        self.store.ingest(snapshot())
        item=self.store.next_source('captured')
        self.store.update_source(item,'ready',verified=123)
        self.store.ingest(snapshot())
        row=self.store.episodes(SID)[0]
        self.assertEqual((row['status'],row['verified']),('ready',123))

    def test_stale_probe_cannot_invalidate_a_new_rotated_source(self):
        self.store.ingest(snapshot());old=self.store.next_source('captured')
        self.store.ingest(snapshot('new',MEDIA+'?token=rotated'))
        self.store.update_source(old,'needs_refresh',error='expired')
        self.assertEqual(self.store.episodes(SID)[0]['status'],'captured')
        self.assertEqual(self.store.episodes(SID)[0]['generation'],'new')

    def test_reopening_store_retains_library_and_recovers_only_interrupted_work(self):
        self.store.ingest(snapshot());item=self.store.next_source('captured')
        self.store.update_source(item,'downloading',verified=time.time())
        reopened=LibraryStore(self.store.path)
        self.assertEqual(reopened.episodes(SID)[0]['status'],'downloading')
        reopened.recover_interrupted()
        self.assertEqual(reopened.episodes(SID)[0]['status'],'captured')

    def test_expired_sources_are_not_counted_as_ready_or_skipped_during_collection(self):
        self.store.ingest(snapshot(media=MEDIA+'?Expires=1000000001'))
        item=self.store.next_source('captured');self.store.update_source(item,'ready',verified=time.time())
        self.assertEqual(self.store.series()[0]['ready'],0)
        self.assertEqual(self.store.skip_sources(SID),[])

    def test_series_landing_page_survives_an_expired_episode_url(self):
        state=snapshot();state['seriesPageUrl']=f'https://www.reelshort.com/ar/movie/fixture-{SID}'
        self.store.ingest(state)
        self.assertIn('/movie/',self.store.series()[0]['page_url'])
        self.assertEqual(series_page('https://www.reelshort.com.evil/episodes/episode-1-'+SID),'')

    def engine(self):
        auto=AutomaticDownloads(self.root,self.root/'Inbox',self.root/'Videos',self.root/'Data',lambda e:None)
        bridge=Mock()
        bridge.status.return_value={'connected':False,'page_url':'','pending_kind':'','command_error':''}
        verifier=Mock();verifier.check.return_value=SourceCheck(True,'verified_video',time.time())
        opener=Mock()
        engine=LibraryEngine(self.store,bridge,auto,self.root,self.root/'Data',self.root/'Videos',lambda e:None,verifier=verifier,opener=opener)
        engine.queue=Mock(running=False)
        return engine,opener,verifier

    def test_ready_library_download_starts_without_browser_or_connection(self):
        state=snapshot();state['total']=1
        self.store.ingest(state);item=self.store.next_source('captured');self.store.update_source(item,'ready',verified=time.time())
        engine,opener,_=self.engine();engine.start_downloads();engine.tick()
        opener.assert_not_called();engine.bridge.command.assert_not_called()
        self.assertEqual(engine.queue.start.call_args.args[0][0].series_id,SID)

    def test_failed_probe_never_submits_an_expired_source_to_the_downloader(self):
        state=snapshot();state['total']=1
        self.store.ingest(state)
        engine,opener,verifier=self.engine()
        verifier.check.return_value=SourceCheck(False,'source_expired',time.time())
        engine.downloading=True;engine.tick()
        engine.queue.start.assert_not_called()
        self.assertEqual(self.store.episodes(SID)[0]['status'],'needs_refresh')
        engine.tick();opener.assert_not_called()
        with self.store.db() as db:
            db.execute('UPDATE episodes SET next_try=0 WHERE sid=?',(SID,))
        engine.tick();opener.assert_called_once()

    def test_explicit_stop_and_application_close_have_distinct_resume_intent(self):
        engine,_,_=self.engine();engine.downloading=True;self.store.save_setting('resume_downloads',True)
        engine.pause_for_exit()
        self.assertTrue(self.store.setting('resume_downloads'))
        engine,_,_=self.engine();engine.stop()
        self.assertFalse(self.store.setting('resume_downloads'))

    def test_reconnecting_extension_suspends_browser_refresh_but_keeps_valid_downloads(self):
        state=snapshot();self.store.ingest(state)
        item=self.store.episodes(SID)[0];self.store.update_source(item,'ready',verified=time.time())
        missing=self.store.episodes(SID)[1];self.store.update_source(missing,'needs_refresh')
        engine,opener,_=self.engine();engine.downloading=True;engine.suspend_browser=True
        engine.tick();opener.assert_not_called();engine.queue.start.assert_called_once()
        engine.suspend_browser=False;engine.tick();opener.assert_called_once()

    def test_catalog_timeout_counts_idle_time_not_total_duration_of_active_shelves(self):
        engine,_,_=self.engine();engine.discovery_id='catalog';engine.discovery_started=time.time()-400
        engine.bridge.catalog_status.return_value={'id':'catalog','done':False,'updated':time.time()-10}
        engine._capture_tick();self.assertEqual(engine.discovery_id,'catalog')
        engine.bridge.command.assert_not_called()
        engine.bridge.catalog_status.return_value={'id':'catalog','done':False,'updated':time.time()-200}
        engine._capture_tick();self.assertEqual(engine.discovery_id,'')
        engine.bridge.command.assert_called_once_with('stop')

    def test_completed_file_is_skipped_without_even_rechecking_its_media_url(self):
        state=snapshot();state['total']=1;self.store.ingest(state)
        engine,opener,verifier=self.engine()
        file=self.root/'Videos/movie.webm';file.parent.mkdir();file.write_bytes(b'x'*256)
        engine.automatic.record_library_completion(SID,1,file)
        engine.downloading=True;engine.tick()
        verifier.check.assert_not_called();opener.assert_not_called();engine.queue.start.assert_not_called()
        self.assertEqual(self.store.episodes(SID)[0]['status'],'done')

    def test_catalog_receiver_requires_the_active_job_and_reconnect_requires_a_new_heartbeat(self):
        credentials=self.root/'connection.json';write_json(credentials,{'token':'s'*40,'extension_id':'a'*32})
        bridge=Bridge(credentials,library=self.store)
        body={'version':EXTENSION_VERSION,'extensionId':'a'*32}
        origin='chrome-extension://'+'a'*32
        bridge.receive('/connect',origin,'Bearer '+'s'*40,body,'Edg/157')
        self.assertTrue(bridge.status()['connected']);bridge.require_fresh_heartbeat();self.assertFalse(bridge.status()['connected'])
        job=bridge.command('catalog',page_url='https://www.reelshort.com/ar')
        item={'id':SID,'title':'test','pageUrl':PAGE}
        code,_=bridge.receive('/library/catalog',origin,'Bearer '+'s'*40,{**body,'jobId':'old','items':[item]},'Edg/157')
        self.assertEqual(code,409)
        code,_=bridge.receive('/library/catalog',origin,'Bearer '+'s'*40,{**body,'jobId':job,'items':[item],'done':True},'Edg/157')
        self.assertEqual(code,200);self.assertTrue(bridge.catalog_status()['done'])
        bridge.command('stop')
        self.assertEqual(bridge.receive('/library/catalog',origin,'Bearer '+'s'*40,{**body,'jobId':job,'items':[item]},'Edg/157')[0],409)


class SourceTests(unittest.TestCase):
    def test_expiry_can_be_known_or_unknown_but_is_never_assumed_permanent(self):
        self.assertEqual(source_expiry(MEDIA),0)
        self.assertEqual(source_expiry(MEDIA+'?Expires=2000000000'),2000000000)
        self.assertEqual(source_expiry(MEDIA+'?X-Amz-Date=20260101T000000Z&X-Amz-Expires=60'),1767225660)
        self.assertFalse(trusted_source('https://127.0.0.1/video.mp4'))
        self.assertFalse(trusted_source('https://v-mps.crazymaplestudios.com.evil/video.mp4'))

    def probe(self,body,url=MEDIA):
        response=Mock(status=200,url=url);response.read.return_value=body
        manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=manager
        with patch('app.media_check.build_opener',return_value=opener):
            return MediaVerifier().check(url)

    def test_http_success_with_an_html_error_page_is_not_a_verified_video(self):
        self.assertFalse(self.probe(b'<html>expired</html>').ok)
        self.assertTrue(self.probe(b'\x00\x00\x00\x18ftypisom').ok)

    def test_hls_rejects_drm_and_foreign_or_private_segment_locations(self):
        base=b'#EXTM3U\n#EXTINF:4,\nsegment1.ts\n#EXT-X-ENDLIST\n'
        url=MEDIA.replace('.mp4','.m3u8')
        self.assertTrue(self.probe(base,url).ok)
        self.assertFalse(self.probe(base.replace(b'segment1.ts',b'http://127.0.0.1/private.ts'),url).ok)
        self.assertFalse(self.probe(base+b'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"\n',url).ok)


if __name__=='__main__':
    unittest.main()
