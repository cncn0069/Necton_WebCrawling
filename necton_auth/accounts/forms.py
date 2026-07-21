from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User


class LoginForm(forms.Form):
    email = forms.EmailField(label="이메일", max_length=255)
    password = forms.CharField(
        label="비밀번호",
        strip=False,
        widget=forms.PasswordInput,
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        password = cleaned_data.get("password")

        if email and password:
            self.user_cache = authenticate(
                self.request,
                email=email,
                password=password,
            )
            if self.user_cache is None:
                raise ValidationError(
                    "이메일 또는 비밀번호가 올바르지 않습니다."
                )

        return cleaned_data

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None

    def get_user(self):
        return self.user_cache


class SignUpForm(forms.ModelForm):
    password = forms.CharField(
        label="비밀번호",
        strip=False,
        widget=forms.PasswordInput,
    )
    password_confirm = forms.CharField(
        label="비밀번호 확인",
        strip=False,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ("email", "password", "nickname", "phone", "company")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("이미 가입된 이메일입니다.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")

        if password and password_confirm and password != password_confirm:
            self.add_error(
                "password_confirm",
                "비밀번호가 일치하지 않습니다.",
            )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user
