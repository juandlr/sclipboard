"""
tray.py — System tray icon via Gio.DBusConnection (pure GLib, zero extra deps).
Publishes org.kde.StatusNotifierItem on the session bus using
Gio.DBusConnection (built into GLib). Uses edit-copy-symbolic
from the system icon theme — no need to bundle or load pixel data.
"""
import os
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

_BUS_NAME = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
_OBJ_PATH = '/StatusNotifierItem'
_MENU_PATH = '/MenuBar'
_ICON_NAME = 'edit-copy-symbolic'


class _StatusNotifierItem:
    """Exports org.kde.StatusNotifierItem and handles Properties."""

    def __init__(self, connection, on_activate, on_context_menu):
        self._connection = connection
        self._on_activate = on_activate
        self._on_context_menu = on_context_menu

        node_info = self._build_introspection()
        self._reg_ids = []
        for iface_info in node_info.interfaces:
            reg_id = connection.register_object(
                _OBJ_PATH, iface_info,
                self._handle_method_call,
                self._handle_get_property,
                self._handle_set_property,
            )
            self._reg_ids.append(reg_id)

    def _build_introspection(self):
        xml = """<!DOCTYPE node PUBLIC '-//freedesktop//DTD D-BUS Object Introspection 1.0//EN'
 'http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd'>
<node>
  <interface name='org.kde.StatusNotifierItem'>
    <method name='Activate'><arg type='i' name='x' direction='in'/><arg type='i' name='y' direction='in'/></method>
    <method name='SecondaryActivate'><arg type='i' name='x' direction='in'/><arg type='i' name='y' direction='in'/></method>
    <method name='ContextMenu'><arg type='i' name='x' direction='in'/><arg type='i' name='y' direction='in'/></method>
    <method name='Scroll'><arg type='i' name='delta' direction='in'/><arg type='s' name='orientation' direction='in'/></method>
    <signal name='NewIcon'/>
    <signal name='NewTitle'/>
    <signal name='NewStatus'><arg type='s' name='status' direction='out'/></signal>
    <property name='Id' type='s' access='read'/>
    <property name='Category' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconName' type='s' access='read'/>
    <property name='IconPixmap' type='a(iiay)' access='read'/>
    <property name='Title' type='s' access='read'/>
    <property name='ItemIsMenu' type='b' access='read'/>
    <property name='WindowId' type='i' access='read'/>
    <property name='Menu' type='o' access='read'/>
    <property name='ToolTip' type='(sa(iiay)ss)' access='read'/>
  </interface>
</node>"""
        return Gio.DBusNodeInfo.new_for_xml(xml)

    def _handle_method_call(self, connection, sender, object_path, interface_name,
                            method_name, parameters, invocation):
        if interface_name == 'org.freedesktop.DBus.Properties':
            if method_name == 'Get':
                iface = parameters.get_child_value(0).get_string()
                prop = parameters.get_child_value(1).get_string()
                val = self._get_all(iface).lookup_value(prop)
                invocation.return_value(GLib.Variant('(v)', (val,)))
            elif method_name == 'GetAll':
                iface = parameters.get_child_value(0).get_string()
                val = self._get_all(iface)
                invocation.return_value(GLib.Variant('(a{sv})', (val,)))
        elif interface_name == 'org.kde.StatusNotifierItem':
            if method_name == 'Activate':
                if self._on_activate:
                    self._on_activate()
                invocation.return_value(GLib.Variant('()', ()))
            elif method_name == 'SecondaryActivate':
                invocation.return_value(GLib.Variant('()', ()))
            elif method_name == 'ContextMenu':
                if self._on_context_menu:
                    x = parameters.get_child_value(0).get_int32()
                    y = parameters.get_child_value(1).get_int32()
                    self._on_context_menu(x, y)
                invocation.return_value(GLib.Variant('()', ()))
            elif method_name == 'Scroll':
                invocation.return_value(GLib.Variant('()', ()))

    def _handle_get_property(self, connection, sender, object_path, interface_name, key):
        if interface_name != 'org.kde.StatusNotifierItem':
            return None
        return self._get_all(interface_name).lookup_value(key)

    def _handle_set_property(self, connection, sender, object_path, interface_name, key, value):
        return False

    def _get_all(self, iface):
        if iface != 'org.kde.StatusNotifierItem':
            return GLib.Variant('a{sv}', {})
        tooltip = GLib.Variant('(sa(iiay)ss)', (_ICON_NAME, [], 'Clipboard Manager', ''))
        return GLib.Variant('a{sv}', {
            'Id': GLib.Variant('s', 'sclipboard'),
            'Category': GLib.Variant('s', 'ApplicationStatus'),
            'Status': GLib.Variant('s', 'Active'),
            'IconName': GLib.Variant('s', _ICON_NAME),
            'IconPixmap': GLib.Variant('a(iiay)', []),
            'Title': GLib.Variant('s', 'Clipboard Manager'),
            'ItemIsMenu': GLib.Variant('b', False),
            'WindowId': GLib.Variant('i', 0),
            'Menu': GLib.Variant('o', _MENU_PATH),
            'ToolTip': tooltip,
        })


