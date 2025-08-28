# server/billing_views.py
import stripe
from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from djstripe.models import Customer

def _abs(request, name):
    return request.build_absolute_uri(reverse(name))

@login_required
def create_checkout_session(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer, _ = Customer.get_or_create(subscriber=request.user)

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
        success_url=_abs(request, "billing_success") + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=_abs(request, "billing_cancel"),
        customer=customer.id if customer and customer.id else None,
        customer_creation="always" if not (customer and customer.id) else "if_required",
        allow_promotion_codes=True,
    )
    return redirect(session.url)

@login_required
def create_portal_session(request):
    stripe.api_key = settings.STRIPE_SECRET_KEY
    customer, _ = Customer.get_or_create(subscriber=request.user)
    portal = stripe.billing_portal.Session.create(
        customer=customer.id,
        return_url=_abs(request, "home"),
    )
    return redirect(portal.url)

def billing_success(request):
    return render(request, "billing_success.html")   # <-- matches your files

def billing_cancel(request):
    return render(request, "billing_cancel.html")    # <-- matches your files
