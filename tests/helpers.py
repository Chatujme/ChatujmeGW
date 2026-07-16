"""Shared test doubles."""

from chatujmegw.models import Channel
from chatujmegw.session import ClientSession


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
    inst = ClientSession(FakeSocket(), '127.0.0.1', handler)
    handler.instance = inst
    return inst


def make_logged_inst(nick="test2"):
    inst = make_inst()
    inst.user.login = True
    inst.user.nick = nick
    inst.user.username = nick
    return inst


def add_channel(inst, channel_id=15):
    channel = Channel()
    channel.id = channel_id
    inst.channels.append(channel)
    return channel


def sent(inst):
    return inst.socket.sent.decode('utf-8')
