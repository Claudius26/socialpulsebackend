from django.urls import path
from . import views

# Dashboard endpoints (authenticated with the app JWT) mounted at /api/developer/
urlpatterns = [
    path("key/", views.api_key, name="api_key"),
    path("credit/", views.api_credit, name="api_credit"),
    path("credit/topup/", views.topup_api_credit_view, name="topup_api_credit"),
    path("credit/withdraw/", views.withdraw_api_credit_view, name="withdraw_api_credit"),
]
