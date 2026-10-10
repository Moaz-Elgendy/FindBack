"""Cookie policy for the anonymous acquisition that feeds shared results."""
from http.cookiejar import DefaultCookiePolicy


class NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False
