"""
watcher.py — Headless clipboard watcher for XWayland
=====================================================
Runs under GDK_BACKEND=x11 so it always receives clipboard events,
even when the main GUI window is not focused.

Flow:
  User copies → ::changed fires → read clipboard async
  → build JSON dict → send through Unix socket → GUI picks it up
"""

import os
import sys
import time
import json
import socket
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk, GLib

SOCKET_PATH = '/tmp/sclipboard.sock'


# ── Socket helper ──────────────────────────────────────────────

def _send_to_gui(item_dict: dict):
    """Connect to the GUI's listening socket and send one clipboard item."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        sock.send(json.dumps(item_dict).encode())
        sock.close()
    except Exception:
        pass  # GUI may not be running yet — just drop


# ── Headless application ───────────────────────────────────────

class WatcherApp(Gtk.Application):
    """Headless GTK4 app — no windows, just clipboard monitoring."""

    def __init__(self):
        super().__init__(application_id='io.github.juandlr.sclipboard.Watcher')
        self._clipboard = None
        self._last_text = ''
        self._seq = 0                     # sequence number to cancel stale reads

    def do_startup(self):
        """Called once when the app starts. Set up clipboard monitoring."""
        Gtk.Application.do_startup(self)

        display = Gdk.Display.get_default()
        self._clipboard = display.get_clipboard()
        self._clipboard.connect('changed', self._on_changed)

        # Keep the app alive — a headless app with no windows would exit immediately.
        self.hold()
        print('[watcher] started, listening for clipboard changes', flush=True)

    def do_activate(self):
        """Required by Gtk.Application — we don't create windows."""
        pass

    # ── Clipboard handling ─────────────────────────────────────

    def _on_changed(self, clipboard):
        """Clipboard changed — read text and images asynchronously."""
        self._seq += 1
        seq = self._seq
        clipboard.read_text_async(None, self._on_text_ready, seq)
        clipboard.read_texture_async(None, self._on_texture_ready, seq)

    def _on_text_ready(self, clipboard, result, seq):
        """Async text callback — skip if a newer ::changed already fired."""
        if seq != self._seq:
            return
        text = clipboard.read_text_finish(result)
        if text and text.strip() and text != self._last_text:
            self._last_text = text
            item = {
                'content': text,
                'content_type': 'text',
                'timestamp': int(time.time()),
                'thumbnail': '',
            }
            _send_to_gui(item)

    def _on_texture_ready(self, clipboard, result, seq):
        """Async texture callback — save to temp file, notify GUI."""
        if seq != self._seq:
            return
        try:
            texture = clipboard.read_texture_finish(result)
        except Exception:
            return
        if texture is None:
            return

        # Save texture to a stable temp path
        import tempfile
        import hashlib

        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        texture.save_to_png(tmp.name)
        tmp.close()

        with open(tmp.name, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()[:12]

        filepath = os.path.join(tempfile.gettempdir(),
                                f'clipimage_{file_hash}.png')
        os.replace(tmp.name, filepath)

        item = {
            'content': '',
            'content_type': 'image',
            'timestamp': int(time.time()),
            'thumbnail': filepath,
        }
        _send_to_gui(item)

    def do_shutdown(self):
        """Clean up on exit."""
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
