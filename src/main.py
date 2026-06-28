import sys
import os
import gi
import json
import socket
import threading
import subprocess

# Lock the API versions — must happen before any gi.repository imports
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk

# Let Python find our src/ package whether we run as 'python3 src/main.py' or 'python3 -m src.main'
if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.window import ClipboardWindow
    from src.clipboard_monitor import ClipboardMonitor
    from src.clipboard_item import ClipboardItem
    from src.tray import TrayIcon
else:
    from .window import ClipboardWindow
    from .clipboard_monitor import ClipboardMonitor
    from .clipboard_item import ClipboardItem
    from .tray import TrayIcon


# Persistence paths — app-level so they survive window destruction
_xdg_data = os.environ.get('XDG_DATA_HOME',
                            os.path.join(os.path.expanduser('~'), '.local', 'share'))
_data_dir = os.path.join(_xdg_data, 'sclipboard')
os.makedirs(_data_dir, exist_ok=True)
HISTORY_FILE = os.path.join(_data_dir, 'history.json')
SOCKET_PATH = '/tmp/sclipboard.sock'


class ClipboardApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id='io.github.juandlr.sclipboard',
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        # Data lives at app level — survives window destroy/create cycles
        self._store = Gio.ListStore.new(ClipboardItem)
        self._load_history()

        # GSettings — GNOME-native settings storage (auto-persists)
        self.settings = Gio.Settings.new('io.github.juandlr.sclipboard')

        self._monitor = ClipboardMonitor()
        self._monitor.prime_from_store(self._store)
        # Connect monitor directly — handles clipboard when watcher is unavailable
        self._monitor.connect('content-changed', self._on_monitor_item)
        # GUI no longer listens to monitor directly —
        # the X11 watcher process handles clipboard monitoring
        self._window = None
        self._tray = None  # created on first activation
        self._watcher = None

        # ── Socket listener for watcher IPC ──
        self._running = True
        self._listener_thread = threading.Thread(
            target=self._socket_listen, daemon=True)
        self._listener_thread.start()

    def do_activate(self):
        # Create tray on first activation (only primary instance reaches here)
        if self._tray is None:
            self._tray = TrayIcon(on_activate=self._on_tray_activate)
            self._tray.set_open_callback(self._on_tray_activate)
            self._tray.set_quit_callback(self.quit)
        # Start the X11 watcher process
        self._start_watcher()
        if self._window is not None:
            if self._window.is_active():
                self._window.set_visible(False)
            else:
                self._window.set_visible(True)
                self._window.present()
            return
        self._create_window()

    def _create_window(self):
        self._window = ClipboardWindow(app=self, store=self._store, monitor=self._monitor)
        self._window.set_hide_on_close(True)
        self._window.present()

        # Quit action — exits the app completely
        if not self.has_action('quit'):
            quit_action = Gio.SimpleAction.new('quit', None)
            quit_action.connect('activate', lambda a, p: self.quit())
            self.add_action(quit_action)
            self.set_accels_for_action('app.quit', ['<Primary>q'])

    def _on_tray_activate(self):
        """Tray icon clicked → toggle window."""
        self.activate()  # GTK routes this to do_activate()

    def _on_tray_context_menu(self, x, y):
        """Right-click tray icon → show quit popup."""
        menu = Gtk.Window(type=Gtk.WindowType.POPUP)
        menu.set_resizable(False)
        menu.set_decorated(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class('toolbar')
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        quit_btn = Gtk.Button(label='Quit')
        quit_btn.connect('clicked', lambda b: self.quit())
        box.append(quit_btn)

        menu.set_child(box)
        menu.present()
        menu.set_position(Gtk.WindowPosition.MOUSE)

    # ── X11 Watcher process ─────────────────────────────────────

    def _start_watcher(self):
        """Launch the headless clipboard watcher with X11 backend.
        The watcher reads clipboard changes (unfocused-safe via X11)
        and sends them through the Unix socket."""
        if self._watcher is not None:
            return  # already running
        env = os.environ.copy()
        env['GDK_BACKEND'] = 'x11'
        # Resolve watcher path relative to main.py (works in Flatpak and local)
        base = os.path.dirname(os.path.abspath(__file__))
        watcher_path = os.path.join(base, 'watcher.py')
        try:
            self._watcher = subprocess.Popen(
                [sys.executable, watcher_path],
                env=env,
                cwd=base,
                stdout=None,
                stderr=None,
            )
            print('[main] watcher started (pid=%d)' % self._watcher.pid, flush=True)
        except Exception as e:
            print('[main] failed to start watcher: %s' % e, flush=True)

    # ── Socket listener (runs in background thread) ─────────────

    def _socket_listen(self):
        """Thread: listen on Unix socket for watcher notifications."""
        # Clean up stale socket file from previous run
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        sock.listen(5)
        sock.settimeout(1.0)  # wake up every second to check _running

        print('[main] socket listening on %s' % SOCKET_PATH, flush=True)

        while self._running:
            try:
                conn, _ = sock.accept()
                data = conn.recv(4096)
                conn.close()
                if data:
                    item_dict = json.loads(data)
                    # Must use idle_add — GTK is not thread-safe
                    GLib.idle_add(self._on_watcher_item, item_dict)
            except socket.timeout:
                continue
            except Exception:
                continue

        sock.close()
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

    def _on_monitor_item(self, monitor, item):
        """Called when ClipboardMonitor detects a clipboard change."""
        self._process_item(item)

    def _on_watcher_item(self, item_dict: dict):
        """Called from main thread (via idle_add) when watcher sends an item."""
        item = ClipboardItem(
            content=item_dict.get('content', ''),
            content_type=item_dict.get('content_type', 'text'),
            timestamp=item_dict.get('timestamp', 0),
            thumbnail=item_dict.get('thumbnail', ''),
        )
        self._process_item(item)
        return False  # don't repeat

    # ── Cleanup ─────────────────────────────────────────────────

    def do_shutdown(self):
        """Kill watcher process and clean up socket on exit."""
        self._running = False
        if self._watcher is not None:
            try:
                self._watcher.terminate()
                self._watcher.wait(timeout=2)
            except Exception:
                self._watcher.kill()
            print('[main] watcher stopped', flush=True)
        Adw.Application.do_shutdown(self)

    # ── Clipboard monitoring (app-level, survives window destruction) ──

    def _process_item(self, item):
        for i in range(self._store.get_n_items()):
            existing = self._store.get_item(i)
            if item.content_type == 'image':
                if existing.content_type == 'image' and existing.thumbnail == item.thumbnail:
                    self._store.remove(i)
                    break
            elif existing.content == item.content and existing.content_type == item.content_type:
                self._store.remove(i)
                break
        self._store.insert(0, item)
        self.save_history()
        self._trim_to_max()
        return False  # don't repeat

    def _trim_to_max(self):
        max_items = self.settings.get_int('max-items')
        while self._store.get_n_items() > max_items:
            self._store.remove(self._store.get_n_items() - 1)

    def trim_to_max(self):
        """Public — called by window when max_items changes."""
        self._trim_to_max()

    # ── Persistence ──

    def save_history(self):
        """Public — called by window after modifications."""
        data = []
        for i in range(self._store.get_n_items()):
            item = self._store.get_item(i)
            data.append({
                'content': item.content,
                'content_type': item.content_type,
                'timestamp': item.timestamp,
                'thumbnail': item.thumbnail,
            })
        with open(HISTORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_history(self):
        if not os.path.exists(HISTORY_FILE):
            return
        try:
            with open(HISTORY_FILE) as f:
                data = json.load(f)
            for entry in data:
                item = ClipboardItem(**entry)
                self._store.append(item)
        except (json.JSONDecodeError, KeyError):
            pass

    @property
    def store(self):
        return self._store


def main():
    return ClipboardApplication().run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())
