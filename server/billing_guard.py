# server/billing_guard.py
from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from djstripe.models import Subscription, Customer

def user_has_active_subscription(user) -> bool:
    if not user.is_authenticated:
        return False
    Customer.get_or_create(subscriber=user)
    return Subscription.objects.filter(
        customer__subscriber=user,
        status__in=["active", "trialing"],
        current_period_end__gt=timezone.now(),
    ).exists()

def require_active_subscription(view_fn):
    @wraps(view_fn)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?msg=Please log in to continue.")
        if not user_has_active_subscription(request.user):
            return redirect(f"{reverse('home')}?msg=Subscribe to use the summarizer.")
        return view_fn(request, *args, **kwargs)
    return _wrapped
