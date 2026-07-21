from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import User


class LoginPageTests(SimpleTestCase):
    def test_root_redirects_to_login_page(self):
        response = self.client.get("/")

        self.assertRedirects(response, reverse("accounts:login"))

    def test_login_page_uses_clean_django_template(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, "accounts/css/login.css")
        self.assertNotContains(response, "__bundler")
        self.assertNotContains(response, "<x-dc")

    def test_login_post_explains_that_authentication_is_not_connected(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"email": "admin@example.com", "password": "password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "로그인 기능은 데이터베이스 연결 후 사용할 수 있습니다.",
        )
        self.assertContains(response, 'value="admin@example.com"')

    def test_login_stylesheet_is_discoverable(self):
        self.assertIsNotNone(finders.find("accounts/css/login.css"))


class SignUpPageTests(TestCase):
    valid_signup_data = {
        "email": "new-admin@example.com",
        "password": "S3cure!Passphrase-7746",
        "nickname": "관리자",
        "phone": "010-1234-5678",
        "company": "Necton",
    }

    def test_signup_page_uses_django_template_and_separated_css(self):
        response = self.client.get(reverse("accounts:signup"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="nickname"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'name="company"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, "accounts/css/signup.css")
        self.assertNotContains(response, "support.js")
        self.assertNotContains(response, "<x-dc")

    def test_signup_creates_user_with_hashed_password_and_schema_defaults(self):
        response = self.client.post(
            reverse("accounts:signup"),
            self.valid_signup_data,
            follow=True,
        )

        self.assertRedirects(response, reverse("accounts:login"))
        self.assertContains(response, "회원가입이 완료되었습니다. 로그인해 주세요.")

        user = User.objects.get(email="new-admin@example.com")
        self.assertNotEqual(user.password, self.valid_signup_data["password"])
        self.assertTrue(user.check_password(self.valid_signup_data["password"]))
        self.assertEqual(user.role, User.ROLE_USER)
        self.assertEqual(user.status, User.STATUS_ACTIVE)
        self.assertIsNotNone(user.created_date)
        self.assertIsNotNone(user.update_date)

    def test_signup_rejects_duplicate_email_case_insensitively(self):
        existing_user = User(email="New-Admin@Example.com")
        existing_user.set_password("S3cure!ExistingPassphrase-7746")
        existing_user.save()

        response = self.client.post(
            reverse("accounts:signup"),
            self.valid_signup_data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이미 가입된 이메일입니다.")
        self.assertEqual(User.objects.count(), 1)

    def test_signup_rejects_blank_password(self):
        signup_data = self.valid_signup_data | {"password": ""}

        response = self.client.post(reverse("accounts:signup"), signup_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertFalse(User.objects.exists())

    def test_login_page_links_to_signup_page(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, reverse("accounts:signup"))

    def test_signup_stylesheet_is_discoverable(self):
        self.assertIsNotNone(finders.find("accounts/css/signup.css"))
