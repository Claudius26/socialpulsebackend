from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class SupportMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="support_messages")
    sender = models.CharField(max_length=10, choices=[("user", "User"), ("admin", "Admin")])
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.sender} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
