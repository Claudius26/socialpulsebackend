from django.urls import path
from . import views

# Public developer API (authenticated with an API key) mounted at /api/v1/
urlpatterns = [
    path("numbers/", views.v1_list_numbers, name="v1_list_numbers"),
    path("numbers/purchase/", views.v1_purchase, name="v1_purchase"),
    path("numbers/<str:activation_id>/sms/", views.v1_get_sms, name="v1_get_sms"),
    path("numbers/<str:activation_id>/cancel/", views.v1_cancel, name="v1_cancel"),
    path("balance/", views.v1_balance, name="v1_balance"),
]
