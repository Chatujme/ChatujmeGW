import unittest
from unittest import mock

from chatujmegw import config

from .helpers import add_channel, make_inst, make_logged_inst, sent


class TestPartCrash(unittest.TestCase):
    def test_part_nonnumeric_does_not_kill_connection(self):
        # Regression: PART #abc raised ValueError in channel lookup -> disconnect
        inst = make_logged_inst()
        add_channel(inst, 15)
        inst.feed("PART #abc\r\n")  # must not raise
        self.assertIn(" 403 ", sent(inst))
        self.assertEqual(len(inst.channels), 1)  # room untouched

    def test_part_without_params(self):
        inst = make_logged_inst()
        inst.feed("PART\r\n")
        self.assertIn(" 461 ", sent(inst))

    def test_names_nonnumeric_does_not_raise(self):
        inst = make_logged_inst()
        add_channel(inst, 15)
        inst.feed("NAMES #abc\r\n")  # must not raise
        self.assertIn(" 403 ", sent(inst))


class TestJoin(unittest.TestCase):
    def test_join_requires_login(self):
        inst = make_inst()
        inst.feed("JOIN #15\r\n")
        self.assertIn(" 444 ", sent(inst))

    def test_join_invalid_room_id(self):
        inst = make_logged_inst()
        with mock.patch.object(inst, 'join_channel') as hj:
            inst.feed("JOIN #abc\r\n")
            hj.assert_not_called()
        self.assertIn(" 403 ", sent(inst))

    def test_join_multiple_rooms(self):
        inst = make_logged_inst()
        with mock.patch.object(inst, 'join_channel') as hj:
            inst.feed("JOIN #15,#20\r\n")
            hj.assert_has_calls([mock.call("15"), mock.call("20")])


class TestPrivmsg(unittest.TestCase):
    def test_pm_without_room_sends_error(self):
        # Regression: PM with no joined room was silently dropped
        inst = make_logged_inst()
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("PRIVMSG someone :ahoj\r\n")
            st.assert_not_called()
        self.assertIn(" 401 ", sent(inst))
        self.assertIn("join a room first", sent(inst))

    def test_pm_routed_through_first_room(self):
        inst = make_logged_inst()
        add_channel(inst, 15)
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("PRIVMSG pepa :ahoj\r\n")
            st.assert_called_once_with("/m pepa ahoj", 15, "pepa")

    def test_room_message(self):
        inst = make_logged_inst()
        add_channel(inst, 15)
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("PRIVMSG #15 :ahoj vsem\r\n")
            st.assert_called_once_with("ahoj vsem", "15", "#15")

    def test_requires_login(self):
        inst = make_inst()
        inst.feed("PRIVMSG #15 :ahoj\r\n")
        self.assertIn(" 444 ", sent(inst))

    def test_long_message_capped(self):
        # The RFC line cap (512) applies first, so the text can never exceed
        # MAX_MESSAGE_LENGTH - the oversized input must be capped, not crash
        inst = make_logged_inst()
        add_channel(inst, 15)
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed(f"PRIVMSG #15 :{'x' * 600}\r\n")
            self.assertLessEqual(len(st.call_args[0][0]), config.MAX_MESSAGE_LENGTH)


class TestCtcp(unittest.TestCase):
    def test_version_command(self):
        # Regression: module split once rewrote "VERSION" string literals
        inst = make_logged_inst()
        inst.feed("VERSION\r\n")
        self.assertIn(" 351 ", sent(inst))

    def test_ctcp_version_request(self):
        inst = make_logged_inst()
        inst.feed("PRIVMSG test2 :\x01VERSION\x01\r\n")
        self.assertIn("\x01VERSION ChatujmeGW", sent(inst))

    def test_ctcp_ping_echo(self):
        inst = make_logged_inst()
        inst.feed("PRIVMSG test2 :\x01PING 12345\x01\r\n")
        self.assertIn("\x01PING 12345\x01", sent(inst))

    def test_ctcp_time(self):
        inst = make_logged_inst()
        inst.feed("PRIVMSG test2 :\x01TIME\x01\r\n")
        self.assertIn("\x01TIME ", sent(inst))

    def test_ctcp_version_reply_stored(self):
        inst = make_logged_inst()
        inst.feed("NOTICE test2 :\x01VERSION HexChat 2.16.2\x01\r\n")
        self.assertEqual(inst.user.client_version, "HexChat 2.16.2")


