"""Stable, non-secret download diagnostics and bounded retry decisions."""
import re

MAX_ATTEMPTS = 3

LABELS = {
    'connection_failed': 'تعذر الاتصال بمصدر الفيديو',
    'saved_file_missing': 'تعذر العثور على الملف في مجلد الحفظ',
    'playlist_too_large': 'قائمة الفيديو تتجاوز حجم الفحص المدعوم',
    'http_forbidden': 'الخادم رفض طلب التنزيل؛ جارٍ طلب مصدر جديد من صفحة الحلقة',
    'http_unauthorized': 'المصدر يحتاج جلسة دخول صالحة في الموقع',
    'http_not_found': 'رابط الفيديو لم يعد متاحًا؛ سيُجدَّد من صفحة الحلقة',
    'rate_limited': 'الخادم طلب مهلة؛ ستستمر بقية الملفات المتاحة',
    'network_error': 'تعذر الاتصال أو انقطع الإنترنت؛ أُجلت المحاولة',
    'download_stalled': 'توقف وصول البيانات؛ أُلغيت المحاولة العالقة وستعاد لاحقًا',
    'download_timeout': 'انتهت مهلة هذه المحاولة؛ ستعاد دون حجز بقية الطابور',
    'disk_full': 'مساحة القرص غير كافية؛ حرّر مساحة ثم واصل',
    'write_error': 'تعذرت الكتابة في مجلد الحفظ؛ تحقق من المسار وصلاحياته',
    'invalid_video': 'الملف المنقول ليس فيديو صالحًا؛ سيُطلب مصدر جديد',
    'protected_source': 'المصدر محمي أو غير مدعوم',
    'unsupported_source': 'مصدر الفيديو غير مدعوم',
    'source_not_captured': 'لم يصل مصدر فيديو مؤكد بعد؛ ستعاد المحاولة تلقائيًا',
    'source_unavailable': 'المصدر لم يستجب كمصدر فيديو؛ سيعاد فحصه',
    'source_expired': 'انتهت صلاحية المصدر؛ سيُجدد من صفحة الحلقة',
    'not_video': 'الاستجابة ليست ملف فيديو صالحًا',
    'not_direct_vod': 'لم تتأكد قائمة الفيديو الكاملة؛ سيعاد التقاط المصدر',
    'capture_timeout': 'لم تتقدم جلسة الالتقاط؛ أعيد إدراج الناقص وواصلت الجلسات الأخرى',
    'capture_paused': 'توقفت صفحة الالتقاط؛ ستعاد تهيئتها تلقائيًا',
    'invalid_episode_index': 'مصادر الفهرس غير متسقة؛ سيعاد فتح الحلقات بصفحات جديدة',
    'download_failed': 'فشل التنزيل؛ حُفظ تفصيل الخطأ وستعاد المحاولة لاحقًا',
    'account_access_required': 'الموقع لم يفتح الحلقة لهذا الحساب؛ لن تُعاد تلقائيًا. افتحها في الموقع ثم استخدم «أعد فحص المعلّق والمقفل»',
    'playback_not_started': 'فُتحت الحلقة لكن الفيديو لم يبدأ التشغيل؛ تأكد من تسجيل الدخول وأن تبويب Edge ظاهر',
    'video_element_missing': 'لم يظهر مشغّل الفيديو في صفحة الحلقة',
    'hls_not_observed': 'بدأ التشغيل لكن لم يُرصد بث الحلقة؛ ستعاد المحاولة',
    'hls_access_denied': 'الخادم رفض بث الحلقة لهذه الجلسة',
    'manual_open_required': 'الموقع لم يفتح الحلقة عند اختيارها؛ قد تحتاج اشتراكًا أو تسجيل دخول',
    'access_unavailable': 'الحلقة غير متاحة للاختيار في الموقع',
    'wrong_page': 'انتقل التبويب إلى صفحة أخرى أثناء الفحص',
    'series_not_playing': 'لم تبدأ أي حلقة من هذا المسلسل بالتشغيل في الموقع؛ أُوقفت المحاولات التلقائية',
    'capture_not_reached': 'لم تصل جلسة الالتقاط إلى هذه الحلقة؛ ستُجرب في جولة لاحقة دون احتسابها محاولة',
}


def classify(message, *, code=None):
    text = str(message).lower()
    match = re.search(r'(?:http(?: error)?\s*[: ]*|status(?: code)?\s*[: ]*)(401|403|404|410|429|503)\b', text)
    status = int(match[1]) if match else 0
    if 'no space left' in text or 'not enough space' in text or 'disk full' in text:
        reason = 'disk_full'
    elif 'permission denied' in text or 'access is denied' in text or 'unable to open for writing' in text:
        reason = 'write_error'
    elif status in (429, 503):
        reason = 'rate_limited'
    elif status == 401:
        reason = 'http_unauthorized'
    elif status == 403:
        reason = 'http_forbidden'
    elif status in (404, 410):
        reason = 'http_not_found'
    elif 'timed out' in text or 'connection' in text or 'resolve' in text or 'network' in text:
        reason = 'network_error'
    elif 'drm' in text or 'sample-aes' in text:
        reason = 'protected_source'
    else:
        reason = 'download_failed'
    return reason, status


def retry_delay(reason, attempts):
    if reason in ('disk_full', 'write_error', 'protected_source'):
        return 300
    return min(300, (60 if reason == 'rate_limited' else 15) * 2 ** min(5, max(0, attempts - 1)))


def explanation(reason):
    return LABELS.get(reason, LABELS['download_failed'])
