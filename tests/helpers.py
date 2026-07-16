"""Shared test doubles."""

from chatujmegw.models import RoomStruct
from chatujmegw.session import Chatujme


class FakeSocket:
    def __init__(self):
        self.sent = b''

    def sendall(self, data):
        self.sent += data

    def send(self, data):
        self.sent += data
        return len(data)

    def close(self):
        pass


class FakeHandler:
    def __init__(self):
        self.running = True
        self.instance = None
        self.socket = FakeSocket()


def make_inst():
    handler = FakeHandler()
    inst = Chatujme(FakeSocket(), '127.0.0.1', handler)
    handler.instance = inst
    return inst


def make_logged_inst(nick="test2"):
    inst = make_inst()
    inst.user.login = True
    inst.user.nick = nick
    inst.user.username = nick
    return inst


def add_room(inst, room_id=15):
    room = RoomStruct()
    room.id = room_id
    inst.rooms.append(room)
    return room


def sent(inst):
    return inst.socket.sent.decode('utf-8')
