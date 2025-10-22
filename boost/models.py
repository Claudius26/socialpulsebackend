from django.db import models
from django.conf import settings

class BoostRequest(models.Model):
    PLATFORM_CHOICES = [
        ("Instagram", "Instagram"),
        ("Facebook", "Facebook"),
        ("TikTok", "TikTok"),
        ("Twitter", "Twitter"),
        ("YouTube", "YouTube"),
        ("Telegram", "Telegram"),
        ("Spotify", "Spotify"),
        ("Website Traffic", "Website Traffic"),
    ]

    STATUS_CHOICES = [
        ("Pending", "Pending"),
        ("Processing", "Processing"),
        ("Completed", "Completed"),
        ("Partial", "Partial"),
        ("Failed", "Failed"),
    ]

    TRAFFIC_SOURCES = [
        ("Facebook", "Facebook"),
        ("Instagram", "Instagram"),
        ("LinkedIn", "LinkedIn"),
        ("YouTube", "YouTube"),
        ("Twitter", "Twitter"),
        ("Wikipedia", "Wikipedia"),
        ("Bing", "Bing"),
        ("Yahoo", "Yahoo"),
        ("Fiverr", "Fiverr"),
        ("VK", "VK.com"),
        ("Ebay", "Ebay.com.au"),
        ("Abc", "Abc.net.au"),
        ("News", "News.com.au"),
        ("Gumtree", "Gumtree.com.au"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    service = models.CharField(max_length=100)
    target = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    audience = models.CharField(max_length=50)
    quality = models.CharField(max_length=20, default="Low")
    traffic_source = models.CharField(max_length=50, choices=TRAFFIC_SOURCES, blank=True, null=True)
    
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_time = models.CharField(max_length=100, blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    error_message = models.TextField(blank=True, null=True)

    smm_order_id = models.CharField(max_length=100, blank=True, null=True)
    smm_charge = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    smm_start_count = models.CharField(max_length=50, blank=True, null=True)
    smm_remains = models.CharField(max_length=50, blank=True, null=True)
    smm_currency = models.CharField(max_length=10, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - {self.platform} ({self.service})"
