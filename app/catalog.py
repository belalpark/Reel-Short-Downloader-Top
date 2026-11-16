from __future__ import annotations

import csv
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

MAX_CSV_BYTES = 5 * 1024 * 1024
SERIES_ID = re.compile(r"^[0-9a-f]{24}$", re.I)
MEDIA_EXTENSIONS = {".mp4", ".m3u8", ".mpd", ".m4v", ".mov", ".webm"}


@dataclass(frozen=True)
class Episode:
    series_id: str
    title: str
    episode: int
    total: int
    page_url: str
    media_url: str
    playback: str = "not_checked"
    source_status: str = ""
    source_type: str = ""
    capture_id: str = ""


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:65].rstrip(" .") or "Series"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "_" + value
    return value


def valid_media_url(value: str) -> str:
    if not value:
        return ""
    if len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ValueError("رابط فيديو غير صالح.")
    try:
        u = urlsplit(value)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or not host or u.username or u.password or u.port not in (None, 443):
            raise ValueError()
        if "." not in host or host.endswith((".localhost", ".local", ".internal")) or host == "localhost":
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        path = unquote(u.path)
        if Path(path).suffix.lower() not in MEDIA_EXTENSIONS or re.search(r"(?:^|/)(undefined|null|nan|none)(?:/|$)", path, re.I):
            raise ValueError()
    except (ValueError, UnicodeError) as e:
        raise ValueError("رابط فيديو غير صالح؛ يلزم رابط HTTPS مباشر بصيغة فيديو معروفة.") from e
    return value


def media_identity(value: str) -> str:
    u = urlsplit(value)
    return f"{u.scheme}://{u.netloc.lower()}{u.path}"


def _integer(value: str, label: str, low=1, high=150) -> int:
    if not re.fullmatch(r"[0-9]+", value or "") or not low <= int(value) <= high:
        raise ValueError(f"{label} غير صالح: {value[:30]}")
    return int(value)


def load_catalog(path: Path) -> list[Episode]:
    if not path.is_file() or path.suffix.lower() != ".csv" or path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError("اختر ملف CSV صالحًا لا يتجاوز 5 ميجابايت.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        fields = reader.fieldnames or []
        required = {"SeriesID", "SeriesTitle", "Episode", "TotalEpisodes", "PageURL", "MediaURL"}
        if not required.issubset(fields) or len(set(fields)) != len(fields):
            raise ValueError("يلزم فهرس الحلقات الأصلي؛ ملف مرشحات HLS لا يصلح للتنزيل.")
        result: list[Episode] = []
        seen, media_seen = set(), set()
        series = total = None
        try:
            for row in reader:
                if len(result) >= 150 or None in row or any(v is None for v in row.values()):
                    raise ValueError("صفوف CSV ناقصة أو أكثر من الحد المسموح.")
                sid = row["SeriesID"].lower().strip()
                count = _integer(row["TotalEpisodes"], "عدد الحلقات")
                ep = _integer(row["Episode"], "رقم الحلقة", high=count)
                if not SERIES_ID.fullmatch(sid) or (series is not None and (sid != series or count != total)):
                    raise ValueError("يلزم فهرس مسلسل واحد بعدد حلقات متسق.")
                if ep in seen:
                    raise ValueError(f"رقم الحلقة مكرر: {ep}")
                seen.add(ep)
                series, total = sid, count
                page = row["PageURL"].strip()
                if page:
                    p = urlsplit(page)
                    if p.scheme != "https" or p.netloc != "www.reelshort.com" or sid not in unquote(p.path).lower() or not re.search(rf"/episodes/episode-{ep}(?:[-/]|$)", p.path, re.I):
                        raise ValueError(f"صفحة الحلقة {ep} لا تطابق المسلسل.")
                    page = "https://www.reelshort.com" + p.path
                media = valid_media_url(row["MediaURL"].strip())
                if media:
                    identity = media_identity(media)
                    if identity in media_seen:
                        raise ValueError("رابط الفيديو نفسه منسوب لأكثر من حلقة؛ راجع الفهرس.")
                    media_seen.add(identity)
                result.append(Episode(sid, row["SeriesTitle"].strip()[:250] or "Series", ep, count, page, media, row.get("PlaybackStatus", "not_checked"), row.get("SourceStatus", ""), row.get("SourceType", ""), row.get("CaptureID", "")[:100]))
        except (csv.Error, UnicodeError) as e:
            raise ValueError("تعذر قراءة CSV؛ أعد تصديره بترميز UTF-8.") from e
    if not result:
        raise ValueError("الفهرس فارغ.")
    return sorted(result, key=lambda item: item.episode)
