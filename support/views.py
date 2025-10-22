from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.mail import send_mail
from django.conf import settings
from .models import SupportMessage
from .serializers import SupportMessageSerializer

class SupportMessageListCreateView(generics.ListCreateAPIView):
    serializer_class = SupportMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return SupportMessage.objects.filter(user=self.request.user).order_by("created_at")

    def perform_create(self, serializer):
        message = serializer.save(user=self.request.user, sender="user")

        send_mail(
            subject=f"New Support Message from {self.request.user.email}",
            message=message.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.SUPPORT_EMAIL],
            fail_silently=True,
        )

class AdminReplyView(generics.CreateAPIView):
    serializer_class = SupportMessageSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user_id = request.data.get("userId")
        message_text = request.data.get("message")

        if not user_id or not message_text:
            return Response({"error": "Invalid request"}, status=status.HTTP_400_BAD_REQUEST)

        message = SupportMessage.objects.create(
            user_id=user_id,
            sender="admin",
            message=message_text,
        )

        send_mail(
            subject="Support Reply",
            message=message_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[message.user.email],
            fail_silently=True,
        )

        return Response(SupportMessageSerializer(message).data, status=status.HTTP_201_CREATED)
