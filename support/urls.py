from django.urls import path
from .views import SupportMessageListCreateView, AdminReplyView

urlpatterns = [
    path("", SupportMessageListCreateView.as_view(), name="support_messages"),
    path("admin-reply/", AdminReplyView.as_view(), name="support_admin_reply"),
]
