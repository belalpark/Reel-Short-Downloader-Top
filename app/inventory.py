"""An explicit catalog pass. Only the inventory action can issue a catalog command."""
import threading
import time


class CatalogInventory:
    PAGE = 'https://www.reelshort.com/ar'

    def __init__(self, bridge, opener):
        self.bridge, self.opener = bridge, opener
        self.job_id = ''
        self.started = 0
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if self.job_id:
                return
            self.job_id = self.bridge.command('catalog', page_url=self.PAGE)
            self.started = time.time()
            try:
                self.opener(self.PAGE)
            except OSError:
                self.cancel()
                raise

    def cancel(self):
        with self.lock:
            if self.job_id:
                self.bridge.command('stop')
                self.job_id = ''

    def poll(self):
        with self.lock:
            if not self.job_id:
                return ''
            progress = self.bridge.catalog_status()
            current = progress.get('id') == self.job_id
            if current and progress.get('done'):
                self.job_id = ''
                return 'حُفظ الجرد الجزئي. يمكن تنزيل الروابط الموجودة.' if progress.get('error') else 'حُفظ جرد المسلسلات. يجري جمع روابط الحلقات الناقصة.'
            last_update = progress.get('updated', 0) if current else 0
            if time.time() - max(self.started, last_update) > 180:
                self.cancel()
                return 'انتهت مهلة الجرد. حُفظت المسلسلات التي وصلت.'
            return ''
