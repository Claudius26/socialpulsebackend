from django.urls import path
from . import views

# Dashboard endpoints (authenticated with the app JWT) mounted at /api/developer/
urlpatterns = [
    path("keys/", views.api_keys, name="api_keys"),
    path("keys/<int:pk>/revoke/", views.revoke_api_key, name="revoke_api_key"),
    path("credit/", views.api_credit, name="api_credit"),
    path("credit/topup/", views.topup_api_credit_view, name="topup_api_credit"),
]
