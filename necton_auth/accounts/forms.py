from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import User


class SignUpForm(forms.ModelForm):
    password = forms.CharField(
        label="비밀번호",
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

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user
