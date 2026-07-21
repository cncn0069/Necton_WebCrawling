from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import SignUpForm


@require_http_methods(["GET", "POST"])
def login_page(request):
    return render(request, "accounts/login.html")


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
