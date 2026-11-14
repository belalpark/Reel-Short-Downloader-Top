from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app import EXTENSION_VERSION
from app.bridge import Bridge
from app.catalog import Episode, load_catalog, safe_name, valid_media_url
from app.downloader import DownloadQueue, build_command, redact, validate_video
from app.runtime import verify_integrity

SID = "0123456789abcdef01234567"
EXT_ID = "a" * 32
ORIGIN = "chrome-extension://" + EXT_ID
EDGE = "Mozilla/5.0 Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "index.csv"

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, count=45):
        return [{"SeriesID": SID, "SeriesTitle": "مسلسل تجريبي", "Episode": str(n), "TotalEpisodes": str(count), "PageURL": f"https://www.reelshort.com/episodes/episode-{n}-series-{SID}", "MediaURL": f"https://cdn.example.org/{n}.mp4?token=private" if n <= 8 else ""} for n in range(1, count + 1)]

    def save(self, rows):
        with self.path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def test_previous_45_episode_8_media_catalog(self):
        self.save(self.rows())
        data = load_catalog(self.path)
        self.assertEqual(len(data), 45)
        self.assertEqual(sum(bool(r.media_url) for r in data), 8)
        self.assertEqual(data[0].title, "مسلسل تجريبي")

    def test_hls_candidate_file_is_not_downloadable(self):
        self.path.write_text("Episode,HLSCandidateURL\n1,https://cdn.example.org/x.m3u8", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HLS"):
            load_catalog(self.path)

    def test_duplicate_episode_rejected(self):
        rows = self.rows(2)
        rows[1] = rows[0]
        self.save(rows)
        with self.assertRaises(ValueError):
            load_catalog(self.path)

    def test_mixed_series_rejected(self):
        rows = self.rows(2)
        rows[1]["SeriesID"] = "f" * 24
        self.save(rows)
        with self.assertRaises(ValueError):
            load_catalog(self.path)

    def test_mismatched_total_rejected(self):
        rows = self.rows(2)
        rows[1]["TotalEpisodes"] = "3"
        self.save(rows)
        with self.assertRaises(ValueError):
            load_catalog(self.path)

    def test_cross_episode_media_with_rotated_token_rejected(self):
        rows = self.rows(2)
        rows[1]["MediaURL"] = "https://cdn.example.org/1.mp4?token=rotated"
        self.save(rows)
        with self.assertRaisesRegex(ValueError, "أكثر من حلقة"):
            load_catalog(self.path)

    def test_foreign_or_wrong_episode_page_rejected(self):
        for page in (f"https://evil.example/episodes/episode-1-{SID}", f"https://www.reelshort.com/episodes/episode-2-{SID}", f"https://user@www.reelshort.com/episodes/episode-1-{SID}"):
            rows = self.rows(1)
            rows[0]["PageURL"] = page
            self.save(rows)
            with self.subTest(page=page), self.assertRaises(ValueError):
                load_catalog(self.path)

    def test_url_validation_and_signed_query(self):
        good = "https://cdn.example.org/video.m3u8?token=a%26b"
        self.assertEqual(valid_media_url(good), good)
        for bad in ("file:///etc/passwd", "https://127.0.0.1/v.mp4", "https://localhost/v.mp4", "https://foo.local/v.mp4", "https://user:password@cdn.example.org/v.mp4", "http://cdn.example.org/v.mp4", "https://cdn.example.org/undefined/x.mp4", "https://cdn.example.org/v.mp4\n--exec=bad"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                valid_media_url(bad)

    def test_windows_filenames(self):
        for bad in ("CON", "con.txt", "NUL", "COM1.mov", "../../"):
            result = safe_name(bad)
            self.assertNotIn("/", result)
            self.assertNotEqual(result.upper().split(".")[0], "CON")
        self.assertEqual(safe_name("الاسم"), "الاسم")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "connection.json"
        self.bridge = Bridge(self.path, port=0)
        self.bridge.start()
        self.code = self.bridge.enable_pairing()

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def request(self, path="/pair", code=None, token="", origin=ORIGIN, agent=EDGE, method="POST", host=None, version=EXTENSION_VERSION):
        body = json.dumps({"extensionId": EXT_ID, "version": version, "code": code if code is not None else self.code})
        conn = http.client.HTTPConnection("127.0.0.1", self.bridge.port, timeout=5)
        headers = {"Origin": origin, "User-Agent": agent, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if host:
            headers["Host"] = host
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read())
        cors = response.getheader("Access-Control-Allow-Origin")
        conn.close()
        return response.status, data, cors

    def test_only_loopback_is_bound(self):
        self.assertEqual(self.bridge.server.server_address[0], "127.0.0.1")

    def test_immediate_restart_after_authenticated_request(self):
        _, data, _ = self.request()
        self.assertEqual(self.request('/heartbeat', token=data['token'])[0], 200)
        port = self.bridge.port
        self.bridge.close()
        self.bridge = Bridge(self.path, port=port)
        self.bridge.start()
        self.assertEqual(self.request('/heartbeat', token=data['token'])[0], 200)

    def test_second_listener_cannot_take_the_running_port(self):
        other = Bridge(self.path, port=self.bridge.port)
        try:
            with self.assertRaises(OSError):
                other.start()
        finally:
            other.close()

    def test_pair_persist_and_authenticated_heartbeat(self):
        status, data, cors = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(cors, ORIGIN)
        self.assertTrue(self.bridge.status()["connected"])
        self.assertNotIn(self.code, self.path.read_text())
        self.assertEqual(self.request("/heartbeat", token=data["token"])[0], 200)
        self.assertEqual(self.request("/heartbeat", token="wrong")[0], 401)
        restarted = Bridge(self.path)
        self.assertFalse(restarted.status()["connected"])
        self.assertEqual(restarted.receive("/heartbeat", ORIGIN, "Bearer " + data["token"], {"version": EXTENSION_VERSION, "extensionId": EXT_ID}, EDGE)[0], 200)
        self.assertTrue(restarted.status()["connected"])

    def test_webpage_origin_and_dns_rebinding_rejected(self):
        status, _, cors = self.request(origin="https://www.reelshort.com")
        self.assertEqual(status, 403)
        self.assertIsNone(cors)
        self.assertEqual(self.request(host="attacker.example:18765")[0], 403)

    def test_five_wrong_attempts_lock_pairing(self):
        wrong = "99999999" if self.code != "99999999" else "00000000"
        for _ in range(5):
            self.assertEqual(self.request(code=wrong)[0], 403)
        self.assertEqual(self.request()[1]["error"], "pairing_expired_or_locked")

    def test_expired_code_rejected(self):
        self.bridge.pair_until = 0
        self.assertEqual(self.request()[0], 403)

    def test_new_code_invalidates_previous(self):
        self.bridge.enable_pairing()
        if self.code != self.bridge.pair_code:
            self.assertEqual(self.request()[0], 403)

    def test_chrome_does_not_count_as_edge(self):
        self.assertEqual(self.request(agent="Chrome/144.0")[0], 200)
        self.assertFalse(self.bridge.status()["connected"])

    def test_old_extension_rejected(self):
        self.assertEqual(self.request(version="5.3.0")[0], 409)

    def test_stale_connection_does_not_count_as_active(self):
        self.request()
        self.bridge.last_seen -= 76
        self.assertFalse(self.bridge.status()["connected"])

    def test_pair_code_cannot_be_read_over_http(self):
        status, data, _ = self.request(method="GET")
        self.assertEqual(status, 405)
        self.assertNotIn(self.code, json.dumps(data))


class IntegrityAndDownloadTests(unittest.TestCase):
    def test_tampered_and_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            for relative in ("extension/manifest.json", "tools/yt-dlp.exe", "tools/ffmpeg.exe", "tools/ffprobe.exe"):
                p = root / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"fixture")
                files[relative] = hashlib.sha256(b"fixture").hexdigest()
            (root / "integrity.json").write_text(json.dumps({"files": files}))
            self.assertTrue(verify_integrity(root)[0])
            (root / "tools/ffmpeg.exe").write_bytes(b"changed")
            self.assertFalse(verify_integrity(root)[0])

    def test_download_uses_argument_list_and_no_cookie_or_exec_options(self):
        item = Episode(SID, "Title", 2, 45, "https://www.reelshort.com/episode", "https://cdn.example.org/a.mp4?token=a&x=$(bad)")
        cmd = build_command(Path("C:/app dir"), Path("C:/out dir"), item)
        self.assertEqual(cmd[-2:], ["--", item.media_url])
        self.assertIn("--ignore-config", cmd)
        self.assertNotIn("--cookies-from-browser", cmd)
        self.assertNotIn("--exec", cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "Episode_002.%(ext)s")

    def test_no_media_cannot_enter_download_queue(self):
        queue = DownloadQueue(Path("."), Path("report.json"), lambda _: None)
        with self.assertRaises(ValueError):
            queue.start([Episode(SID, "T", 1, 1, "", "")], Path("."))

    def test_log_redaction(self):
        result = redact("ERROR: https://cdn.example.org/x.mp4?token=secret&key=other failed")
        self.assertNotIn("secret", result)
        self.assertNotIn("other", result)

    def test_empty_file_never_counts_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.touch()
            self.assertFalse(validate_video(Path(tmp), path))

    def test_ffprobe_requires_a_video_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"x" * 1024)
            with patch("app.downloader.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = '{"streams":[{"codec_type":"audio"}]}'
                self.assertFalse(validate_video(Path(tmp), path))
                run.return_value.stdout = '{"streams":[{"codec_type":"video"}]}'
                self.assertTrue(validate_video(Path(tmp), path))


if __name__ == "__main__":
    unittest.main()
