import unittest

from chatujmegw import textfilters


class TestTextFilters(unittest.TestCase):
    def test_clean_urls_unwraps_redirect(self):
        msg = '<a href="//link.chatujme.cz/redirect?url=https%3A%2F%2Fexample.com%2Fx" target="_blank">example</a>'
        self.assertEqual(textfilters.clean_urls(msg), "https://example.com/x")

    def test_clean_urls_protocol_relative(self):
        msg = '<a href="//example.com/a" target="_blank">a</a>'
        self.assertEqual(textfilters.clean_urls(msg), "https://example.com/a")

    def test_clean_smiles_text_mode(self):
        msg = "<img src='https://static.chatujme.cz/smiles/42.gif' alt=':)' aria-label='x' title='x'>"
        self.assertEqual(textfilters.clean_smiles(msg, 1), "*42*")

    def test_clean_smiles_hide_mode(self):
        msg = "ahoj <img src='https://static.chatujme.cz/smiles/42.gif' alt=':)'>"
        self.assertEqual(textfilters.clean_smiles(msg, 0), "ahoj ")

    def test_clean_smiles_url_mode(self):
        msg = "<img src='https://static.chatujme.cz/smiles/42.gif' alt=':)'>"
        self.assertEqual(textfilters.clean_smiles(msg, 2), "https://static.chatujme.cz/smiles/42.gif")

    def test_clean_highlight(self):
        msg = "<span style='background:#eded1a'>test2</span>: ahoj"
        self.assertEqual(textfilters.clean_highlight(msg), "test2: ahoj")

    def test_clean_mailto(self):
        msg = '<a href="mailto:a@b.cz">a@b.cz</a>'
        self.assertEqual(textfilters.clean_urls_mailto(msg), "a@b.cz")

    def test_clean_message_pipeline(self):
        msg = ("<span style='background:#eded1a'>test2</span>: "
               "<img src='https://static.chatujme.cz/smiles/7.gif' alt=':)'> "
               '<a href="//example.com/x" target="_blank">example.com/x</a>')
        self.assertEqual(textfilters.clean_message(msg, 1), "test2: *7* https://example.com/x")
