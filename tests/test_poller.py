import unittest

from chatujmegw.poller import GetMessages

from .helpers import add_room, make_logged_inst, sent


def make_poller():
    inst = make_logged_inst()
    room = add_room(inst, 15)
    return GetMessages(inst), inst, room


class TestSystemMessages(unittest.TestCase):
    def test_user_joined(self):
        poller, inst, room = make_poller()
        msg = "12:00:01: Uživatel pepa vstoupil do místnosti."
        poller.handle_system_message(None, msg, room)
        self.assertIn("JOIN #15", sent(inst))
        self.assertEqual(room.users[-1].nick, "pepa")
        self.assertEqual(room.users[-1].sex, "boys")

    def test_user_joined_female(self):
        poller, inst, room = make_poller()
        msg = "12:00:01: Uživatelka jana vstoupila do místnosti."
        poller.handle_system_message(None, msg, room)
        self.assertEqual(room.users[-1].nick, "jana")
        self.assertEqual(room.users[-1].sex, "girls")

    def test_user_left(self):
        poller, inst, room = make_poller()
        msg = "12:00:02: Uživatel pepa odešel z místnosti."
        poller.handle_system_message(None, msg, room)
        self.assertIn("PART #15", sent(inst))

    def test_user_kicked(self):
        poller, inst, room = make_poller()
        msg = "Uživatel pepa byl vykopnut z místnosti. Vykopl jej admin z důvodu: spam."
        poller.handle_system_message(None, msg, room)
        self.assertIn("KICK #15 pepa :spam", sent(inst))

    def test_generic_message_becomes_notice(self):
        poller, inst, room = make_poller()
        msg = "19:10:13: Místnost byla uzamčena"
        poller.handle_system_message(None, msg, room)
        out = sent(inst)
        self.assertIn("NOTICE #15 :Místnost byla uzamčena", out)
        self.assertNotIn("19:10:13", out)
