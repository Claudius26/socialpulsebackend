from rest_framework.permissions import BasePermission


class IsCardPulseUser(BasePermission):
    """Allow only authenticated users in the CardPulse realm.

    Keeps SocialPulse (web) accounts out of CardPulse endpoints even if they
    present a valid token — the two user bases stay isolated.
    """
    message = "This endpoint is only available to CardPulse accounts."

    def has_permission(self, request, view):
        user = request.user
        # Staff/admins are allowed everywhere (one admin works across both apps).
        return bool(user and user.is_authenticated
                    and (getattr(user, "app", None) == "cardpulse" or user.is_staff))


class IsVerifiedCardPulseUser(BasePermission):
    """CardPulse user who has verified their email — required for money actions."""
    message = "Verify your email to use this feature."

    def has_permission(self, request, view):
        user = request.user
        if user and user.is_authenticated and user.is_staff:
            return True  # admins bypass the realm + verification gate
        return bool(
            user and user.is_authenticated
            and getattr(user, "app", None) == "cardpulse"
            and getattr(user, "email_verified", False)
        )
