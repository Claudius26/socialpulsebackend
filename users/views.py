from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate, get_user_model
from .serializers import RegisterSerializer, UserSerializer
from .models import Wallet
from countryinfo import CountryInfo
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


def get_currency_from_country(country_name):
    try:
        if not country_name:
            return "USD"
        country = CountryInfo(country_name)
        currencies = country.currencies()
        if currencies and len(currencies) > 0:
            return currencies[0]
    except Exception:
        pass
    return "USD"


class RegisterManualView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        if "fullName" in data:
            data["full_name"] = data.pop("fullName")

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        country_name = serializer.validated_data.get("country")
        currency = get_currency_from_country(country_name)

        
        Wallet.objects.create(user=user, currency=currency)

        refresh = RefreshToken.for_user(user)
        wallet = user.wallet

        response_data = {
            "user": {
                **UserSerializer(user).data,
                "wallet": {
                    "balance": wallet.balance,
                    "currency": wallet.currency,
                },
                "country": country_name,
            },
            "token": str(refresh.access_token),
        }

        return Response(response_data, status=status.HTTP_201_CREATED)

class RegisterGoogleView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email")
        full_name = request.data.get("fullName")
        google_id = request.data.get("google_id")
        country_name = request.data.get("country")

        if not email or not google_id:
            return Response({"error": "Missing Google account details."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response(
                {"error": "A user with this email already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = User.objects.create(
            email=email,
            full_name=full_name,
            username=email.split("@")[0],
        )

        currency = get_currency_from_country(country_name)

        Wallet.objects.create(user=user, currency=currency)

        refresh = RefreshToken.for_user(user)
        wallet = user.wallet

        response_data = {
            "user": {
                **UserSerializer(user).data,
                "wallet": {
                    "balance": wallet.balance,
                    "currency": wallet.currency,
                },
                "country": country_name,
            },
            "token": str(refresh.access_token),
        }

        return Response(response_data, status=status.HTTP_201_CREATED)

    
class LoginWithGoogleView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get("email")
        google_id = request.data.get("google_id")

        if not email or not google_id:
            return Response(
                {"error": "Email and Google ID are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = User.objects.filter(email=email).first()
        if not user:
            return Response(
                {"error": "No account found for this Google user. Please register first."},
                status=status.HTTP_404_NOT_FOUND
            )

        refresh = RefreshToken.for_user(user)
        wallet = getattr(user, "wallet", None)

        return Response({
            "message": "Login successful",
            "user": {
                **UserSerializer(user).data,
                "wallet": {
                    "balance": wallet.balance if wallet else 0,
                    "currency": wallet.currency if wallet else "USD",
                },
            },
            "token": str(refresh.access_token),
        }, status=status.HTTP_200_OK)

class LoginView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            email = request.data.get("email")
            password = request.data.get("password")

            if not email or not password:
                return Response(
                    {"error": "Email and password are required."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            user = authenticate(request, email=email, password=password)
            if not user:
                return Response(
                    {"error": "Invalid credentials."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            refresh = RefreshToken.for_user(user)

            wallet_data = {}
            try:
                wallet = user.wallet
                wallet_data = {
                    "balance": wallet.balance,
                    "currency": wallet.currency,
                }
            except Exception as wallet_error:
                logger.warning(f"Wallet not found for user {user.email}: {wallet_error}")
                wallet_data = {
                    "balance": 0,
                    "currency": "N/A",
                }

            return Response({
                "user": {
                    **UserSerializer(user).data,
                    "wallet": wallet_data,
                },
                "token": str(refresh.access_token),
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Login error: {str(e)}", exc_info=True)
            return Response(
                {"error": "An internal error occurred during login. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UpdateUserProfileView(generics.UpdateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user

    def put(self, request, *args, **kwargs):
        user = self.get_object()
        data = request.data.copy()

        old_password = data.get("oldPassword")
        new_password = data.get("newPassword")

        serializer = self.get_serializer(user, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        if new_password:
            if not old_password:
                return Response(
                    {"error": "Old password is required to change password."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not user.check_password(old_password):
                return Response(
                    {"error": "Old password is incorrect."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.set_password(new_password)
            user.save()

        return Response(
            {"message": "Profile updated successfully.", "user": serializer.data},
            status=status.HTTP_200_OK,
        )


class UserDashboardView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request, *args, **kwargs):
        user = request.user
        wallet = getattr(user, "wallet", None)
        country_name = user.country
        currency = get_currency_from_country(country_name)

        if wallet and wallet.currency != currency:
            wallet.currency = currency
            wallet.save()

        return Response({
            "user": {
                **UserSerializer(user).data,
                "wallet": {
                    "balance": wallet.balance if wallet else 0,
                    "currency": wallet.currency if wallet else currency,
                },
                "country": country_name,
            }
        })