class _DbusMenu:
    """Minimal com.canonical.dbusmenu interface."""

    def __init__(self, connection, menu_items):
        self._connection = connection
        self._menu_items = menu_items
        node_info = self._build_introspection()
        self._reg_ids = []
        for iface_info in node_info.interfaces:
            reg_id = connection.register_object(
                _MENU_PATH, iface_info,
                self._handle_method_call,
                self._handle_get_property,
                self._handle_set_property,
            )
            self._reg_ids.append(reg_id)

    def _build_introspection(self):
        xml = """<!DOCTYPE node PUBLIC '-//freedesktop//DTD D-BUS Object Introspection 1.0//EN'
 'http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd'>
<node>
  <interface name='com.canonical.dbusmenu'>
    <method name='GetLayout'><arg type='i' name='parent_id' direction='in'/><arg type='i' name='depth' direction='in'/><arg type='as' name='prop_names' direction='in'/><arg type='u' name='revision' direction='out'/><arg type='(ia{sv}av)' name='layout' direction='out'/></method>
    <method name='GetGroupProperties'><arg type='ai' name='ids' direction='in'/><arg type='as' name='prop_names' direction='in'/><arg type='a(ia{sv})' name='props' direction='out'/></method>
    <method name='GetProperty'><arg type='i' name='id' direction='in'/><arg type='s' name='prop_name' direction='in'/><arg type='v' name='value' direction='out'/></method>
    <method name='AboutToShow'><arg type='i' name='id' direction='in'/><arg type='b' name='need_update' direction='out'/></method>
    <method name='Event'><arg type='i' name='id' direction='in'/><arg type='s' name='event_id' direction='in'/><arg type='v' name='data' direction='in'/><arg type='u' name='timestamp' direction='in'/></method>
    <property name='Version' type='u' access='read'/>
    <property name='TextDirection' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconThemePath' type='as' access='read'/>
  </interface>
</node>"""
        return Gio.DBusNodeInfo.new_for_xml(xml)

    def _handle_method_call(self, connection, sender, object_path, interface_name,
                            method_name, parameters, invocation):
        if interface_name == 'org.freedesktop.DBus.Properties':
            if method_name == 'Get':
                iface = parameters.get_child_value(0).get_string()
                prop = parameters.get_child_value(1).get_string()
                val = self._get_all(iface).lookup_value(prop)
                invocation.return_value(GLib.Variant('(v)', (val,)))
            elif method_name == 'GetAll':
                iface = parameters.get_child_value(0).get_string()
                val = self._get_all(iface)
                invocation.return_value(GLib.Variant('(a{sv})', (val,)))
        elif interface_name == 'com.canonical.dbusmenu':
            if method_name == 'GetLayout':
                parent_id = parameters.get_child_value(0).get_int32()
                result = self._get_layout(parent_id)
                invocation.return_value(result)
            elif method_name == 'GetGroupProperties':
                ids = [parameters.get_child_value(0).get_child_value(i).get_int32()
                       for i in range(parameters.get_child_value(0).n_children())]
                result = self._get_group_properties(ids)
                outer = GLib.VariantBuilder(GLib.VariantType.new('(a(ia{sv}))'))
                outer.add_value(result)
                invocation.return_value(outer.end())
            elif method_name == 'GetProperty':
                item_id = parameters.get_child_value(0).get_int32()
                prop_name = parameters.get_child_value(1).get_string()
                invocation.return_value(GLib.Variant('(v)', (self._get_property(item_id, prop_name),)))
            elif method_name == 'AboutToShow':
                invocation.return_value(GLib.Variant('(b)', (False,)))
            elif method_name == 'Event':
                item_id = parameters.get_child_value(0).get_int32()
                if 1 <= item_id <= len(self._menu_items):
                    callback = self._menu_items[item_id - 1][3] if len(self._menu_items[item_id - 1]) > 3 else None
                    if callback:
                        callback()
                invocation.return_value(GLib.Variant('()', ()))

    def _handle_get_property(self, connection, sender, object_path, interface_name, key):
        if interface_name != 'com.canonical.dbusmenu':
            return None
        return self._get_all(interface_name).lookup_value(key)

    def _handle_set_property(self, connection, sender, object_path, interface_name, key, value):
        return False

    def _get_layout(self, parent_id):
        outer = GLib.VariantBuilder(GLib.VariantType.new('(u(ia{sv}av))'))
        outer.add_value(GLib.Variant('u', 0))
        if parent_id != 0:
            layout = GLib.VariantBuilder(GLib.VariantType.new('(ia{sv}av)'))
            layout.add_value(GLib.Variant('i', 0))
            layout.add_value(GLib.Variant('a{sv}', {}))
            layout.add_value(GLib.Variant('av', []))
            outer.add_value(layout.end())
            return outer.end()
        children = GLib.VariantBuilder(GLib.VariantType.new('av'))
        for i, item_info in enumerate(self._menu_items):
            label, enabled, item_type = item_info[0], item_info[1], item_info[2]
            child = GLib.VariantBuilder(GLib.VariantType.new('(ia{sv}av)'))
            child.add_value(GLib.Variant('i', i + 1))
            child_props = GLib.VariantBuilder(GLib.VariantType.new('a{sv}'))
            for k, v in {
                'label': GLib.Variant('s', label),
                'enabled': GLib.Variant('b', enabled),
                'visible': GLib.Variant('b', True),
                'type': GLib.Variant('s', item_type),
                'toggle-type': GLib.Variant('s', ''),
                'toggle-state': GLib.Variant('i', 0),
                'children-display': GLib.Variant('s', ''),
                'shortcut': GLib.Variant('a(ii)', []),
            }.items():
                child_props.add_value(GLib.Variant('{sv}', (k, v)))
            child.add_value(child_props.end())
            child.add_value(GLib.Variant('av', []))
            children.add_value(GLib.Variant('v', child.end()))
        layout = GLib.VariantBuilder(GLib.VariantType.new('(ia{sv}av)'))
        layout.add_value(GLib.Variant('i', 0))
        layout.add_value(GLib.Variant('a{sv}', {}))
        layout.add_value(children.end())
        outer.add_value(layout.end())
        return outer.end()

    def _get_group_properties(self, ids):
        builder = GLib.VariantBuilder(GLib.VariantType.new('a(ia{sv})'))
        for item_id in ids:
            if 1 <= item_id <= len(self._menu_items):
                label, enabled, item_type = self._menu_items[item_id - 1][0], self._menu_items[item_id - 1][1], self._menu_items[item_id - 1][2]
                props = {
                    'label': GLib.Variant('s', label),
                    'enabled': GLib.Variant('b', enabled),
                    'visible': GLib.Variant('b', True),
                    'type': GLib.Variant('s', item_type),
                }
            else:
                props = {}
            entry = GLib.VariantBuilder(GLib.VariantType.new('(ia{sv})'))
            entry.add_value(GLib.Variant('i', item_id))
            dict_b = GLib.VariantBuilder(GLib.VariantType.new('a{sv}'))
            for k, v in props.items():
                dict_b.add_value(GLib.Variant('{sv}', (k, v)))
            entry.add_value(dict_b.end())
            builder.add_value(entry.end())
        return builder.end()

    def _get_property(self, item_id, prop_name):
        defaults = {
            'type': GLib.Variant('s', 'standard'),
            'children-display': GLib.Variant('s', 'submenu'),
            'enabled': GLib.Variant('b', True),
            'visible': GLib.Variant('b', True),
        }
        if item_id == 0:
            return defaults.get(prop_name, GLib.Variant('s', ''))
        if 1 <= item_id <= len(self._menu_items):
            label, enabled, item_type = self._menu_items[item_id - 1][0], self._menu_items[item_id - 1][1], self._menu_items[item_id - 1][2]
            props = {
                'label': GLib.Variant('s', label),
                'enabled': GLib.Variant('b', enabled),
                'type': GLib.Variant('s', item_type),
            }
            return props.get(prop_name, GLib.Variant('b', False))
        return GLib.Variant('b', False)

    def _get_all(self, iface):
        if iface != 'com.canonical.dbusmenu':
            return GLib.Variant('a{sv}', {})
        return GLib.Variant('a{sv}', {
            'Version': GLib.Variant('u', 4),
            'TextDirection': GLib.Variant('s', 'ltr'),
            'Status': GLib.Variant('s', 'normal'),
            'IconThemePath': GLib.Variant('as', []),
        })


