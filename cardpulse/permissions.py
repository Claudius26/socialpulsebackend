from rest_framework.permissions import BasePermission


def _is_admin(user) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


class IsCardPulseUser(BasePermission):
    """Allow only authenticated CardPulse customers.

    SocialPulse (web) accounts and ADMIN/staff accounts are kept out of the app
    entirely — admins manage everything from the web dashboard, never the app.
    (An admin who wants to use the app must register a separate user account.)
    """
    message = "This endpoint is only available to CardPulse accounts."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated
            and getattr(user, "app", None) == "cardpulse"
            and not _is_admin(user)
        )


class IsVerifiedCardPulseUser(BasePermission):
    """CardPulse customer who has verified their email — required for money actions."""
    message = "Verify your email to use this feature."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated
            and getattr(user, "app", None) == "cardpulse"
            and not _is_admin(user)
            and getattr(user, "email_verified", False)
        )