class TestCap(unittest.TestCase):
    def test_cap_ls(self):
        inst = make_inst()
        inst.feed("CAP LS 302\r\n")
        self.assertIn("CAP * LS :away-notify", sent(inst))
        self.assertTrue(inst.cap_negotiating)

    def test_cap_req_supported(self):
        inst = make_inst()
        inst.feed("CAP REQ :away-notify\r\n")
        self.assertIn("CAP * ACK :away-notify", sent(inst))

    def test_cap_req_unsupported(self):
        inst = make_inst()
        inst.feed("CAP REQ :sasl\r\n")
        self.assertIn("CAP * NAK :sasl", sent(inst))

    def test_cap_end_triggers_login(self):
        inst = make_inst()
        inst.user.nick = "test2"
        inst.user.username = "test2"
        inst.user.password = "heslo"
        with mock.patch.object(inst, 'authenticate', return_value=True) as cl:
            inst.feed("CAP END\r\n")
            cl.assert_called_once()
        self.assertTrue(inst.user.login)

    def test_login_attempted_once_across_nick_user_pass_and_cap_end(self):
        # Regression: NICK+USER+PASS then CAP END fired authenticate() twice ->
        # 2FA notice relayed twice + two account login attempts
        inst = make_inst()
        with mock.patch.object(inst, 'authenticate', return_value=False) as auth:
            inst.feed("NICK test2\r\n")
            inst.feed("USER test2 0 * :test2\r\n")
            inst.feed("PASS heslo\r\n")
            inst.feed("CAP END\r\n")
            auth.assert_called_once()

    def test_new_password_after_failure_retries(self):
        # A corrected credential must allow another attempt
        inst = make_inst()
        with mock.patch.object(inst, 'authenticate', side_effect=[False, True]) as auth:
            inst.feed("NICK test2\r\n")
            inst.feed("USER test2 0 * :test2\r\n")
            inst.feed("PASS wrong\r\n")
            inst.feed("PASS right\r\n")
            self.assertEqual(auth.call_count, 2)


class TestModeKick(unittest.TestCase):
    def test_mode_plus_o_transfers_admin(self):
        inst = make_logged_inst()
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("MODE #15 +o pepa\r\n")
            st.assert_called_once_with("/predej pepa", "15", "15")
        self.assertIn("MODE #15 +o pepa", sent(inst))

    def test_mode_ban_list_empty(self):
        inst = make_logged_inst()
        inst.feed("MODE #15 +b\r\n")
        self.assertIn(" 368 ", sent(inst))

    def test_kick_sends_command(self):
        inst = make_logged_inst()
        with mock.patch.object(inst, 'send_text') as st:
            inst.feed("KICK #15 pepa :spam\r\n")
            st.assert_called_once_with("/kick pepa spam", "15", "15")
        self.assertIn("KICK #15 pepa :spam", sent(inst))

    def test_kick_needs_params(self):
        inst = make_logged_inst()
        inst.feed("KICK #15\r\n")
        self.assertIn(" 461 ", sent(inst))


class TestListTopic(unittest.TestCase):
    def test_list_renders_rooms(self):
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'get_rooms',
                               return_value='[{"id": 15, "online": 3, "nazev": "Chatujme"}]'):
            inst.feed("LIST\r\n")
        out = sent(inst)
        self.assertIn(" 321 ", out)
        self.assertIn("#15 3 :Chatujme", out)
        self.assertIn(" 323 ", out)

    def test_topic_view(self):
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'get_room',
                               return_value='{"id": 15, "nazev": "Chatujme", "topic": "Vitejte"}'):
            inst.feed("TOPIC #15\r\n")
        self.assertIn("#15 :[Chatujme] Vitejte", sent(inst))

    def test_topic_set_permission_denied(self):
        inst = make_logged_inst()
        with mock.patch.object(inst.api, 'set_topic',
                               return_value='{"code": 403, "message": "Nemas prava"}'):
            inst.feed("TOPIC #15 :novy popis\r\n")
        out = sent(inst)
        self.assertIn(" 482 ", out)
        self.assertIn("Nemas prava", out)


