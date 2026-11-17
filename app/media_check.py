"""Bounded public-CDN checks. A successful check is not a permanent URL guarantee."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urljoin
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .catalog import valid_media_url
from .http_context import safe_user_agent


def source_expiry(url):
    values = {k.lower(): v[-1] for k, v in parse_qs(urlsplit(url).query).items()}
    try:
        if 'x-amz-date' in values and 'x-amz-expires' in values:
            start = datetime.strptime(values['x-amz-date'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
            return start + max(0, float(values['x-amz-expires']))
        for name in ('expires', 'exp', 'expire'):
            if name in values:
                number = float(values[name])
                if number > 1e12:
                    number /= 1000
                if number > 1e9:
                    return number
    except (ValueError, OverflowError):
        pass
    return 0.0


def trusted_location(url, allow_loopback=False):
    try:
        u = urlsplit(url)
        if allow_loopback and u.scheme == 'http' and u.hostname == '127.0.0.1' and not u.username and not u.password:
            return True
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            return False
        host = (u.hostname or '').lower()
        return host.endswith('.crazymaplestudios.com') or host == 'crazymaplestudios.com' or host.endswith('.reelshort.com') or host == 'reelshort.com'
    except (ValueError, UnicodeError):
        return False


def trusted_source(url, allow_loopback=False):
    if not trusted_location(url, allow_loopback):
        return False
    if allow_loopback and urlsplit(url).hostname == '127.0.0.1':
        return True
    try:
        return bool(valid_media_url(url)) and not urlsplit(url).path.lower().endswith('.mpd')
    except ValueError:
        return False


@dataclass(frozen=True)
class SourceCheck:
    ok: bool
    reason: str
    checked_at: float
    expires_at: float = 0


class MediaVerifier:
    def __init__(self, allow_loopback=False, timeout=6):
        self.allow_loopback, self.timeout = allow_loopback, timeout

    def check(self, url, page_url='', user_agent=''):
        now, expiry = time.time(), source_expiry(url)
        def result(ok, reason):
            return SourceCheck(ok, reason, now, expiry)
        if not trusted_source(url, self.allow_loopback):
            return result(False, 'unsupported_source')
        if expiry and expiry <= now + 30:
            return result(False, 'source_expired')
        verifier = self
        class Redirects(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not trusted_source(newurl, verifier.allow_loopback):
                    raise ValueError('unsafe_redirect')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        try:
            referer = page_url if page_url.startswith('https://www.reelshort.com/') else 'https://www.reelshort.com/'
            request = Request(url, headers={'Range': 'bytes=0-65535', 'Referer': referer,
                'Origin': 'https://www.reelshort.com', 'User-Agent': safe_user_agent(user_agent)})
            with build_opener(Redirects()).open(request, timeout=self.timeout) as response:
                if response.status not in (200, 206) or not trusted_source(response.url, self.allow_loopback):
                    return result(False, 'source_unavailable')
                body = response.read(65536)
            if urlsplit(url).path.lower().endswith('.m3u8'):
                text = body.decode('utf-8-sig', errors='replace').strip()
                if not text.startswith('#EXTM3U') or '#EXT-X-ENDLIST' not in text or '#EXTINF:' not in text:
                    return result(False, 'not_direct_vod')
                formats = re.findall(r'KEYFORMAT="([^"]+)"', text)
                if 'SAMPLE-AES' in text or any(value != 'identity' for value in formats):
                    return result(False, 'protected_source')
                references = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
                references += re.findall(r'URI="([^"]+)"', text)
                if any(not trusted_location(urljoin(url, reference), self.allow_loopback) for reference in references):
                    return result(False, 'unsupported_source')
                return result(True, 'verified_hls')
            if body.startswith(bytes.fromhex('1a45dfa3')) or b'ftyp' in body[:32] or b'moov' in body[:32]:
                return result(True, 'verified_video')
            return result(False, 'not_video')
        except HTTPError as error:
            return result(False, {401:'http_unauthorized',403:'http_forbidden',404:'http_not_found',410:'http_not_found',429:'rate_limited',503:'rate_limited'}.get(error.code, 'source_unavailable'))
        except (URLError, OSError, ValueError):
            return result(False, 'connection_failed')
