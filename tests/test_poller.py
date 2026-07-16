import unittest

from chatujmegw.poller import MessagePoller

from .helpers import add_channel, make_logged_inst, sent


def make_poller():
    inst = make_logged_inst()
    room = add_channel(inst, 15)
    return MessagePoller(inst), inst, room


class TestSystemMessages(unittest.TestCase):
    def test_user_joined(self):
        poller, inst, room = make_poller()
        msg = "12:00:01: Uživatel pepa vstoupil do místnosti."
        poller.handle_system_message(msg, room)
        self.assertIn("JOIN #15", sent(inst))
        self.assertEqual(room.members[-1].nick, "pepa")
        self.assertEqual(room.members[-1].sex, "boys")

    def test_user_joined_female(self):
        poller, inst, room = make_poller()
        msg = "12:00:01: Uživatelka jana vstoupila do místnosti."
        poller.handle_system_message(msg, room)
        self.assertEqual(room.members[-1].nick, "jana")
        self.assertEqual(room.members[-1].sex, "girls")

    def test_user_left(self):
        poller, inst, room = make_poller()
        msg = "12:00:02: Uživatel pepa odešel z místnosti."
        poller.handle_system_message(msg, room)
        self.assertIn("PART #15", sent(inst))

    def test_user_kicked(self):
        poller, inst, room = make_poller()
        msg = "Uživatel pepa byl vykopnut z místnosti. Vykopl jej admin z důvodu: spam."
        poller.handle_system_message(msg, room)
        self.assertIn("KICK #15 pepa :spam", sent(inst))

    def test_leaving_member_removed_from_roster(self):
        # Regression: PART/KICK/removal did not drop the member from channel.members,
        # leaving NAMES/WHOIS/hostmask stale
        from chatujmegw.models import ChannelMember
        poller, inst, room = make_poller()
        for nm in ("pepa", "jana"):
            m = ChannelMember()
            m.nick = nm
            room.members.append(m)
        poller.handle_system_message("12:00:02: Uživatel pepa odešel z místnosti.", room)
        self.assertEqual([m.nick for m in room.members], ["jana"])

    def test_kicked_member_removed_from_roster(self):
        from chatujmegw.models import ChannelMember
        poller, inst, room = make_poller()
        m = ChannelMember()
        m.nick = "pepa"
        room.members.append(m)
        poller.handle_system_message(
            "Uživatel pepa byl vykopnut z místnosti. Vykopl jej admin z důvodu: spam.", room)
        self.assertEqual(room.members, [])


class TestOutboundSanitize(unittest.TestCase):
    def _run_one_pass(self, inst, poller, payload):
        import json
        from unittest import mock

        inst.user.poll_interval = 0

        def stop(_now):
            poller.running = False
            return True

        with mock.patch.object(inst.api, 'get_messages', return_value=json.dumps(payload)), \
                mock.patch.object(inst, 'ping_keepalive', side_effect=stop):
            poller.run()

    def test_crlf_in_api_message_cannot_inject(self):
        # Regression: a newline in an API message field could forge an IRC line
        poller, inst, room = make_poller()
        room.first_load = False
        self._run_one_pass(inst, poller, {
            "mess": [{"id": 5, "typ": 0, "nick": "pepa",
                      "zprava": "hi\r\nKICK #15 victim :hax", "komu": ""}],
            "sayAgo": {}})
        out = sent(inst)
        # CRLF stripped -> the payload stays inline in one PRIVMSG, never a real line
        self.assertIn("PRIVMSG #15 :hiKICK #15 victim :hax", out)
        self.assertNotIn("\nKICK", out)
        self.assertEqual(len([l for l in out.split("\r\n") if l]), 1)

    def test_crlf_in_api_nick_cannot_inject(self):
        poller, inst, room = make_poller()
        room.first_load = False
        self._run_one_pass(inst, poller, {
            "mess": [{"id": 6, "typ": 0, "nick": "evil\r\nNOTICE x :y", "zprava": "hi", "komu": ""}],
            "sayAgo": {}})
        # exactly one line emitted for this message (no injected second line)
        lines = [l for l in sent(inst).split("\r\n") if "PRIVMSG" in l or "NOTICE" in l]
        self.assertEqual(len(lines), 1)

    def test_generic_message_becomes_notice(self):
        poller, inst, room = make_poller()
        msg = "19:10:13: Místnost byla uzamčena"
        poller.handle_system_message(msg, room)
        out = sent(inst)
        self.assertIn("NOTICE #15 :Místnost byla uzamčena", out)
        self.assertNotIn("19:10:13", out)
