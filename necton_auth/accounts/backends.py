from .models import User


class EmailBackend:
    """USER 테이블의 이메일과 해시 비밀번호로 인증한다."""

    def authenticate(self, request, email=None, password=None, **kwargs):
        if not email or not password:
            return None

        try:
            user = User.objects.get(email__iexact=email.strip())
        except User.DoesNotExist:
            return None

        if user.status != User.STATUS_ACTIVE:
            return None

        if user.check_password(password):
            return user

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(
                pk=user_id,
                status=User.STATUS_ACTIVE,
            )
        except User.DoesNotExist:
            return None
