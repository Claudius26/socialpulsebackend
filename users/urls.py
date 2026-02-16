from django.urls import path
from .views import RegisterManualView, LoginView, MeView, UpdateUserProfileView

urlpatterns = [
    path("register/", RegisterManualView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
    path("update_profile/", UpdateUserProfileView.as_view(), name="update-profile"),
]
