from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    logout,
)
from django.contrib import messages
from django.middleware.csrf import rotate_token
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, SignUpForm


def start_login_session(request, user):
    """last_login 컬럼이 없는 기존 USER 스키마용 로그인 처리."""
    request.session.flush()
    request.session[SESSION_KEY] = str(user.pk)
    request.session[BACKEND_SESSION_KEY] = user.backend
    request.session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    request.user = user
    rotate_token(request)


@require_http_methods(["GET", "POST"])
def login_page(request):
    form = LoginForm(request.POST or None, request=request)

    if request.method == "POST" and form.is_valid():
        start_login_session(request, form.get_user())
        messages.success(request, "로그인되었습니다.")
        return redirect("accounts:login")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["POST"])
def logout_user(request):
    logout(request)
    messages.success(request, "로그아웃되었습니다.")
    return redirect("accounts:login")


@require_http_methods(["GET", "POST"])
def signup_page(request):
    form = SignUpForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("email", "이미 가입된 이메일입니다.")
        else:
            messages.success(request, "회원가입이 완료되었습니다. 로그인해 주세요.")
            return redirect("accounts:login")

    return render(request, "accounts/signup.html", {"form": form})
