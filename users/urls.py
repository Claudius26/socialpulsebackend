from django.urls import path
from .views import RegisterManualView, LoginView, MeView, UpdateUserProfileView

urlpatterns = [
    path("register/", RegisterManualView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", MeView.as_view(), name="me"),
    path("update_profile/", UpdateUserProfileView.as_view(), name="update-profile"),
]

from django.urls import path
from .views import admin_login,admin_profile,admin_update_profile,admin_change_password,cache_test

urlpatterns += [
    path("admin/login/", admin_login, name="admin_login"),
    path("admin/profile/", admin_profile, name="admin_profile"),
    path("admin/profile/update/", admin_update_profile, name="admin_update_profile"),
    path("admin/profile/change-password/", admin_change_password, name="admin_change_password"),
    path("cache-test/", cache_test),
]
