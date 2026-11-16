"""Episode display derived from saved files, excluding them from remaining work."""

REASONS = {
    "hls_not_observed": "لم يُلتقط بث هذه الحلقة بعد",
    "hls_ambiguous": "بانتظار تأكيد مصدر الحلقة",
    "hls_duration_mismatch": "مدة البث لا تطابق الحلقة",
    "hls_previous_episode": "البث المرصود يخص حلقة سابقة",
    "hls_stale_playback": "المشغّل لم ينتقل إلى فيديو الحلقة بعد",
    "hls_segments_not_observed": "بانتظار تحميل مقاطع فيديو الحلقة",
    "hls_access_denied": "تعذرت قراءة مصدر البث؛ ستُعاد المحاولة",
    "hls_rate_limited": "الموقع يطلب مهلة؛ ستُعاد المحاولة تلقائيًا",
    "hls_drm_protected": "بث محمي غير مدعوم",
    "playback_not_started": "المشغّل لم يبدأ تشغيل الحلقة",
    "video_element_missing": "مشغّل الفيديو لم يظهر",
    "access_unavailable": "الموقع لم يتيح فتح الحلقة في هذه الجلسة",
    "manual_open_required": "تعذر فتح الحلقة؛ ستُعاد المحاولة تلقائيًا",
    "missing": "زر الحلقة لم يظهر؛ ستُعاد المحاولة",
}


def catalog_view(entries, is_complete, statuses):
    rows, done, ready = [], 0, 0
    for item in entries:
        completed = is_complete(item)
        done += bool(completed)
        ready += bool(item.media_url) and not completed
        status = statuses.get(item.episode)
        if completed:
            status, source = "مكتمل ومفحوص", "محفوظ على الجهاز"
        else:
            if status and status.startswith("فشل"):
                status = "تعذر التنزيل؛ ستُعاد المحاولة تلقائيًا"
            status = status or ("بانتظار التنزيل" if item.media_url else REASONS.get(item.source_status or item.playback, "بانتظار التقاط فيديو هذه الحلقة"))
            source = "متوفر للتنزيل" if item.media_url else "لم يُلتقط بعد"
        rows.append((status, source, item.episode))
    return rows, done, ready, len(entries) - done
