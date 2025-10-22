from django.db import models
from django.conf import settings

class VirtualNumber(models.Model):
    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Active", "Active"),
        ("Expired", "Expired"),
        ("Failed", "Failed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="virtual_numbers")
    country = models.CharField(max_length=100)
    service = models.CharField(max_length=100)  
    phone_number = models.CharField(max_length=50)
    activation_id = models.CharField(max_length=100)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.phone_number} ({self.service})"

class ReceivedSMS(models.Model):
    virtual_number = models.ForeignKey(VirtualNumber, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField()
    received_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"SMS for {self.virtual_number.phone_number}"
