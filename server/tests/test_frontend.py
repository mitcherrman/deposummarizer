"""
Static checks for the hostname gate and the logout control. Browser-level
behavior was verified manually (see BEAR_V2_HANDOFF.md, B0.5 addendum).
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.template.loader import render_to_string
from django.test import SimpleTestCase

BASE_JS = Path(__file__).resolve().parent.parent / "static" / "javascript" / "base.js"

APPROVED_HOSTS = {
    "bearsummarizer.com",
    "www.bearsummarizer.com",
    "bear-ai-summarizer.com",
    "127.0.0.1",
    "localhost",
}


class HostnameGateTests(SimpleTestCase):
    def test_allowlist_is_exactly_the_approved_hosts(self):
        source = BASE_JS.read_text(encoding="utf-8")
        match = re.search(r"urls\s*=\s*\[([^\]]*)\]", source)
        self.assertIsNotNone(match)
        hosts = set(re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(1)))
        hosts = {single or double for single, double in hosts}
        self.assertEqual(hosts, APPROVED_HOSTS)


class _ButtonFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_logout_form = False
        self.logout_buttons = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "logoutForm":
            self.in_logout_form = True
        elif tag == "button" and self.in_logout_form:
            self.logout_buttons.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_logout_form = False


class _User:
    is_authenticated = True

    def get_username(self):
        return "synthetic-user"


class LogoutControlTests(SimpleTestCase):
    def logout_buttons(self, user):
        finder = _ButtonFinder()
        finder.feed(render_to_string("base.html", {"user": user}))
        return finder.logout_buttons

    def test_logout_button_does_not_submit_by_itself(self):
        buttons = self.logout_buttons(_User())
        self.assertEqual(len(buttons), 1)
        # a button without type="button" submits the form even when the
        # confirm() dialog is cancelled
        self.assertEqual(buttons[0].get("type"), "button")
        self.assertEqual(buttons[0].get("onclick"), "logoutConfirm()")

    def test_logout_confirm_submits_only_when_confirmed(self):
        source = BASE_JS.read_text(encoding="utf-8")
        body = re.search(r"function logoutConfirm\(\)\s*\{(.*?)\n\}", source, re.S).group(1)
        self.assertRegex(
            body,
            r"^\s*if \(confirm\([^)]*\)\) \{\s*document\.getElementById\(\"logoutForm\"\)\.submit\(\);\s*\}\s*$",
        )
