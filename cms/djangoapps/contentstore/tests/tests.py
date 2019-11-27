"""
This test file will test registration, login, activation, and session activity timeouts
"""
from __future__ import absolute_import, print_function

import datetime
import time

import mock
from contentstore.tests.test_course_settings import CourseTestCase
from contentstore.tests.utils import AjaxEnabledTestClient, parse_json, registration, user
from ddt import data, ddt, unpack
from django.conf import settings
from django.core.cache import cache
from django.test.utils import override_settings
from django.urls import reverse
from pytz import UTC
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class ContentStoreTestCase(ModuleStoreTestCase):
    """Test class to verify user account operations"""

    def _login(self, email, password):
        """
        Login.  View should always return 200.  The success/fail is in the
        returned json
        """
        resp = self.client.post(
            reverse('user_api_login_session'),
            {'email': email, 'password': password}
        )
        return resp

    def login(self, email, password):
        """Login, check that it worked."""
        resp = self._login(email, password)
        self.assertEqual(resp.status_code, 200)
        return resp

    def _create_account(self, username, email, password):
        """Try to create an account.  No error checking"""
        registration_url = reverse('user_api_registration')
        resp = self.client.post(registration_url, {
            'username': username,
            'email': email,
            'password': password,
            'location': 'home',
            'language': 'Franglish',
            'name': 'Fred Weasley',
            'terms_of_service': 'true',
            'honor_code': 'true',
        })
        return resp

    def create_account(self, username, email, password):
        """Create the account and check that it worked"""
        resp = self._create_account(username, email, password)
        self.assertEqual(resp.status_code, 200)
        json_data = parse_json(resp)
        self.assertEqual(json_data['success'], True)

        # Check both that the user is created, and inactive
        self.assertFalse(user(email).is_active)

        return resp

    def _activate_user(self, email):
        """Look up the activation key for the user, then hit the activate view.
        No error checking"""
        activation_key = registration(email).activation_key

        # and now we try to activate
        resp = self.client.get(reverse('activate', kwargs={'key': activation_key}))
        return resp

    def activate_user(self, email):
        resp = self._activate_user(email)
        self.assertEqual(resp.status_code, 200)
        # Now make sure that the user is now actually activated
        self.assertTrue(user(email).is_active)


@ddt
class AuthTestCase(ContentStoreTestCase):
    """Check that various permissions-related things work"""

    CREATE_USER = False
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super(AuthTestCase, self).setUp()

        self.email = 'a@b.com'
        self.pw = 'xyz'
        self.username = 'testuser'
        self.client = AjaxEnabledTestClient()
        # clear the cache so ratelimiting won't affect these tests
        cache.clear()

    def check_page_get(self, url, expected):
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, expected)
        return resp

    def test_private_pages_auth(self):
        """Make sure pages that do require login work."""
        auth_pages = (
            '/home/',
        )

        # These are pages that should just load when the user is logged in
        # (no data needed)
        simple_auth_pages = (
            '/home/',
        )

        # need an activated user
        self.create_account(self.username, self.email, self.pw)
        self.activate_user(self.email)

        # Create a new session
        self.client = AjaxEnabledTestClient()

        # Not logged in.  Should redirect to login.
        print('Not logged in')
        for page in auth_pages:
            print(u"Checking '{0}'".format(page))
            self.check_page_get(page, expected=302)

        # Logged in should work.
        self.login(self.email, self.pw)

        print('Logged in')
        for page in simple_auth_pages:
            print(u"Checking '{0}'".format(page))
            self.check_page_get(page, expected=200)

    @override_settings(SESSION_INACTIVITY_TIMEOUT_IN_SECONDS=1)
    def test_inactive_session_timeout(self):
        """
        Verify that an inactive session times out and redirects to the
        login page
        """
        self.create_account(self.username, self.email, self.pw)
        self.activate_user(self.email)

        self.login(self.email, self.pw)

        # make sure we can access courseware immediately
        course_url = '/home/'
        resp = self.client.get_html(course_url)
        self.assertEquals(resp.status_code, 200)

        # then wait a bit and see if we get timed out
        time.sleep(2)

        resp = self.client.get_html(course_url)

        # re-request, and we should get a redirect to login page
        self.assertRedirects(resp, settings.LOGIN_URL + '?next=/home/', target_status_code=302)

    @data(
        (True, 'assertContains'),
        (False, 'assertNotContains'))
    @unpack
    def test_signin_and_signup_buttons_index_page(self, allow_account_creation, assertion_method_name):
        """
        Navigate to the home page and check the Sign Up button is hidden when ALLOW_PUBLIC_ACCOUNT_CREATION flag
        is turned off, and not when it is turned on.  The Sign In button should always appear.
        """
        with mock.patch.dict(settings.FEATURES, {"ALLOW_PUBLIC_ACCOUNT_CREATION": allow_account_creation}):
            response = self.client.get(reverse('homepage'))
            assertion_method = getattr(self, assertion_method_name)
            assertion_method(
                response,
                u'<a class="action action-signup" href="{}/register?next=http%3A%2F%2Ftestserver%2F">Sign Up</a>'.format(  # pylint: disable=line-too-long
                    settings.LMS_ROOT_URL
                )
            )
            self.assertContains(
                response,
                u'<a class="action action-signin" href="/signin_redirect_to_lms?next=http%3A%2F%2Ftestserver%2F">Sign In</a>'  # pylint: disable=line-too-long
            )


