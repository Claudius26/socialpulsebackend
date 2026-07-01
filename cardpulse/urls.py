from django.urls import path

from .views import (
    CardPulseRegisterView,
    CardPulseLoginView,
    CardPulseMeView,
    TagCheckView,
    SetTagView,
    SetPhoneView,
    SetAvatarView,
    GetAvatarView,
    SetTransactionPinView,
    ChangeTransactionPinView,
    VerifyEmailView,
    ResendOTPView,
    ChangePasswordView,
)

app_name = "cardpulse"

urlpatterns = [
    path("auth/register/", CardPulseRegisterView.as_view(), name="register"),
    path("auth/login/", CardPulseLoginView.as_view(), name="login"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/resend-otp/", ResendOTPView.as_view(), name="resend-otp"),
    path("me/", CardPulseMeView.as_view(), name="me"),
    path("username/check/", TagCheckView.as_view(), name="tag-check"),
    path("username/", SetTagView.as_view(), name="set-tag"),
    path("phone/", SetPhoneView.as_view(), name="set-phone"),
    path("avatar/", SetAvatarView.as_view(), name="set-avatar"),
    path("avatar/me/", GetAvatarView.as_view(), name="get-avatar"),
    path("password/change/", ChangePasswordView.as_view(), name="change-password"),
    path("pin/set/", SetTransactionPinView.as_view(), name="set-pin"),
    path("pin/change/", ChangeTransactionPinView.as_view(), name="change-pin"),
]
