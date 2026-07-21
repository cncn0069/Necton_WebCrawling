from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
)
from django.contrib import messages
from django.middleware.csrf import rotate_token
from django.db import IntegrityError, OperationalError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, SignUpForm

AUTH_EMAIL_SESSION_KEY = "account_email"


def start_login_session(request, user):
    """last_login 컬럼이 없는 기존 USER 스키마용 로그인 처리."""
    request.session.flush()
    request.session[SESSION_KEY] = str(user.pk)
    request.session[BACKEND_SESSION_KEY] = user.backend
    request.session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    request.session[AUTH_EMAIL_SESSION_KEY] = user.email
    request.user = user
    rotate_token(request)


@require_http_methods(["GET", "POST"])
def login_page(request):
    if (
        request.method == "GET"
        and request.session.get(SESSION_KEY)
        and request.session.get(AUTH_EMAIL_SESSION_KEY)
    ):
        return redirect("accounts:account")

    form = LoginForm(request.POST or None, request=request)

    if request.method == "POST":
        try:
            is_valid = form.is_valid()
        except OperationalError:
            form.add_error(
                None,
                "데이터베이스 연결에 문제가 있습니다. 잠시 후 다시 시도해 주세요.",
            )
            return render(
                request,
                "accounts/login.html",
                {"form": form},
                status=503,
            )

        if is_valid:
            start_login_session(request, form.get_user())
            messages.success(request, "로그인되었습니다.")
            return redirect("accounts:account")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["GET"])
def account_page(request):
    email = request.session.get(AUTH_EMAIL_SESSION_KEY)
    if not request.session.get(SESSION_KEY) or not email:
        return redirect("accounts:login")

    return render(
        request,
        "accounts/login.html",
        {"signed_in_email": email},
    )


@require_http_methods(["POST"])
def logout_user(request):
    request.session.flush()
    rotate_token(request)
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
