from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse


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