class TestAway(unittest.TestCase):
    def test_away_set_and_unset(self):
        inst = make_logged_inst()
        with mock.patch.object(inst, 'send_text'):
            inst.feed("AWAY :na obede\r\n")
            self.assertEqual(inst.user.away_message, "na obede")
            self.assertIn(" 306 ", sent(inst))
            inst.feed("AWAY\r\n")
            self.assertIsNone(inst.user.away_message)
            self.assertIn(" 305 ", sent(inst))

    def test_message_clears_away(self):
        inst = make_logged_inst()
        add_channel(inst, 15)
        inst.user.away_message = "pryc"
        with mock.patch.object(inst, 'send_text'):
            inst.feed("PRIVMSG #15 :uz jsem tu\r\n")
        self.assertIsNone(inst.user.away_message)
        self.assertIn(" 305 ", sent(inst))


class TestIdler(unittest.TestCase):
    def test_idler_on_off(self):
        inst = make_logged_inst()
        inst.feed("IDLER ON\r\n")
        self.assertTrue(inst.user.idler_enable)
        inst.feed("IDLER OFF\r\n")
        self.assertFalse(inst.user.idler_enable)

    def test_idler_time_minimum(self):
        inst = make_logged_inst()
        original = inst.user.idler_timer
        inst.feed("IDLER TIME 100\r\n")
        self.assertEqual(inst.user.idler_timer, original)
        self.assertIn("Minimum idler time", sent(inst))

    def test_idler_time_and_text(self):
        inst = make_logged_inst()
        inst.feed("IDLER TIME 2000\r\n")
        self.assertEqual(inst.user.idler_timer, 2000)
        inst.feed("IDLER TEXT a, b,c\r\n")
        self.assertEqual(inst.user.idler_text, ["a", "b", "c"])

    def test_idler_status(self):
        inst = make_logged_inst()
        inst.feed("IDLER STATUS\r\n")
        self.assertIn("Idler: OFF", sent(inst))


class TestSmiles(unittest.TestCase):
    def test_smiles_set_modes(self):
        from chatujmegw import textfilters
        inst = make_logged_inst()
        for subcmd, expected in (("CODE", textfilters.SMILES_CODE),
                                 ("URL", textfilters.SMILES_URL),
                                 ("HIDE", textfilters.SMILES_HIDE),
                                 ("TEXT", textfilters.SMILES_TEXT)):
            inst.feed(f"SMILES {subcmd}\r\n")
            self.assertEqual(inst.user.show_smiles, expected, subcmd)
        self.assertIn("set to TEXT", sent(inst))

    def test_smiles_status_shows_current_mode(self):
        inst = make_logged_inst()
        inst.feed("SMILES\r\n")
        self.assertIn("current: TEXT", sent(inst))
        self.assertIn("SMILES CODE", sent(inst))

    def test_smiles_unknown_shows_help(self):
        inst = make_logged_inst()
        inst.feed("SMILES FOO\r\n")
        self.assertIn("current: TEXT", sent(inst))


class TestMisc(unittest.TestCase):
    def test_unknown_command(self):
        inst = make_logged_inst()
        inst.feed("FOOBAR x\r\n")
        self.assertIn(" 421 ", sent(inst))
        self.assertIn("FOOBAR", sent(inst))

    def test_nickserv_identify(self):
        inst = make_logged_inst()
        inst.feed("NS identify heslo\r\n")
        self.assertIn("already identified", sent(inst))

    def test_register_redirects_to_web(self):
        inst = make_inst()
        inst.feed("REGISTER\r\n")
        self.assertIn("chatujme.cz/registrace", sent(inst))

    def test_whois_unknown_nick(self):
        inst = make_logged_inst()
        inst.feed("WHOIS pepa\r\n")
        self.assertIn(" 401 ", sent(inst))

    def test_userhost(self):
        inst = make_logged_inst()
        inst.feed("USERHOST pepa\r\n")
        self.assertIn(" 302 ", sent(inst))

    def test_api_commands_rate_limited(self):
        inst = make_logged_inst()
        inst.user.command_timestamps = [1e12] * config.MAX_COMMANDS_PER_SECOND
        with mock.patch('time.time', return_value=1e12), \
                mock.patch.object(inst.api, 'get_rooms') as api:
            inst.feed("LIST\r\n")
            api.assert_not_called()
        self.assertIn("Rate limit exceeded", sent(inst))
