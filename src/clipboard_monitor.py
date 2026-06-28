"""
clipboard_monitor.py — Watches the system clipboard for changes
================================================================
Hooks Gdk.Clipboard::changed signal and reads content async.
Emits 'content-changed' with a new ClipboardItem.

Sequence-number pattern
-----------------------
GTK4's async clipboard reads return NULL when the clipboard changes
again before the read completes.  A GCancellable alone isn't enough
because the internal clipboard machinery may not check it on time.

Instead, every ::changed increments a sequence counter.  Each async
callback captures the current sequence and silently returns if a newer
::changed happened in the meantime — guaranteeing we only process the
latest clipboard content."""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import GObject, Gdk

from .clipboard_item import ClipboardItem


class ClipboardMonitor(GObject.Object):
    __gtype_name__ = 'ClipboardMonitor'

    __gsignals__ = {
        'content-changed': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__()
        display = Gdk.Display.get_default()
        self._clipboard = display.get_clipboard()
        self._clipboard.connect('changed', self._on_clipboard_changed)
        self._last_text = ''
        self._last_texture_hash = ''
        self._skip_next = False
        self._seq = 0                    # monotonic ::changed counter

    def prime_from_store(self, store):
        """Mark that the next clipboard change should be ignored (already in history)."""
        if store.get_n_items() > 0:
            item = store.get_item(0)
            if item.content_type == 'text':
                self._last_text = item.content or ''
        self._skip_next = True

    def _on_clipboard_changed(self, clipboard):
        """Bump the sequence so in-flight async callbacks become stale."""
        if self._skip_next:
            self._skip_next = False
            return  # skip initial detection on startup

        self._seq += 1
        seq = self._seq
        clipboard.read_text_async(None, self._on_text_ready, seq)
        clipboard.read_texture_async(None, self._on_texture_ready, seq)

    def _on_text_ready(self, clipboard, result, seq):
        """Async text callback — ignore if a newer ::changed arrived."""
        if seq != self._seq:
            return
        text = clipboard.read_text_finish(result)
        if text and text.strip() and text != self._last_text:
            self._last_text = text
            item = ClipboardItem(content=text, content_type='text')
            self.emit('content-changed', item)

    def _on_texture_ready(self, clipboard, result, seq):
        """Async texture callback — ignore if a newer ::changed arrived."""
        if seq != self._seq:
            return
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception:
            return  # no image in clipboard, ignore
        if texture is None:
            return

        import tempfile, os, hashlib

        # Save to temp file first, then hash the actual bytes
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        texture.save_to_png(tmp.name)
        tmp.close()

        with open(tmp.name, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()[:12]

        if file_hash == self._last_texture_hash:
            os.unlink(tmp.name)  # duplicate, clean up
            return
        self._last_texture_hash = file_hash

        # Rename to stable path based on content hash
        filepath = os.path.join(tempfile.gettempdir(),
                               f'clipimage_{file_hash}.png')
        os.replace(tmp.name, filepath)

        item = ClipboardItem(content='', content_type='image',
                             thumbnail=filepath)
        self.emit('content-changed', item)
