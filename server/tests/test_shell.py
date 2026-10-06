"""
B1 global shell invariants: product brand, the `msg` notice, navigation,
auth form contracts and static asset references. Visual design, responsive
layout and reduced motion were verified in a browser (see
BEAR_V2_HANDOFF.md, B1 addendum); these tests guard the contracts behind
them, not CSS values.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

PAGE_TEMPLATES = {
    "home.html": "/home",
    "output.html": "/output",
    "login.html": "/login",
    "new.html": "/new",
    "about.html": "/about",
    "contact.html": "/contact",
    "404.html": "/missing-page",
}

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
             "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children = []
        self.text = ""

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def find_all(self, tag=None, cls=None, **attrs):
        return [
            n for n in self.iter()
            if (tag is None or n.tag == tag)
            and (cls is None or cls in n.classes)
            and all(n.attrs.get(k.replace("_", "-")) == v for k, v in attrs.items())
        ]

    def all_text(self):
        return self.text + "".join(c.all_text() for c in self.children)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node("#root", [])
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        self.current.text += data


class _User:
    is_authenticated = True

    def get_username(self):
        return "synthetic-user"


def render(template, path="/home", user=None, **context):
    request = RequestFactory().get(path)
    request.user = user or AnonymousUser()
    html = render_to_string(template, context, request=request)
    builder = TreeBuilder()
    builder.feed(html)
    return html, builder.root


class BrandTests(SimpleTestCase):
    def test_every_page_is_branded_bearsummarizer(self):
        for template, path in PAGE_TEMPLATES.items():
            with self.subTest(template=template):
                html, root = render(template, path)
                title = root.find_all("title")[0].all_text()
                self.assertIn("BearSummarizer", title)
                self.assertNotIn("Deposum", html)
                self.assertTrue(root.find_all("a", cls="brand", href="/home"))

    def test_brand_mark_has_alt_text(self):
        _, root = render("about.html", "/about")
        mark = root.find_all("img", cls="brand__mark")[0]
        self.assertEqual(mark.attrs.get("alt"), "BEAR")


class NoticeTests(SimpleTestCase):
    MESSAGE = "Incorrect username/password."

    def test_msg_renders_one_semantic_dismissible_notice(self):
        for template, path in PAGE_TEMPLATES.items():
            if template == "404.html":
                continue  # the 404 view never passes msg
            with self.subTest(template=template):
                html, root = render(template, path, msg=self.MESSAGE)
                notices = root.find_all(cls="msg-container")
                self.assertEqual(len(notices), 1)
                notice = notices[0]
                self.assertEqual(notice.attrs.get("role"), "alert")
                texts = notice.find_all("p", cls="notice__text")
                self.assertEqual([t.all_text().strip() for t in texts], [self.MESSAGE])
                self.assertNotIn("<c ", html)
                buttons = notice.find_all("button")
                self.assertEqual(len(buttons), 1)
                self.assertEqual(buttons[0].attrs.get("type"), "button")
                self.assertEqual(buttons[0].attrs.get("onclick"), "removeMessage()")
                self.assertTrue(buttons[0].attrs.get("aria-label"))

    def test_no_notice_without_msg(self):
        for template, path in PAGE_TEMPLATES.items():
            with self.subTest(template=template):
                _, root = render(template, path)
                self.assertEqual(root.find_all(cls="msg-container"), [])

    def test_msg_is_escaped(self):
        html, _ = render("login.html", "/login", msg="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_remove_message_hook_still_targets_the_notice(self):
        source = (STATIC_DIR / "javascript" / "base.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelector(".msg-container")', source)


class NavigationTests(SimpleTestCase):
    def test_primary_links_and_anonymous_login(self):
        _, root = render("about.html", "/about")
        nav = root.find_all("nav", aria_label="Primary")[0]
        hrefs = [a.attrs.get("href") for a in nav.find_all("a")]
        for href in ("/home", "/about", "/contact", "/login"):
            self.assertIn(href, hrefs)
        self.assertEqual(nav.find_all(id="logoutForm"), [])

    def test_active_page_is_marked_current(self):
        _, root = render("about.html", "/about")
        current = [a.attrs["href"] for a in root.find_all("a", cls="site-nav__link")
                   if a.attrs.get("aria-current") == "page"]
        self.assertEqual(current, ["/about"])

    def test_mobile_toggle_controls_the_menu(self):
        _, root = render("about.html", "/about")
        toggle = root.find_all("button", cls="site-nav__toggle")[0]
        self.assertEqual(toggle.attrs.get("type"), "button")
        self.assertEqual(toggle.attrs.get("aria-expanded"), "false")
        self.assertEqual(toggle.attrs.get("data-bs-toggle"), "collapse")
        self.assertEqual(toggle.attrs.get("data-bs-target"), "#" + toggle.attrs.get("aria-controls"))
        self.assertTrue(toggle.all_text().strip())  # visible label, not icon-only
        menu = root.find_all(id=toggle.attrs["aria-controls"])
        self.assertEqual(len(menu), 1)
        self.assertIn("collapse", menu[0].classes)

    def test_authenticated_account_state(self):
        html, root = render("about.html", "/about", user=_User())
        self.assertIn("synthetic-user", html)
        account = root.find_all(cls="site-nav__account")[0]
        self.assertEqual([a for a in account.find_all("a") if a.attrs.get("href") == "/login"], [])
        form = account.find_all("form", id="logoutForm")[0]
        self.assertEqual((form.attrs.get("method"), form.attrs.get("action")), ("POST", "/logout"))
        self.assertTrue(form.find_all("input", name="csrfmiddlewaretoken"))

    def test_skip_link_targets_main(self):
        _, root = render("about.html", "/about")
        skip = root.find_all("a", cls="skip-link")[0]
        self.assertEqual(skip.attrs.get("href"), "#main-content")
        self.assertEqual(len(root.find_all("main", id="main-content")), 1)


class ShellGateTests(SimpleTestCase):
    def test_body_hidden_gate_runs_base_js_first(self):
        _, root = render("about.html", "/about")
        body = root.find_all("body")[0]
        self.assertIn("hidden", body.attrs)
        first = body.children[0]
        self.assertEqual(first.tag, "script")
        self.assertTrue(first.attrs.get("src", "").endswith("javascript/base.js"))

    @override_settings(DEBUG=False)
    def test_unknown_route_uses_the_custom_404(self):
        response = self.client.get("/definitely-not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, 'href="/home"', status_code=404)


class AuthFormContractTests(SimpleTestCase):
    def assert_labels_match_inputs(self, form):
        ids = [i.attrs.get("id") for i in form.find_all("input") if i.attrs.get("id")]
        fors = [label.attrs.get("for") for label in form.find_all("label")]
        self.assertEqual(len(fors), len(set(fors)), "duplicate label 'for'")
        for target in fors:
            self.assertIn(target, ids)

    def test_login_form_contract(self):
        _, root = render("login.html", "/login")
        form = root.find_all("form", id="form")[0]
        self.assertEqual((form.attrs.get("method"), form.attrs.get("action")), ("POST", "/auth"))
        self.assertTrue(form.find_all("input", name="csrfmiddlewaretoken"))
        names = [i.attrs.get("name") for i in form.find_all("input", cls="form-control")]
        self.assertEqual(names, ["username", "password"])
        self.assertEqual(form.find_all("input", name="password")[0].attrs.get("type"), "password")
        self.assertEqual(form.find_all("button", type="submit")[0].all_text().strip(), "Log in")
        self.assert_labels_match_inputs(form)
        self.assertTrue(root.find_all("a", href="/new"))

    def test_create_account_form_contract(self):
        _, root = render("new.html", "/new")
        form = root.find_all("form", id="form")[0]
        self.assertEqual((form.attrs.get("method"), form.attrs.get("action")), ("POST", "/create"))
        self.assertTrue(form.find_all("input", name="csrfmiddlewaretoken"))
        # new.js reads the page's .form-control inputs by position
        controls = root.find_all("input", cls="form-control")
        self.assertEqual([c.attrs.get("name") for c in controls],
                         ["username", "password", "password-confirm"])
        button = form.find_all("button", id="create-btn")[0]
        self.assertEqual(button.attrs.get("type"), "submit")
        self.assertIn("disabled", button.attrs)
        self.assertEqual(len(form.find_all(id="warning-box")), 1)
        self.assertTrue(any(s.attrs.get("src", "").endswith("javascript/new.js")
                            for s in form.find_all("script")))
        self.assert_labels_match_inputs(form)
        self.assertTrue(root.find_all("a", href="/login"))


class StaticAssetTests(SimpleTestCase):
    def test_template_static_references_exist(self):
        for template, path in PAGE_TEMPLATES.items():
            with self.subTest(template=template):
                _, root = render(template, path, msg="x")
                refs = [n.attrs.get("src") or n.attrs.get("href") for n in root.iter()
                        if n.tag in ("img", "script", "link")]
                for ref in refs:
                    if ref and ref.startswith("/static/"):
                        self.assertTrue((STATIC_DIR / ref[len("/static/"):]).is_file(), ref)

    def test_stylesheet_urls_exist(self):
        for css in (STATIC_DIR / "styles").glob("*.css"):
            source = css.read_text(encoding="utf-8")
            for ref in re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", source):
                if ref.startswith("data:"):
                    continue
                with self.subTest(css=css.name, ref=ref):
                    target = (STATIC_DIR / ref[len("/static/"):]) if ref.startswith("/static/") \
                        else (css.parent / ref)
                    self.assertTrue(target.resolve().is_file(), ref)

    def test_reduced_motion_is_handled(self):
        source = (STATIC_DIR / "styles" / "base.css").read_text(encoding="utf-8")
        self.assertRegex(source, r"@media\s*\(prefers-reduced-motion:\s*reduce\)")
