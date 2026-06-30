from django.urls import path, include

urlpatterns = [
    path('api/', include('users.urls')),
    path("api/deposit/", include("payments.urls")),
    path("api/support/", include("support.urls")),
    path("api/boost/", include("boost.urls")),
   path("api/virtualnumbers/", include("virtualnumbers.urls")),
    path("api/developer/", include("developer.urls")),
    path("api/v1/", include("developer.urls_v1")),
    path("api/v1/cardpulse/", include("cardpulse.urls")),
    path("api/v1/cardpulse/giftcards/", include("giftcards.urls")),
    path("api/v1/cardpulse/p2p/", include("p2p.urls")),
    path("api/v1/cardpulse/wallet/", include("banking.urls")),
    path("api/v1/cardpulse/admin/", include("cardpulse.admin_urls")),
    # CardPulse reuses the existing virtual-number flow under its own namespace
    # (same views; they charge the caller's cash wallet).
    path("api/v1/cardpulse/numbers/",
         include(("virtualnumbers.urls", "cardpulse_numbers"), namespace="cardpulse_numbers")),


]
