from django.urls import path
from .views import RegisterManualView, LoginView, RegisterGoogleView, UserDashboardView, UpdateUserProfileView,LoginWithGoogleView

urlpatterns = [
    path('register/manual/', RegisterManualView.as_view(), name='register_manual'),
    path('register/google/', RegisterGoogleView.as_view(), name='register_google'),
    path('login/', LoginView.as_view(), name='login'),
    path('login/google/', LoginWithGoogleView.as_view(), name='login_google'),
    path('user/dashboard/', UserDashboardView.as_view(), name='user_dashboard'),
    path('user/update/', UpdateUserProfileView.as_view(), name='user_update'),
]