class TrayIcon:
    """System tray icon via Gio.DBusConnection — zero extra dependencies."""

    def __init__(self, on_activate=None):
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._menu_items = [
            ('Toggle Clipboard', True, 'standard', None),
            ('', True, 'separator', None),
            ('Quit', True, 'standard', None),
        ]
        self._sni = _StatusNotifierItem(self._connection, on_activate, self._on_context_menu)
        self._menu = _DbusMenu(self._connection, self._menu_items)

        def _name_acquired(conn, name):
            print(f'[tray] name acquired: {name}', flush=True)
        def _name_lost(conn, name):
            print(f'[tray] name lost: {name}', flush=True)
        self._name_id = Gio.bus_own_name_on_connection(
            self._connection, _BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            _name_acquired, _name_lost,
        )
        self._connection.call_sync(
            'org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
            'org.kde.StatusNotifierWatcher', 'RegisterStatusNotifierItem',
            GLib.Variant('(s)', (_BUS_NAME,)),
            None,
            Gio.DBusCallFlags.NONE, -1, None,
        )
        print('[tray] registered', flush=True)

    def _on_context_menu(self, x, y):
        pass

    def set_open_callback(self, callback):
        self._menu_items[0] = ('Toggle Clipboard', True, 'standard', callback)

    def set_quit_callback(self, callback):
        self._menu_items[2] = ('Quit', True, 'standard', callback)
