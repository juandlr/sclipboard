"""
clipboard_item.py — A single clipboard history entry
=====================================================
GObject with typed properties: content, content_type, timestamp, thumbnail.
"""

import time
import gi
gi.require_version('GObject', '2.0')
from gi.repository import GObject


class ClipboardItem(GObject.Object):
    __gtype_name__ = 'ClipboardItem'

    content: str = GObject.Property(type=str, default='')
    content_type: str = GObject.Property(type=str, default='text')
    timestamp: int = GObject.Property(type=int, default=0)
    thumbnail: str = GObject.Property(type=str, default='')

    def __init__(self, content='', content_type='text',
                 timestamp=0, thumbnail=''):
        super().__init__()
        self.content = content
        self.content_type = content_type
        self.timestamp = timestamp or int(time.time())
        self.thumbnail = thumbnail
