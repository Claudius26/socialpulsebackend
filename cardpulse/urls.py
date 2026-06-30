from django.urls import path

from .views import (
    CardPulseRegisterView,
    CardPulseLoginView,
    CardPulseMeView,
    TagCheckView,
    SetTagView,
    SetTransactionPinView,
    ChangeTransactionPinView,
)

app_name = "cardpulse"

urlpatterns = [
    path("auth/register/", CardPulseRegisterView.as_view(), name="register"),
    path("auth/login/", CardPulseLoginView.as_view(), name="login"),
    path("me/", CardPulseMeView.as_view(), name="me"),
    path("tag/check/", TagCheckView.as_view(), name="tag-check"),
    path("tag/", SetTagView.as_view(), name="set-tag"),
    path("pin/set/", SetTransactionPinView.as_view(), name="set-pin"),
    path("pin/change/", ChangeTransactionPinView.as_view(), name="change-pin"),
]
