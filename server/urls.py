# server/urls.py
from django.urls import path, include
from django.views.generic.base import RedirectView
from django.conf import settings
from . import views, billing_views
from djstripe.views import ProcessWebhookView

from django.contrib import admin


urlpatterns = [

    path("admin/", admin.site.urls),

    # pages
    path("", RedirectView.as_view(url="/home"), name="home"),
    path("home", views.home, name="home"),
    path("about", views.about, name="about"),
    path("contact", views.contact, name="contact"),
    path("output", views.output, name="output"),
    path("login", views.login_page, name="login"),
    path("new", views.new_account, name="new"),

    # billing (your views)
    path("billing/checkout", billing_views.create_checkout_session, name="billing_checkout"),
    path("billing/portal",   billing_views.create_portal_session,   name="billing_portal"),
    path("billing/success",  billing_views.billing_success,         name="billing_success"),
    path("billing/cancel",   billing_views.billing_cancel,          name="billing_cancel"),

    # dj-stripe routes
    path("stripe/", include("djstripe.urls", namespace="djstripe")),  # keeps the admin + UUID route


    # utility
    path("summarize",    views.summarize, name="summarize"),
    path("chat",         views.chat_html, name="chat"),
    path("ask",          views.ask, name="ask"),
    path("out/verify",   views.verify, name="verify"),
    path("clear",        views.clear, name="clear"),

    # downloadable
    path("out",        RedirectView.as_view(url="/out/pdf"), name="out_pdf"),
    path("out/pdf",    views.out, name="out_pdf"),
    path("out/docx",   views.out_docx, name="out_docx"),
    path("transcript", views.transcript, name="transcript"),

    # auth
    path("create", views.create_account, name="create"),
    path("auth",   views.auth, name="auth"),
    path("logout", views.logout_user, name="logout"),
    path("delete", views.delete_account, name="delete"),
] + ([
    # dev-only
    path("session",  views.session, name="session"),
    path("cyclekey", views.cyclekey, name="cyclekey"),
] if settings.DEBUG else [])
