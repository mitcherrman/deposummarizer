"""
URL configuration for server project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.views.generic.base import RedirectView
from . import views

urlpatterns = [
    path('admin', admin.site.urls),
    #pages
    path('', RedirectView.as_view(url='/home'), name='home'),
    path("home", views.home, name="home"),
    path("about", views.about, name="about"),
    path("contact", views.contact, name="contact"),
    path('output', views.output, name='output'),
    path('login', views.login_page, name='login'),
    path('new', views.new_account, name='new'),
    #utility
    path('summarize', views.summarize, name='summarize'),
    path('chat', views.chat_html, name='chat'),
    path('ask', views.ask, name='ask'),
    path('out/verify', views.verify, name='verify'),
    #downloadable
    path('out', RedirectView.as_view(url='/out/pdf'), name='out_pdf'),
    path('out/pdf', views.out, name='out_pdf'),
    path('out/docx', views.out_docx, name='out_docx'),
    path('transcript', views.transcript, name='transcript'),
    #authentication
    path('create', views.create_account, name='create'),
    path('auth', views.auth, name='auth'),
    path('logout', views.logout_user, name='logout'),
    path('delete', views.delete_account, name='delete'),
    #dev views
    path('clear', views.clear, name='clear'),
    path('session', views.session, name='session'),
    path('cyclekey', views.cyclekey, name='cyclekey')
]