class ForumTestCase(CourseTestCase):
    """Tests class to verify course to forum operations"""

    def setUp(self):
        """ Creates the test course. """
        super(ForumTestCase, self).setUp()
        self.course = CourseFactory.create(org='testX', number='727', display_name='Forum Course')

    def set_blackout_dates(self, blackout_dates):
        """Helper method to set blackout dates in course."""
        self.course.discussion_blackouts = [
            [start_date.isoformat(), end_date.isoformat()] for start_date, end_date in blackout_dates
        ]

    def test_blackouts(self):
        now = datetime.datetime.now(UTC)
        times1 = [
            (now - datetime.timedelta(days=14), now - datetime.timedelta(days=11)),
            (now + datetime.timedelta(days=24), now + datetime.timedelta(days=30))
        ]
        self.set_blackout_dates(times1)
        self.assertTrue(self.course.forum_posts_allowed)
        times2 = [
            (now - datetime.timedelta(days=14), now + datetime.timedelta(days=2)),
            (now + datetime.timedelta(days=24), now + datetime.timedelta(days=30))
        ]
        self.set_blackout_dates(times2)
        self.assertFalse(self.course.forum_posts_allowed)

        # Single date set for allowed forum posts.
        self.course.discussion_blackouts = [
            now + datetime.timedelta(days=24),
            now + datetime.timedelta(days=30)
        ]
        self.assertTrue(self.course.forum_posts_allowed)

        # Single date set for restricted forum posts.
        self.course.discussion_blackouts = [
            now - datetime.timedelta(days=24),
            now + datetime.timedelta(days=30)
        ]
        self.assertFalse(self.course.forum_posts_allowed)

        # test if user gives empty blackout date it should return true for forum_posts_allowed
        self.course.discussion_blackouts = [[]]
        self.assertTrue(self.course.forum_posts_allowed)


@ddt
class CourseKeyVerificationTestCase(CourseTestCase):
    """Test class to verify course decorator operations"""

    def setUp(self):
        """
        Create test course.
        """
        super(CourseKeyVerificationTestCase, self).setUp()
        self.course = CourseFactory.create(org='edX', number='test_course_key', display_name='Test Course')

    @data(('edX/test_course_key/Test_Course', 200), ('garbage:edX+test_course_key+Test_Course', 404))
    @unpack
    def test_course_key_decorator(self, course_key, status_code):
        """
        Tests for the ensure_valid_course_key decorator.
        """
        url = '/import/{course_key}'.format(course_key=course_key)
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, status_code)

        url = '/import_status/{course_key}/{filename}'.format(
            course_key=course_key,
            filename='xyz.tar.gz'
        )
        resp = self.client.get_html(url)
        self.assertEqual(resp.status_code, status_code)
