import unittest

from chatujmegw import textfilters


class TestTextFilters(unittest.TestCase):
    def test_clean_urls_unwraps_redirect(self):
        msg = '<a href="//link.chatujme.cz/redirect?url=https%3A%2F%2Fexample.com%2Fx" target="_blank">example</a>'
        self.assertEqual(textfilters.clean_urls(msg), "https://example.com/x")

    def test_clean_urls_protocol_relative(self):
        msg = '<a href="//example.com/a" target="_blank">a</a>'
        self.assertEqual(textfilters.clean_urls(msg), "https://example.com/a")

    WEB_SMILEY = ("<img src='https://static.chatujme.cz/smiles/2.gif' alt='&ast;2&ast;' "
                  "aria-label='Smajlík - LOL' title='Smajlík - LOL' class='chat-smiley'>")
    CATEGORY_SMILEY = ("<img src='https://static.chatujme.cz/smiles/110.gif' alt='&ast;110&ast;' "
                       "aria-label='Smajlík z kategorie Zvířata' title='Smajlík z kategorie Zvířata' class='chat-smiley'>")

    def test_text_mode_uses_aria_description(self):
        # Same source of truth the web uses for accessibility (aria-label)
        self.assertEqual(textfilters.clean_smiles(self.WEB_SMILEY, textfilters.SMILES_TEXT), "*LOL*")

    def test_text_mode_category_only_falls_back_to_code(self):
        # "Smajlík z kategorie X" carries no real meaning - keep the round-trippable code
        self.assertEqual(textfilters.clean_smiles(self.CATEGORY_SMILEY, textfilters.SMILES_TEXT), "*110*")

    def test_text_mode_no_aria_falls_back_to_code(self):
        msg = "<img src='https://static.chatujme.cz/smiles/42.gif' alt=':)' title='x'>"
        self.assertEqual(textfilters.clean_smiles(msg, textfilters.SMILES_TEXT), "*42*")

    def test_text_mode_decodes_html_entities(self):
        msg = ("<img src='https://static.chatujme.cz/smiles/9.gif' alt='&ast;9&ast;' "
               "aria-label='Smajlík - Drž&#39;í růži'>")
        self.assertEqual(textfilters.clean_smiles(msg, textfilters.SMILES_TEXT), "*Drž'í růži*")

    def test_code_mode_always_code(self):
        self.assertEqual(textfilters.clean_smiles(self.WEB_SMILEY, textfilters.SMILES_CODE), "*2*")

    def test_url_mode_with_aria_markup(self):
        self.assertEqual(textfilters.clean_smiles(self.WEB_SMILEY, textfilters.SMILES_URL),
                         "https://static.chatujme.cz/smiles/2.gif")

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
