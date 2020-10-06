"""test passlib.apps"""
#=============================================================================
# imports
#=============================================================================
# core
import logging; log = logging.getLogger(__name__)
# site
# pkg
from passlib import apps, hash as hashmod
from passlib.tests.utils import TestCase
# module

#=============================================================================
# test predefined app contexts
#=============================================================================
class AppsTest(TestCase):
    """perform general tests to make sure contexts work"""
    # NOTE: these tests are not really comprehensive,
    #       since they would do little but duplicate
    #       the presets in apps.py
    #
    #       they mainly try to ensure no typos
    #       or dynamic behavior foul-ups.

    def test_master_context(self):
        ctx = apps.master_context
        self.assertGreater(len(ctx.schemes()), 50)

    def test_custom_app_context(self):
        ctx = apps.custom_app_context
        self.assertEqual(ctx.schemes(), ("sha512_crypt", "sha256_crypt"))
        for hash in [
            ('$6$rounds=41128$VoQLvDjkaZ6L6BIE$4pt.1Ll1XdDYduEwEYPCMOBiR6W6'
                'znsyUEoNlcVXpv2gKKIbQolgmTGe6uEEVJ7azUxuc8Tf7zV9SD2z7Ij751'),
            ('$5$rounds=31817$iZGmlyBQ99JSB5n6$p4E.pdPBWx19OajgjLRiOW0itGny'
                 'xDGgMlDcOsfaI17'),
        ]:
            self.assertTrue(ctx.verify("test", hash))

    def test_django16_context(self):
        ctx = apps.django16_context
        for hash in [
            'pbkdf2_sha256$29000$ZsgquwnCyBs2$fBxRQpfKd2PIeMxtkKPy0h7SrnrN+EU/cm67aitoZ2s=',
            'sha1$0d082$cdb462ae8b6be8784ef24b20778c4d0c82d5957f',
            'md5$b887a$37767f8a745af10612ad44c80ff52e92',
            'crypt$95a6d$95x74hLDQKXI2',
            '098f6bcd4621d373cade4e832627b4f6',
        ]:
            self.assertTrue(ctx.verify("test", hash))

        self.assertEqual(ctx.identify("!"), "django_disabled")
        self.assertFalse(ctx.verify("test", "!"))

    def test_django_context(self):
        ctx = apps.django_context
        for hash in [
            'pbkdf2_sha256$29000$ZsgquwnCyBs2$fBxRQpfKd2PIeMxtkKPy0h7SrnrN+EU/cm67aitoZ2s=',
        ]:
            self.assertTrue(ctx.verify("test", hash))

        self.assertEqual(ctx.identify("!"), "django_disabled")
        self.assertFalse(ctx.verify("test", "!"))

    def test_ldap_nocrypt_context(self):
        ctx = apps.ldap_nocrypt_context
        for hash in [
            '{SSHA}cPusOzd6d5n3OjSVK3R329ZGCNyFcC7F',
            'test',
        ]:
            self.assertTrue(ctx.verify("test", hash))

        self.assertIs(ctx.identify('{CRYPT}$5$rounds=31817$iZGmlyBQ99JSB5'
                'n6$p4E.pdPBWx19OajgjLRiOW0itGnyxDGgMlDcOsfaI17'), None)

    def test_ldap_context(self):
        ctx = apps.ldap_context
        for hash in [
            ('{CRYPT}$5$rounds=31817$iZGmlyBQ99JSB5n6$p4E.pdPBWx19OajgjLRiOW0'
                'itGnyxDGgMlDcOsfaI17'),
            '{SSHA}cPusOzd6d5n3OjSVK3R329ZGCNyFcC7F',
            'test',
        ]:
            self.assertTrue(ctx.verify("test", hash))

    def test_ldap_mysql_context(self):
        ctx = apps.mysql_context
        for hash in [
            '*94BDCEBE19083CE2A1F959FD02F964C7AF4CFC29',
            '378b243e220ca493',
        ]:
            self.assertTrue(ctx.verify("test", hash))

    def test_postgres_context(self):
        ctx = apps.postgres_context
        hash = 'md55d9c68c6c50ed3d02a2fcf54f63993b6'
        self.assertTrue(ctx.verify("test", hash, user='user'))

    def test_phppass_context(self):
        ctx = apps.phpass_context
        for hash in [
            '$P$8Ja1vJsKa5qyy/b3mCJGXM7GyBnt6..',
            '$H$8b95CoYQnQ9Y6fSTsACyphNh5yoM02.',
            '_cD..aBxeRhYFJvtUvsI',
        ]:
            self.assertTrue(ctx.verify("test", hash))

        h1 = "$2a$04$yjDgE74RJkeqC0/1NheSSOrvKeu9IbKDpcQf/Ox3qsrRS/Kw42qIS"
        if hashmod.bcrypt.has_backend():
            self.assertTrue(ctx.verify("test", h1))
            self.assertEqual(ctx.default_scheme(), "bcrypt")
            self.assertEqual(ctx.handler().name, "bcrypt")
        else:
            self.assertEqual(ctx.identify(h1), "bcrypt")
            self.assertEqual(ctx.default_scheme(), "phpass")
            self.assertEqual(ctx.handler().name, "phpass")

    def test_phpbb3_context(self):
        ctx = apps.phpbb3_context
        for hash in [
            '$P$8Ja1vJsKa5qyy/b3mCJGXM7GyBnt6..',
            '$H$8b95CoYQnQ9Y6fSTsACyphNh5yoM02.',
        ]:
            self.assertTrue(ctx.verify("test", hash))
        self.assertTrue(ctx.hash("test").startswith("$H$"))

    def test_roundup_context(self):
        ctx = apps.roundup_context
        for hash in [
            '{PBKDF2}9849$JMTYu3eOUSoFYExprVVqbQ$N5.gV.uR1.BTgLSvi0qyPiRlGZ0',
            '{SHA}a94a8fe5ccb19ba61c4c0873d391e987982fbbd3',
            '{CRYPT}dptOmKDriOGfU',
            '{plaintext}test',
        ]:
            self.assertTrue(ctx.verify("test", hash))

#=============================================================================
# eof
#=============================================================================
