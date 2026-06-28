"""
window.py — The main clipboard manager window
==============================================
Uses a Gtk.Template to auto-wire widgets from window.ui.
Dynamic data-binding and logic stay in Python."""

import os
import time
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio, GLib

_UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'window.ui')


@Gtk.Template(filename=_UI_FILE)
class ClipboardWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'ClipboardWindow'

    # Auto-wired from the UI file via Gtk.Template.Child()
    listbox = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    max_items_spin = Gtk.Template.Child()
    clear_btn = Gtk.Template.Child()
    hide_copy_check = Gtk.Template.Child()

    def __init__(self, app: Adw.Application, store: Gio.ListStore, monitor):
        super().__init__(application=app)

        self._app = app

        # ── Auto-focus search on map ──
        self.connect('map', self._on_map)

        # ── GSettings ──
        settings = app.settings
        self._max_items = settings.get_int('max-items')
        self._win_width = settings.get_int('win-width')
        self._win_height = settings.get_int('win-height')
        self.connect('notify::default-width', self._on_window_size_changed)
        self.connect('notify::default-height', self._on_window_size_changed)
        self.set_default_size(self._win_width, self._win_height)

        self.max_items_spin.set_value(self._max_items)
        self.max_items_spin.connect('value-changed', self._on_max_items_changed)

        # ── Hide-after-copy toggle ──
        settings.bind('hide-after-copy', self.hide_copy_check, 'active',
                      Gio.SettingsBindFlags.DEFAULT)

        # ── Signals ──
        self.clear_btn.connect('clicked', self._on_clear_all)
        self.search_entry.connect('search-changed', self._on_search_changed)
        self.listbox.connect('row-activated', self._on_row_activated)

        # ── Data model ──
        self._store = store

        # Search filter on the item's 'content' property
        self._filter = Gtk.CustomFilter.new(self._do_filter, None)
        self._filter_model = Gtk.FilterListModel.new(store, self._filter)

        # bind_model — GTK4's standard way to connect a ListStore to a
        # ListBox.  Auto-creates/removes rows when the model changes and
        # reliably repaints even when the window is unfocused.
        self.listbox.bind_model(self._filter_model, self._create_row_widget)

        # ── CSS ──
        css = Gtk.CssProvider()
        css.load_from_data(b".thumbnail { border-radius: 3px; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # ── Clipboard for copying ──
        self._clipboard = Gdk.Display.get_default().get_clipboard()


    # ── Row creation (bind_model factory) ──

    def _create_row_widget(self, item):
        """Factory called by bind_model for each item in the model.
        Must return a Gtk.Widget — the ListBox row."""
        row = Adw.ActionRow()
        row.set_activatable(True)
        row._item = item

        raw = item.content or ''
        title = GLib.markup_escape_text(raw)
        if len(raw) > 60:
            title = GLib.markup_escape_text(raw[:60]) + '…'
        row.set_title(title)

        now = int(time.time())
        delta = now - item.timestamp
        if delta < 60:
            ago = 'Just now'
        elif delta < 3600:
            ago = f'{delta // 60} min ago'
        elif delta < 86400:
            ago = f'{delta // 3600} hours ago'
        else:
            ago = f'{delta // 86400} days ago'
        row.set_subtitle(ago)

        if item.content_type == 'image' and item.thumbnail:
            thumb = Gtk.Image.new_from_file(item.thumbnail)
            thumb.set_pixel_size(36)
            thumb.set_margin_top(6)
            thumb.set_margin_bottom(6)
            row.add_prefix(thumb)

        suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        suffix_box.set_valign(Gtk.Align.CENTER)
        suffix_box.set_halign(Gtk.Align.END)

        copy_icon = Gtk.Image.new_from_icon_name('edit-copy-symbolic')
        copy_icon.add_css_class('dim-label')
        copy_wrapper = Gtk.Button()
        copy_wrapper.set_child(copy_icon)
        copy_wrapper.add_css_class('flat')
        copy_wrapper.add_css_class('circular')
        copy_wrapper.set_can_focus(False)
        copy_wrapper.connect('clicked', self._on_copy_clicked, row)
        suffix_box.append(copy_wrapper)

        delete_icon = Gtk.Image.new_from_icon_name('user-trash-symbolic')
        delete_icon.add_css_class('dim-label')
        delete_btn = Gtk.Button()
        delete_btn.set_child(delete_icon)
        delete_btn.add_css_class('flat')
        delete_btn.add_css_class('circular')
        delete_btn.set_can_focus(False)
        delete_btn.connect('clicked', self._on_delete_clicked, row)
        suffix_box.append(delete_btn)

        row.add_suffix(suffix_box)
        return row

    def _on_map(self, window):
        self.search_entry.grab_focus()

    # ── Search ──
    def _on_search_changed(self, search_entry):
        """Notify the filter model that the filter criteria changed."""
        self._filter.changed(Gtk.FilterChange.DIFFERENT)

    def _do_filter(self, item, user_data):
        """Filter predicate — True = show the row."""
        query = self.search_entry.get_text().lower()
        if not query:
            return True
        return query in (item.content or '').lower()

    # ── Row actions ──

    def _do_copy(self, widget):
        """Copy item back to clipboard and optionally hide window."""
        item = getattr(widget, '_item', None)
        if item is None:
            child = widget.get_child()
            item = getattr(child, '_item', None)
        if item is None:
            return
        if item.content_type == 'text':
            self._clipboard.set(item.content)
        else:
            try:
                texture = Gdk.Texture.new_from_filename(item.thumbnail)
                if texture:
                    self._clipboard.set_texture(texture)
            except Exception:
                pass
        # Hide window if preference is enabled
        if self._app.settings.get_boolean('hide-after-copy'):
            self.set_visible(False)

    def _on_row_activated(self, listbox, row):
        self._do_copy(row)

    def _on_copy_clicked(self, button, row):
        self._do_copy(row)

    def _on_delete_clicked(self, button, row):
        item = getattr(row, '_item', None)
        if item is None:
            return
        for i in range(self._store.get_n_items()):
            if self._store.get_item(i) is item:
                self._store.remove(i)
                self.get_application().save_history()
                return

    def _on_clear_all(self, button):
        self._store.remove_all()
        self.get_application().save_history()

    # ── Persistence ──

    def _on_window_size_changed(self, window, pspec):
        app = self.get_application()
        app.settings.set_int('win-width', self.props.default_width)
        app.settings.set_int('win-height', self.props.default_height)

    def _on_max_items_changed(self, spin):
        self._max_items = spin.get_value_as_int()
        app = self.get_application()
        app.settings.set_int('max-items', self._max_items)
        app.trim_to_max()
        app.save_history()
