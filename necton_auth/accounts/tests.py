from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from .models import User


class LoginPageTests(TestCase):
    password = "S3cure!Passphrase-7746"

    def create_user(self, **overrides):
        user = User(
            email=overrides.get("email", "admin@example.com"),
            status=overrides.get("status", User.STATUS_ACTIVE),
        )
        user.set_password(self.password)
        user.save()
        return user

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

    def test_login_rejects_wrong_credentials_without_revealing_the_cause(self):
        self.create_user()

        response = self.client.post(
            reverse("accounts:login"),
            {"email": "admin@example.com", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "이메일 또는 비밀번호가 올바르지 않습니다.",
        )
        self.assertContains(response, 'value="admin@example.com"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_accepts_email_case_insensitively_and_keeps_session(self):
        user = self.create_user(email="Admin@Example.com")

        response = self.client.post(
            reverse("accounts:login"),
            {"email": "ADMIN@example.COM", "password": self.password},
            follow=True,
        )

        self.assertRedirects(response, reverse("accounts:login"))
        self.assertContains(response, "로그인되었습니다.")
        self.assertContains(response, user.email)
        self.assertContains(response, reverse("accounts:logout"))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

        next_response = self.client.get(reverse("accounts:login"))
        self.assertContains(next_response, user.email)
        self.assertContains(next_response, "로그아웃")

    def test_login_rejects_inactive_user(self):
        self.create_user(status="I")

        response = self.client.post(
            reverse("accounts:login"),
            {"email": "admin@example.com", "password": self.password},
        )

        self.assertContains(
            response,
            "이메일 또는 비밀번호가 올바르지 않습니다.",
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_clears_login_session(self):
        self.create_user()
        self.client.post(
            reverse("accounts:login"),
            {"email": "admin@example.com", "password": self.password},
        )

        response = self.client.post(reverse("accounts:logout"), follow=True)

        self.assertRedirects(response, reverse("accounts:login"))
        self.assertContains(response, "로그아웃되었습니다.")
        self.assertContains(response, 'name="email"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_stylesheet_is_discoverable(self):
        self.assertIsNotNone(finders.find("accounts/css/login.css"))


class SignUpPageTests(TestCase):
    valid_signup_data = {
        "email": "new-admin@example.com",
        "password": "S3cure!Passphrase-7746",
        "password_confirm": "S3cure!Passphrase-7746",
        "nickname": "관리자",
        "phone": "01012345678",
        "company": "Necton",
    }

    def test_signup_page_uses_django_template_and_separated_css(self):
        response = self.client.get(reverse("accounts:signup"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="password_confirm"')
        self.assertContains(response, 'name="nickname"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, 'placeholder="01012345678"')
        self.assertContains(response, 'pattern="[0-9]{11}"')
        self.assertContains(response, 'minlength="11"')
        self.assertContains(response, 'maxlength="11"')
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

    def test_signup_accepts_simple_password_when_it_has_eight_characters(self):
        signup_data = self.valid_signup_data | {
            "password": "password",
            "password_confirm": "password",
        }

        response = self.client.post(
            reverse("accounts:signup"),
            signup_data,
        )

        self.assertRedirects(response, reverse("accounts:login"))
        user = User.objects.get(email=self.valid_signup_data["email"])
        self.assertTrue(user.check_password("password"))

    def test_signup_rejects_password_shorter_than_eight_characters(self):
        signup_data = self.valid_signup_data | {
            "password": "1234567",
            "password_confirm": "1234567",
        }

        response = self.client.post(reverse("accounts:signup"), signup_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "비밀번호는 8자 이상 입력해 주세요.")
        self.assertFalse(User.objects.exists())

    def test_signup_rejects_phone_unless_it_is_exactly_eleven_digits(self):
        invalid_phone_numbers = (
            "010-1234-5678",
            "0101234567",
            "010123456789",
            "0101234abcd",
        )

        for phone in invalid_phone_numbers:
            with self.subTest(phone=phone):
                response = self.client.post(
                    reverse("accounts:signup"),
                    self.valid_signup_data | {"phone": phone},
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "핸드폰 번호는 숫자 11자리로 입력해 주세요.",
                )
                self.assertFalse(User.objects.exists())

    def test_signup_allows_phone_to_be_empty(self):
        response = self.client.post(
            reverse("accounts:signup"),
            self.valid_signup_data | {"phone": ""},
        )

        self.assertRedirects(response, reverse("accounts:login"))
        self.assertIsNone(User.objects.get().phone)

    def test_signup_rejects_mismatched_password_confirmation(self):
        signup_data = self.valid_signup_data | {
            "password_confirm": "Different!Passphrase-7746",
        }

        response = self.client.post(reverse("accounts:signup"), signup_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "비밀번호가 일치하지 않습니다.")
        self.assertContains(response, 'name="password_confirm"')
        self.assertFalse(User.objects.exists())

    def test_login_page_links_to_signup_page(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, reverse("accounts:signup"))

    def test_signup_stylesheet_is_discoverable(self):
        self.assertIsNotNone(finders.find("accounts/css/signup.css"))
