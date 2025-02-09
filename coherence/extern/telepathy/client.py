
import time

import telepathy
from telepathy.client import Connection, Channel
from telepathy.interfaces import CONN_INTERFACE, CHANNEL_INTERFACE_GROUP, \
     CHANNEL_TYPE_TUBES, \
     CHANNEL_TYPE_TEXT
from telepathy.constants import CONNECTION_HANDLE_TYPE_CONTACT, \
     CONNECTION_HANDLE_TYPE_ROOM, CONNECTION_STATUS_CONNECTED, \
     CONNECTION_STATUS_DISCONNECTED, CONNECTION_STATUS_CONNECTING, \
     TUBE_TYPE_DBUS, TUBE_TYPE_STREAM, TUBE_STATE_LOCAL_PENDING, \
     TUBE_STATE_REMOTE_PENDING, TUBE_STATE_OPEN

from coherence.extern.telepathy.tubeconn import TubeConnection
from coherence import log

class Client(log.Loggable):
    logCategory = "client"

    def __init__(self, connection, muc_id):
        super(Client, self).__init__()

        self.conn = connection
        self.muc_id = muc_id
        self.channel_text = None
        self._unsent_messages = []

        conn_obj = self.conn[CONN_INTERFACE]
        conn_obj.connect_to_signal('StatusChanged', self.status_changed_cb)
        conn_obj.connect_to_signal('NewChannel', self.new_channel_cb)

        self.joined = False

    def start(self):
        self.conn[CONN_INTERFACE].Connect()

    def stop(self):
        try:
            self.conn[CONN_INTERFACE].Disconnect()
        except:
            pass

    def status_changed_cb(self, state, reason):
        if state == CONNECTION_STATUS_CONNECTING:
            self.info('connecting')
        elif state == CONNECTION_STATUS_CONNECTED:
            self.info('connected')
            self.connected_cb()
        elif state == CONNECTION_STATUS_DISCONNECTED:
            self.info('disconnected')

    def connected_cb(self):
        self.self_handle = self.conn[CONN_INTERFACE].GetSelfHandle()
        self.join_muc()

    def join_muc(self):
        # workaround to be sure that the muc service is fully resolved in
        # Salut.
        time.sleep(2)

        conn_obj = self.conn[CONN_INTERFACE]
        self.info("joining muc %r", self.muc_id)
        handle = conn_obj.RequestHandles(CONNECTION_HANDLE_TYPE_ROOM,
                                         [self.muc_id])[0]

        chan_path = conn_obj.RequestChannel(CHANNEL_TYPE_TEXT,
                                            CONNECTION_HANDLE_TYPE_ROOM,
                                            handle, True)

        self.channel_text = Channel(self.conn.dbus_proxy.bus_name,
                                    chan_path)

        channel_obj = self.channel_text[CHANNEL_INTERFACE_GROUP]
        self.self_handle = channel_obj.GetSelfHandle()
        channel_obj.connect_to_signal("MembersChanged",
                                      self.text_channel_members_changed_cb)

        chan_path = conn_obj.RequestChannel(CHANNEL_TYPE_TUBES,
                                            CONNECTION_HANDLE_TYPE_ROOM,
                                            handle, True)
        self.channel_tubes = Channel(self.conn.dbus_proxy.bus_name,
                                     chan_path)

        if self.self_handle in channel_obj.GetMembers():
            self.joined = True
            self.muc_joined()

    def new_channel_cb(self, object_path, channel_type, handle_type, handle,
        suppress_handler):
        if channel_type == CHANNEL_TYPE_TUBES:
            self.channel_tubes = Channel(self.conn.dbus_proxy.bus_name,
                                         object_path)

            tubes_obj = self.channel_tubes[CHANNEL_TYPE_TUBES]
            tubes_obj.connect_to_signal("TubeStateChanged",
                                        self.tube_state_changed_cb)
            tubes_obj.connect_to_signal("NewTube", self.new_tube_cb)
            tubes_obj.connect_to_signal("TubeClosed", self.tube_closed_cb)
            tubes_obj.connect_to_signal("StreamTubeNewConnection",
                                        self.stream_tube_new_connection_cb)

            for tube in tubes_obj.ListTubes():
                id, initiator, type, service, params, state = tube[:6]
                self.new_tube_cb(id, initiator, type, service, params, state)

    def new_tube_cb(self, id, initiator, type, service, params, state):
        conn_obj = self.conn[CONN_INTERFACE]
        initiator_id = conn_obj.InspectHandles(CONNECTION_HANDLE_TYPE_CONTACT,
                                               [initiator])[0]

        tube_type = {TUBE_TYPE_DBUS: "D-Bus",
                     TUBE_TYPE_STREAM: "Stream"}

        tube_state = {TUBE_STATE_LOCAL_PENDING : 'local pending',
                      TUBE_STATE_REMOTE_PENDING : 'remote pending',
                      TUBE_STATE_OPEN : 'open'}

        self.info("new %s tube (%d) offered by %s. Service: %s. State: %s",
                  tube_type[type], id, initiator_id, service,
                  tube_state[state])

        if state == TUBE_STATE_OPEN:
            self.tube_opened(id)

    def tube_opened(self, id):
        self.info("tube %r opened", id)
        group_iface = self.channel_text[CHANNEL_INTERFACE_GROUP]
        try:
            self.tube_conn = TubeConnection(self.conn,
                                            self.channel_tubes[CHANNEL_TYPE_TUBES],
                                            id, group_iface=group_iface)
        except:
            self.tube_conn = None

    def stream_tube_new_connection_cb(self, id, handle):
        conn_obj = self.conn[CONN_INTERFACE]
        contact = conn_obj.InspectHandles(CONNECTION_HANDLE_TYPE_CONTACT,
                                          [handle])[0]
        self.info("new socket connection on tube %u from %s" % (id, contact))

    def tube_state_changed_cb(self, id, state):
        if state == TUBE_STATE_OPEN:
            self.tube_opened(id)

    def tube_closed_cb (self, id):
        self.info("tube %r closed", id)

    def text_channel_members_changed_cb(self, message, added, removed,
                                        local_pending, remote_pending,
                                        actor, reason):
        if self.self_handle in added and not self.joined:
            self.joined = True
            self.muc_joined()

    def muc_joined(self):
        for msg in self._unsent_messages:
            self.send_text(msg)
        self._unsent_messages = []

    def send_text(self, text):
        if self.channel_text:
            self.info("Sending text %r", text)
            channel_obj = self.channel_text[CHANNEL_TYPE_TEXT]
            channel_obj.Send(telepathy.CHANNEL_TEXT_MESSAGE_TYPE_NORMAL, text)
        else:
            self.info("Queing text %r until muc is joined", text)
            self._unsent_messages.append(text)
