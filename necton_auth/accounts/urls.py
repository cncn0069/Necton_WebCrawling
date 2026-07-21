from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("account/", views.account_page, name="account"),
    path("logout/", views.logout_user, name="logout"),
    path("signup/", views.signup_page, name="signup"),
]
