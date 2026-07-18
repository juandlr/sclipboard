"""
watcher.py — Headless clipboard watcher for XWayland
=====================================================
Runs under GDK_BACKEND=x11 so it always receives clipboard events,
even when the main GUI window is not focused.

Flow:
  User copies → ::changed fires → read clipboard async
  → build JSON dict → write atomically to queue file → GUI picks it up
"""

import os
import sys
import time
import json
import traceback
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GLib

QUEUE_FILE = '/tmp/sclipboard-queue.json'

# Log file for debugging watcher issues
LOG_FILE = '/tmp/sclipboard-watcher.log'

_START_TIME = time.time()


def _log(msg: str):
    """Append a timestamped message to the watcher log."""
    elapsed = time.time() - _START_TIME
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(f'[+{elapsed:.1f}s] {msg}\n')
    except Exception:
        pass


# ── Queue file helper ──────────────────────────────────────────

def _send_to_gui(item_dict: dict):
    """Write clipboard item atomically to the queue file for the GUI."""
    try:
        tmp = QUEUE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(item_dict, f)
        os.replace(tmp, QUEUE_FILE)  # atomic rename — GUI never sees half-written data
    except Exception as e:
        _log(f'queue write failed: {e}')


# ── Headless application ───────────────────────────────────────

class WatcherApp(Gtk.Application):
    """Headless GTK4 app — no windows, just clipboard monitoring."""

    def __init__(self):
        super().__init__(application_id='io.github.juandlr.sclipboard.Watcher')
        self._clipboard = None
        self._last_text = ''
        self._last_texture_hash = ''       # dedup images on copy-back
        self._seq = 0                      # sequence number to cancel stale reads
        self._item_seq = 0                 # monotonic counter, one per item sent to GUI

    def do_startup(self):
        """Called once when the app starts. Set up clipboard monitoring."""
        Gtk.Application.do_startup(self)

        display = Gdk.Display.get_default()
        self._clipboard = display.get_clipboard()
        self._clipboard.connect('changed', self._on_changed)

        # Keep the app alive — a headless app with no windows would exit immediately.
        self.hold()
        _log('started, GDK_BACKEND=%s' % os.environ.get('GDK_BACKEND', 'unknown'))
        print('[watcher] started, listening for clipboard changes', flush=True)

    def do_activate(self):
        """Required by Gtk.Application — we don't create windows."""
        pass

    # ── Clipboard handling ─────────────────────────────────────

    def _on_changed(self, clipboard):
        """Clipboard changed — read text and images asynchronously."""
        self._seq += 1
        seq = self._seq
        _log(f'changed (seq={seq})')
        try:
            clipboard.read_text_async(None, self._on_text_ready, seq)
        except Exception as e:
            _log(f'read_text_async error: {e}\n{traceback.format_exc()}')
        try:
            clipboard.read_texture_async(None, self._on_texture_ready, seq)
        except Exception as e:
            _log(f'read_texture_async error: {e}\n{traceback.format_exc()}')

    def _on_text_ready(self, clipboard, result, seq):
        """Async text callback — skip if a newer ::changed already fired."""
        if seq != self._seq:
            return
        try:
            text = clipboard.read_text_finish(result)
        except Exception as e:
            _log(f'read_text_finish error: {e}')
            return
        if text and text.strip() and text != self._last_text:
            self._last_text = text
            self._item_seq += 1
            item = {
                'content': text,
                'content_type': 'text',
                'timestamp': int(time.time()),
                'thumbnail': '',
                'seq': self._item_seq,
            }
            _log(f'text ready ({len(text)} chars)')
            _send_to_gui(item)

    def _on_texture_ready(self, clipboard, result, seq):
        """Async texture callback — save to temp file, notify GUI."""
        if seq != self._seq:
            return
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception as e:
            _log(f'read_texture_finish error: {e}')
            return
        if texture is None:
            return

        try:
            # Save texture to a stable temp path
            import tempfile
            import hashlib

            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            texture.save_to_png(tmp.name)
            tmp.close()

            with open(tmp.name, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()[:12]

            if file_hash == self._last_texture_hash:
                os.unlink(tmp.name)  # duplicate from copy-back, skip
                return
            self._last_texture_hash = file_hash

            filepath = os.path.join(tempfile.gettempdir(),
                                    f'clipimage_{file_hash}.png')
            os.replace(tmp.name, filepath)

            self._item_seq += 1
            item = {
                'content': '',
                'content_type': 'image',
                'timestamp': int(time.time()),
                'thumbnail': filepath,
                'seq': self._item_seq,
            }
            _log(f'image ready ({filepath})')
            _send_to_gui(item)
        except Exception as e:
            _log(f'texture save error: {e}\n{traceback.format_exc()}')

    def do_shutdown(self):
        """Clean up on exit."""
        _log('shutting down')
        self.release()
        print('[watcher] shutting down', flush=True)
        Gtk.Application.do_shutdown(self)


# ── Entry point ────────────────────────────────────────────────

def main():
    """Launched by the main GUI process with GDK_BACKEND=x11."""
    app = WatcherApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
