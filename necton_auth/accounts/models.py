from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils.crypto import salted_hmac


class User(models.Model):
    """회원 테이블 스키마에 대응하는 사용자 모델."""

    ROLE_USER = "USER"
    STATUS_ACTIVE = "A"

    user_id = models.AutoField(primary_key=True)
    password = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    nickname = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=255, blank=True, null=True)
    role = models.CharField(max_length=11, default=ROLE_USER)
    company = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=1, default=STATUS_ACTIVE)
    created_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "USER"

    def __str__(self):
        return self.email

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_session_auth_hash(self):
        return self._get_session_auth_hash()

    def get_session_auth_fallback_hash(self):
        for fallback_secret in settings.SECRET_KEY_FALLBACKS:
            yield self._get_session_auth_hash(secret=fallback_secret)

    def _get_session_auth_hash(self, secret=None):
        return salted_hmac(
            "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash",
            self.password,
            secret=secret,
            algorithm="sha256",
        ).hexdigest()
