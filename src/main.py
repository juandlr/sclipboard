import sys
import os
import gi
import json
import subprocess

# Lock the API versions — must happen before any gi.repository imports
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib

# Let Python find our src/ package whether we run as 'python3 src/main.py' or 'python3 -m src.main'
if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.window import ClipboardWindow
    from src.clipboard_item import ClipboardItem
    from src.tray import TrayIcon
else:
    from .window import ClipboardWindow
    from .clipboard_item import ClipboardItem
    from .tray import TrayIcon


# Persistence paths — app-level so they survive window destruction
_xdg_data = os.environ.get('XDG_DATA_HOME',
                            os.path.join(os.path.expanduser('~'), '.local', 'share'))
_data_dir = os.path.join(_xdg_data, 'sclipboard')
os.makedirs(_data_dir, exist_ok=True)
HISTORY_FILE = os.path.join(_data_dir, 'history.json')
QUEUE_FILE = '/tmp/sclipboard-queue.json'


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

        self._window = None
        self._tray = None   # created on first activation
        self._watcher = None
        self._last_item_seq = 0   # track watcher items we've already processed

        # ── File-based IPC: watcher writes to queue file, we monitor it ──
        self._setup_watcher_ipc()

        # ── Watcher health check (restarts watcher if it dies) ──
        GLib.timeout_add_seconds(10, self._check_watcher)

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
        self._window = ClipboardWindow(app=self, store=self._store)
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

    # ── Watcher health check ─────────────────────────────────────

    def _start_watcher(self):
        """Launch the headless clipboard watcher with X11 backend.
        The watcher reads clipboard changes (unfocused-safe via X11)
        and writes items to the queue file.
        If it dies, it gets restarted automatically."""
        # Check if existing watcher is still alive
        if self._watcher is not None and self._watcher.poll() is None:
            return  # still running
        if self._watcher is not None:
            rc = self._watcher.returncode
            print(f'[main] watcher died (rc={rc}), restarting', flush=True)

        env = os.environ.copy()
        env['GDK_BACKEND'] = 'x11'
        base = os.path.dirname(os.path.abspath(__file__))
        watcher_path = os.path.join(base, 'watcher.py')
        try:
            self._watcher = subprocess.Popen(
                [sys.executable, watcher_path],
                env=env,
                cwd=base,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f'[main] watcher started (pid=%d)' % self._watcher.pid, flush=True)
        except Exception as e:
            print(f'[main] failed to start watcher: %s' % e, flush=True)

    def _check_watcher(self):
        """Periodic: restart watcher if it died."""
        if self._watcher is not None and self._watcher.poll() is not None:
            print('[main] watcher health check: dead, restarting', flush=True)
            self._start_watcher()
        return GLib.SOURCE_CONTINUE  # keep the timer alive

    # ── File-based watcher IPC ────────────────────────────────────

    def _setup_watcher_ipc(self):
        """Watch the queue file for new clipboard items from the watcher."""
        queue = Gio.File.new_for_path(QUEUE_FILE)
        self._file_monitor = queue.monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._file_monitor.connect('changed', self._on_queue_changed)

    def _on_queue_changed(self, monitor, file, other_file, event_type):
        """File monitor callback — watcher wrote a new item to the queue."""
        if event_type not in (Gio.FileMonitorEvent.CHANGED,
                              Gio.FileMonitorEvent.CREATED):
            return
        try:
            with open(QUEUE_FILE) as f:
                item_dict = json.load(f)
        except Exception:
            return

        # Clipboard was cleared externally (e.g. 1Password timer)
        if item_dict.get('cleared'):
            self._remove_latest()
            return

        seq = item_dict.get('seq', -1)
        if seq <= self._last_item_seq:
            return  # already processed this one
        self._last_item_seq = seq
        item = ClipboardItem(
            content=item_dict.get('content', ''),
            content_type=item_dict.get('content_type', 'text'),
            timestamp=item_dict.get('timestamp', 0),
            thumbnail=item_dict.get('thumbnail', ''),
        )
        GLib.idle_add(self._process_item, item)
        return False  # don't repeat

    def _remove_latest(self):
        """Remove the most recent item (e.g. clipboard was cleared by 1Password)."""
        if self._store.get_n_items() > 0:
            self._store.remove(0)
            self.save_history()
            print('[main] removed latest item (clipboard cleared)', flush=True)

    # ── Cleanup ─────────────────────────────────────────────────

    def do_shutdown(self):
        """Kill watcher process on exit."""
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
        os.chmod(HISTORY_FILE, 0o600)

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
