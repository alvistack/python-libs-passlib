"""passlib.bcrypt -- implementation of OpenBSD's BCrypt algorithm.

TODO:

* support 2x and altered-2a hashes?
  http://www.openwall.com/lists/oss-security/2011/06/27/9

* deal with lack of PY3-compatibile c-ext implementation
"""
#=============================================================================
# imports
#=============================================================================
from __future__ import with_statement, absolute_import
# core
from base64 import b64encode
from hashlib import sha256
import os
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
# site
_bcrypt = None # dynamically imported by _load_backend_bcrypt()
_pybcrypt = None # dynamically imported by _load_backend_pybcrypt()
_bcryptor = None # dynamically imported by _load_backend_bcryptor()
# pkg
_builtin_bcrypt = None  # dynamically imported by _load_backend_builtin()
from passlib.exc import PasslibHashWarning, PasslibSecurityWarning, PasslibSecurityError
from passlib.utils import bcrypt64, safe_crypt, repeat_string, to_bytes, parse_version, \
                          rng, getrandstr, test_crypt, to_unicode
from passlib.utils.compat import u, uascii_to_str, unicode, str_to_uascii
import passlib.utils.handlers as uh

# local
__all__ = [
    "bcrypt",
]

#=============================================================================
# support funcs & constants
#=============================================================================
IDENT_2 = u("$2$")
IDENT_2A = u("$2a$")
IDENT_2X = u("$2x$")
IDENT_2Y = u("$2y$")
IDENT_2B = u("$2b$")
_BNULL = b'\x00'

def _detect_pybcrypt():
    """
    internal helper which tries to distinguish pybcrypt vs bcrypt.

    :returns:
        True if cext-based py-bcrypt,
        False if ffi-based bcrypt,
        None if 'bcrypt' module not found.

    .. versionchanged:: 1.6.3

        Now assuming bcrypt installed, unless py-bcrypt explicitly detected.
        Previous releases assumed py-bcrypt by default.

        Making this change since py-bcrypt is (apparently) unmaintained and static,
        whereas bcrypt is being actively maintained, and it's internal structure may shift.
    """
    # NOTE: this is also used by the unittests.

    # check for module.
    try:
        import bcrypt
    except ImportError:
        return None

    # py-bcrypt has a "._bcrypt.__version__" attribute (confirmed for v0.1 - 0.4),
    # which bcrypt lacks (confirmed for v1.0 - 2.0)
    # "._bcrypt" alone isn't sufficient, since bcrypt 2.0 now has that attribute.
    try:
        from bcrypt._bcrypt import __version__
    except ImportError:
        return False
    return True

#=============================================================================
# handler
#=============================================================================
class bcrypt(uh.HasManyIdents, uh.HasRounds, uh.HasSalt, uh.HasManyBackends, uh.GenericHandler):
    """This class implements the BCrypt password hash, and follows the :ref:`password-hash-api`.

    It supports a fixed-length salt, and a variable number of rounds.

    The :meth:`~passlib.ifc.PasswordHash.using` method accepts the following optional keywords:

    :type salt: str
    :param salt:
        Optional salt string.
        If not specified, one will be autogenerated (this is recommended).
        If specified, it must be 22 characters, drawn from the regexp range ``[./0-9A-Za-z]``.

    :type rounds: int
    :param rounds:
        Optional number of rounds to use.
        Defaults to 12, must be between 4 and 31, inclusive.
        This value is logarithmic, the actual number of iterations used will be :samp:`2**{rounds}`
        -- increasing the rounds by +1 will double the amount of time taken.

    :type ident: str
    :param ident:
        Specifies which version of the BCrypt algorithm will be used when creating a new hash.
        Typically this option is not needed, as the default (``"2a"``) is usually the correct choice.
        If specified, it must be one of the following:

        * ``"2"`` - the first revision of BCrypt, which suffers from a minor security flaw and is generally not used anymore.
        * ``"2a"`` - some implementations suffered from a very rare security flaw.
          current default for compatibility purposes.
        * ``"2y"`` - format specific to the *crypt_blowfish* BCrypt implementation,
          identical to ``"2a"`` in all but name.
        * ``"2b"`` - latest revision of the official BCrypt algorithm (will be default in Passlib 1.7).

    :type relaxed: bool
    :param relaxed:
        By default, providing an invalid value for one of the other
        keywords will result in a :exc:`ValueError`. If ``relaxed=True``,
        and the error can be corrected, a :exc:`~passlib.exc.PasslibHashWarning`
        will be issued instead. Correctable errors include ``rounds``
        that are too small or too large, and ``salt`` strings that are too long.

        .. versionadded:: 1.6

    .. versionchanged:: 1.6
        This class now supports ``"2y"`` hashes, and recognizes
        (but does not support) the broken ``"2x"`` hashes.
        (see the :ref:`crypt_blowfish bug <crypt-blowfish-bug>`
        for details).

    .. versionchanged:: 1.6
        Added a pure-python backend.

    .. versionchanged:: 1.6.3

        Added support for ``"2b"`` variant.
    """

    #===================================================================
    # class attrs
    #===================================================================
    #--GenericHandler--
    name = "bcrypt"
    setting_kwds = ("salt", "rounds", "ident")
    checksum_size = 31
    checksum_chars = bcrypt64.charmap

    #--HasManyIdents--
    default_ident = IDENT_2A
    ident_values = (IDENT_2, IDENT_2A, IDENT_2X, IDENT_2Y, IDENT_2B)
    ident_aliases = {u("2"): IDENT_2, u("2a"): IDENT_2A,  u("2y"): IDENT_2Y,
                     u("2b"): IDENT_2B}

    #--HasSalt--
    min_salt_size = max_salt_size = 22
    salt_chars = bcrypt64.charmap
        # NOTE: 22nd salt char must be in bcrypt64._padinfo2[1], not full charmap

    #--HasRounds--
    default_rounds = 12 # current passlib default
    min_rounds = 4 # minimum from bcrypt specification
    max_rounds = 31 # 32-bit integer limit (since real_rounds=1<<rounds)
    rounds_cost = "log2"

    #===================================================================
    # formatting
    #===================================================================

    @classmethod
    def from_string(cls, hash):
        ident, tail = cls._parse_ident(hash)
        if ident == IDENT_2X:
            raise ValueError("crypt_blowfish's buggy '2x' hashes are not "
                             "currently supported")
        rounds_str, data = tail.split(u("$"))
        rounds = int(rounds_str)
        if rounds_str != u('%02d') % (rounds,):
            raise uh.exc.MalformedHashError(cls, "malformed cost field")
        salt, chk = data[:22], data[22:]
        return cls(
            rounds=rounds,
            salt=salt,
            checksum=chk or None,
            ident=ident,
        )

    def to_string(self):
        hash = u("%s%02d$%s%s") % (self.ident, self.rounds, self.salt, self.checksum)
        return uascii_to_str(hash)

    # NOTE: this should be kept separate from to_string()
    #       so that bcrypt_sha256() can still use it, while overriding to_string()
    def _get_config(self, ident):
        """internal helper to prepare config string for backends"""
        config = u("%s%02d$%s") % (ident, self.rounds, self.salt)
        return uascii_to_str(config)

    #===================================================================
    # migration
    #===================================================================

    @classmethod
    def needs_update(cls, hash, **kwds):
        # check for incorrect padding bits (passlib issue 25)
        if isinstance(hash, bytes):
            hash = hash.decode("ascii")
        if hash.startswith(IDENT_2A) and hash[28] not in bcrypt64._padinfo2[1]:
            return True

        # TODO: try to detect incorrect 8bit/wraparound hashes using kwds.get("secret")

        # hand off to base implementation, so HasRounds can check rounds value.
        return super(bcrypt, cls).needs_update(hash, **kwds)

    #===================================================================
    # specialized salt generation - fixes passlib issue 25
    #===================================================================

    @classmethod
    def normhash(cls, hash):
        """helper to normalize hash, correcting any bcrypt padding bits"""
        if cls.identify(hash):
            return cls.from_string(hash).to_string()
        else:
            return hash

    @classmethod
    def _generate_salt(cls):
        # generate random salt as normal,
        # but repair last char so the padding bits always decode to zero.
        salt = super(bcrypt, cls)._generate_salt()
        return bcrypt64.repair_unused(salt)

    @classmethod
    def _norm_salt(cls, salt, **kwds):
        salt = super(bcrypt, cls)._norm_salt(salt, **kwds)
        assert salt is not None, "HasSalt didn't generate new salt!"
        changed, salt = bcrypt64.check_repair_unused(salt)
        if changed:
            # FIXME: if salt was provided by user, this message won't be
            # correct. not sure if we want to throw error, or use different warning.
            warn(
                "encountered a bcrypt salt with incorrectly set padding bits; "
                "you may want to use bcrypt.normhash() "
                "to fix this; see Passlib 1.5.3 changelog.",
                PasslibHashWarning)
        return salt

    def _norm_checksum(self, checksum, relaxed=False):
        checksum = super(bcrypt, self)._norm_checksum(checksum, relaxed=relaxed)
        changed, checksum = bcrypt64.check_repair_unused(checksum)
        if changed:
            warn(
                "encountered a bcrypt hash with incorrectly set padding bits; "
                "you may want to use bcrypt.normhash() "
                "to fix this; see Passlib 1.5.3 changelog.",
                PasslibHashWarning)
        return checksum

    #===================================================================
    # backend configuration
    #===================================================================

    backends = ("bcrypt", "pybcrypt", "bcryptor", "os_crypt", "builtin")

    # appended to HasManyBackends' "no backends available" error message
    _no_backend_suggestion = " -- recommend you install one (e.g. 'pip install bcrypt')"

    # backend workaround detection
    _has_wraparound_bug = False
    _lacks_20_support = False
    _lacks_2y_support = False
    _lacks_2b_support = False

    #---------------------------------------------------------------
    # backend capability/bug detection
    #---------------------------------------------------------------
    @classmethod
    def set_backend(cls, name="any", dryrun=False):
        """
        subclass hook to handle workaround detection
        """
        super(bcrypt, cls).set_backend(name, dryrun=dryrun)
        if not dryrun:
            cls._configure_workarounds()

    @classmethod
    def _configure_workarounds(cls, backend):
        """
        detect & configure workarounds for specific backend
        """
        backend = cls.get_backend()

        # check for cryptblowfish 8bit bug (fixed in 2y/2b);
        # even though it's not known to be present in any of passlib's backends.
        # this is treated as FATAL, because it can easily result in seriously malformed hashes,
        # and we can't correct for it ourselves.
        # test cases from <http://cvsweb.openwall.com/cgi/cvsweb.cgi/Owl/packages/glibc/crypt_blowfish/wrapper.c.diff?r1=1.9;r2=1.10>
        # NOTE: reference hash is the incorrectly generated $2x$ hash taken from above url
        if cls.verify(u("\xA3"),
                      "$2a$05$/OK.fbVrR/bpIqNJ5ianF.CE5elHaaO4EbggVDjb8P19RukzXSM3e"):
            raise PasslibSecurityError(
                "passlib.hash.bcrypt: Your installation of the %r backend is vulnerable to "
                "the crypt_blowfish 8-bit bug (CVE-2011-2483), "
                "and should be upgraded or replaced with another backend." % backend)

        # check for bsd wraparound bug (fixed in 2b)
        # this is treated as a warning, because it's rare in the field,
        # and pybcrypt (as of 2015-7-21) is unpatched, but some people may be stuck with it.
        # test cases from <http://www.openwall.com/lists/oss-security/2012/01/02/4>
        # NOTE: reference hash is of password "0"*72
        # NOTE: if in future we need to deliberately create hashes which have this bug,
        #       can use something like 'hashpw(repeat_string(secret[:((1+secret) % 256) or 1]), 72)'
        cls._has_wraparound_bug = False
        if cls.verify(("0123456789"*26)[:255],
                      "$2a$04$R1lJ2gkNaoPGdafE.H.16.nVyh2niHsGJhayOHLMiXlI45o8/DU.6"):
            warn("passlib.hash.bcrypt: Your installation of the %r backend is vulnerable to "
                 "the bsd wraparound bug, "
                 "and should be upgraded or replaced with another backend." % backend, 
                 uh.exc.PasslibSecurityWarning)
            cls._has_wraparound_bug = True

        def _detect_lacks_variant(ident, refhash):
            """helper to detect if backend *lacks* support for specified bcrypt variant"""
            assert refhash.startswith(ident)
            # NOTE: can't use cls.verify() directly or we have recursion error
            try:
                result = cls.verify("test", refhash)
            except (ValueError, _bcryptor.engine.SaltError if _bcryptor else ValueError):
                # backends without support will throw various errors about unrecognized version
                # pybcrypt, bcrypt -- raises ValueError
                # bcryptor -- raises bcryptor.engine.SaltError
                log.debug("%r backend lacks %r support", backend, ident)
                return True
            assert result, "%r backend %r check failed" % (backend, ident)
            return False

        # check for native 2 support
        # NOTE: have to clear workaround first, so verify() doesn't enable it during detection.
        cls._lacks_20_support = False
        cls._lacks_20_support = _detect_lacks_variant("$2$", "$2$04$5BJqKfqMQvV7nS.yUguNcu"
                                                             "RfMMOXK0xPWavM7pOzjEi5ze5T1k8/S")

        # TODO: check for 2x support

        # check for native 2y support
        cls._lacks_2y_support = False
        cls._lacks_2y_support = _detect_lacks_variant("$2y$", "$2y$04$5BJqKfqMQvV7nS.yUguNcu"
                                                              "eVirQqDBGaLXSqj.rs.pZPlNR0UX/HK")

        # check for native 2b support
        cls._lacks_2b_support = False
        cls._lacks_2b_support = _detect_lacks_variant("$2b$", "$2b$04$5BJqKfqMQvV7nS.yUguNcu"
                                                              "eVirQqDBGaLXSqj.rs.pZPlNR0UX/HK")

        # sanity check
        assert cls._lacks_2b_support or not cls._has_wraparound_bug, \
            "sanity check failed: %r backend supports $2b$ but has wraparound bug" % backend

    #---------------------------------------------------------------
    # prepare secret & config for backend calc
    #---------------------------------------------------------------
    def _calc_checksum(self, secret):
        """common backend code"""

        # make sure it's unicode
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")

        # NOTE: especially important to forbid NULLs for bcrypt, since many
        # backends (bcryptor, bcrypt) happily accept them, and then
        # silently truncate the password at first NULL they encounter!
        if _BNULL in secret:
            raise uh.exc.NullPasswordError(self)

        # ensure backend is loaded before workaround detection
        self.get_backend()

        # protect from wraparound bug by truncating secret before handing it to the backend.
        # bcrypt only uses first 72 bytes anyways.
        if self._has_wraparound_bug and len(secret) >= 255:
            secret = secret[:72]

        # special case handling for variants (ordered most common first)
        ident = self.ident
        if ident == IDENT_2A:
            # fall through and use backend w/o hacks
            pass

        elif ident == IDENT_2B:
            if self._lacks_2b_support:
                # handle $2b$ hash format even if backend is too old.
                # have it generate a 2A digest, then return it as a 2B hash.
                ident = IDENT_2A

        elif ident == IDENT_2Y:
            if self._lacks_2y_support:
                # handle $2y$ hash format (not supported by BSDs, being phased out on others)
                # have it generate a 2A digest, then return it as a 2Y hash.
                ident = IDENT_2A

        elif ident == IDENT_2:
            if self._lacks_20_support:
                # handle legacy $2$ format (not supported by most backends except BSD os_crypt)
                # we can fake $2$ behavior using the $2a$ algorithm
                # by repeating the password until it's at least 72 chars in length.
                if secret:
                    secret = repeat_string(secret, 72)
                ident = IDENT_2A

        elif ident == IDENT_2X:

            # NOTE: shouldn't get here.
            # XXX: could check if backend does actually offer 'support'
            raise RuntimeError("$2x$ hashes not currently supported by passlib")

        else:
            raise AssertionError("unexpected ident value: %r" % ident)

        # invoke backend
        config = self._get_config(ident)
        return self._calc_checksum_backend(secret, config)

    #---------------------------------------------------------------
    # bcrypt backend
    #---------------------------------------------------------------
    @classmethod
    def _load_backend_bcrypt(cls):
        # try to import bcrypt
        global _bcrypt
        if _detect_pybcrypt():
            # pybcrypt was installed instead
            return None
        try:
            import bcrypt as _bcrypt
        except ImportError: # pragma: no cover
            return None
        try:
            version = _bcrypt.__about__.__version__
        except:
            log.warning("(trapped) error reading bcrypt version", exc_info=True)
            version = '<unknown>'
        log.debug("detected 'bcrypt' backend, version %r", version)
        return cls._calc_checksum_bcrypt

    def _calc_checksum_bcrypt(self, secret, config):
        # bcrypt behavior:
        #   hash must be ascii bytes
        #   secret must be bytes
        #   returns bytes
        if isinstance(config, unicode):
            config = config.encode("ascii")
        hash = _bcrypt.hashpw(secret, config)
        assert hash.startswith(config) and len(hash) == len(config)+31
        assert isinstance(hash, bytes)
        return hash[-31:].decode("ascii")

    #---------------------------------------------------------------
    # pybcrypt backend
    #---------------------------------------------------------------

    #: classwide thread lock used for pybcrypt < 0.3
    _calc_lock = None

    @classmethod
    def _load_backend_pybcrypt(cls):
        # try to import pybcrypt
        global _pybcrypt
        if not _detect_pybcrypt():
            # not installed, or bcrypt installed instead
            return None
        try:
            import bcrypt as _pybcrypt
        except ImportError: # pragma: no cover
            return None

        # determine pybcrypt version
        try:
            version = _pybcrypt._bcrypt.__version__
        except:
            log.warning("(trapped) error reading pybcrypt version", exc_info=True)
            version = "<unknown>"
        log.debug("detected 'pybcrypt' backend, version %r", version)

        # return calc function based on version
        vinfo = parse_version(version) or (0, 0)
        if vinfo < (0, 3):
            warn("py-bcrypt %s has a major security vulnerability, "
                 "you should upgrade to py-bcrypt 0.3 immediately."
                 % version, uh.exc.PasslibSecurityWarning)
            if cls._calc_lock is None:
                import threading
                cls._calc_lock = threading.Lock()
            return cls._calc_checksum_pybcrypt_threadsafe
        else:
            return cls._calc_checksum_pybcrypt

    def _calc_checksum_pybcrypt_threadsafe(self, secret, config):
        # as workaround for pybcrypt < 0.3's concurrency issue,
        # we wrap everything in a thread lock. as long as bcrypt is only
        # used through passlib, this should be safe.
        with self._calc_lock:
            return self._calc_checksum_pybcrypt(secret, config)

    def _calc_checksum_pybcrypt(self, secret, config):
        # py-bcrypt behavior:
        #   py2: unicode secret/hash encoded as ascii bytes before use,
        #        bytes taken as-is; returns ascii bytes.
        #   py3: unicode secret encoded as utf-8 bytes,
        #        hash encoded as ascii bytes, returns ascii unicode.
        hash = _pybcrypt.hashpw(secret, config)
        assert hash.startswith(config) and len(hash) == len(config)+31
        return str_to_uascii(hash[-31:])

    #---------------------------------------------------------------
    # bcryptor backend
    #---------------------------------------------------------------
    @classmethod
    def _load_backend_bcryptor(cls):
        # try to import bcryptor
        global _bcryptor
        try:
            import bcryptor as _bcryptor
        except ImportError: # pragma: no cover
            return None
        return cls._calc_checksum_bcryptor

    def _calc_checksum_bcryptor(self, secret, config):
        # bcryptor behavior:
        #   py2: unicode secret/hash encoded as ascii bytes before use,
        #        bytes taken as-is; returns ascii bytes.
        #   py3: not supported
        hash = _bcryptor.engine.Engine(False).hash_key(secret, config)
        assert hash.startswith(config) and len(hash) == len(config)+31
        return str_to_uascii(hash[-31:])

    #---------------------------------------------------------------
    # os crypt() backend
    #---------------------------------------------------------------
    @classmethod
    def _load_backend_os_crypt(cls):
        # XXX: what to do if "2" isn't supported, but "2a" is?
        #      "2" is *very* rare, and can fake it using "2a"+repeat_string
        h1 = '$2$04$......................1O4gOrCYaqBG3o/4LnT2ykQUt1wbyju'
        h2 = '$2a$04$......................qiOQjkB8hxU8OzRhS.GhRMa4VUnkPty'
        if test_crypt("test", h1) and test_crypt("test", h2):
            return cls._calc_checksum_os_crypt
        return None

    def _calc_checksum_os_crypt(self, secret, config):
        hash = safe_crypt(secret, config)
        if hash:
            assert hash.startswith(config) and len(hash) == len(config)+31
            return hash[-31:]
        else:
            # NOTE: Have to raise this error because python3's crypt.crypt() only accepts unicode.
            #       This means it can't handle any passwords that aren't either unicode
            #       or utf-8 encoded bytes.  However, hashing a password with an alternate
            #       encoding should be a pretty rare edge case; if user needs it, they can just
            #       install bcrypt backend.
            # XXX: is this the right error type to raise?
            #      maybe have safe_crypt() not swallow UnicodeDecodeError, and have handlers
            #      like sha256_crypt trap it if they have alternate method of handling them?
            raise uh.exc.MissingBackendError(
                "non-utf8 encoded passwords can't be handled by crypt.crypt() under python3, "
                "recommend running `pip install bcrypt`.",
                )

    #---------------------------------------------------------------
    # builtin backend
    #---------------------------------------------------------------
    @classmethod
    def _load_backend_builtin(cls):
        if os.environ.get("PASSLIB_BUILTIN_BCRYPT") not in ["enable","enabled"]:
            log.debug("bcrypt 'builtin' backend not enabled via $PASSLIB_BUILTIN_BCRYPT")
            return None
        global _builtin_bcrypt
        from passlib.crypto._blowfish import raw_bcrypt as _builtin_bcrypt
        return cls._calc_checksum_builtin

    def _calc_checksum_builtin(self, secret, config):
        chk = _builtin_bcrypt(secret, config[1:config.index("$", 1)],
                              self.salt.encode("ascii"), self.rounds)
        return chk.decode("ascii")

    #===================================================================
    # eoc
    #===================================================================

_UDOLLAR = u("$")

class bcrypt_sha256(bcrypt):
    """This class implements a composition of BCrypt+SHA256, and follows the :ref:`password-hash-api`.

    It supports a fixed-length salt, and a variable number of rounds.

    The :meth:`~passlib.ifc.PasswordHash.hash` and :meth:`~passlib.ifc.PasswordHash.genconfig` methods accept
    all the same optional keywords as the base :class:`bcrypt` hash.

    .. versionadded:: 1.6.2

    .. versionchanged:: 1.7

        Now defaults to '2b' bcrypt algorithm.
    """
    name = "bcrypt_sha256"

    # this is locked at 2a/2b for now.
    ident_values = (IDENT_2A, IDENT_2B)

    # clone bcrypt's ident aliases so they can be used here as well...
    ident_aliases = (lambda ident_values: dict(item for item in bcrypt.ident_aliases.items()
                                               if item[1] in ident_values))(ident_values)
    default_ident = IDENT_2B

    # sample hash:
    # $bcrypt-sha256$2a,6$/3OeRpbOf8/l6nPPRdZPp.$nRiyYqPobEZGdNRBWihQhiFDh1ws1tu
    # $bcrypt-sha256$           -- prefix/identifier
    # 2a                        -- bcrypt variant
    # ,                         -- field separator
    # 6                         -- bcrypt work factor
    # $                         -- section separator
    # /3OeRpbOf8/l6nPPRdZPp.    -- salt
    # $                         -- section separator
    # nRiyYqPobEZGdNRBWihQhiFDh1ws1tu  -- digest

    # XXX: we can't use .ident attr due to bcrypt code using it.
    #      working around that via prefix.
    prefix = u('$bcrypt-sha256$')

    _hash_re = re.compile(r"""
        ^
        [$]bcrypt-sha256
        [$](?P<variant>[a-z0-9]+)
        ,(?P<rounds>\d{1,2})
        [$](?P<salt>[^$]{22})
        ([$](?P<digest>.{31}))?
        $
        """, re.X)

    @classmethod
    def identify(cls, hash):
        hash = uh.to_unicode_for_identify(hash)
        if not hash:
            return False
        return hash.startswith(cls.prefix)

    @classmethod
    def from_string(cls, hash):
        hash = to_unicode(hash, "ascii", "hash")
        if not hash.startswith(cls.prefix):
            raise uh.exc.InvalidHashError(cls)
        m = cls._hash_re.match(hash)
        if not m:
            raise uh.exc.MalformedHashError(cls)
        rounds = m.group("rounds")
        if rounds.startswith(uh._UZERO) and rounds != uh._UZERO:
            raise uh.exc.ZeroPaddedRoundsError(cls)
        return cls(ident=m.group("variant"),
                   rounds=int(rounds),
                   salt=m.group("salt"),
                   checksum=m.group("digest"),
                   )

    def to_string(self):
        hash = u("%s%s,%d$%s$%s") % (self.prefix, self.ident.strip(_UDOLLAR),
                                     self.rounds, self.salt, self.checksum)
        return uascii_to_str(hash)

    def _calc_checksum(self, secret):
        # NOTE: can't use digest directly, since bcrypt stops at first NULL.
        # NOTE: bcrypt doesn't fully mix entropy for bytes 55-72 of password
        #       (XXX: citation needed), so we don't want key to be > 55 bytes.
        #       thus, have to use base64 (44 bytes) rather than hex (64 bytes).
        # XXX: it's later come out that 55-72 may be ok, so later revision of bcrypt_sha256
        #      may switch to hex encoding, since it's simpler to implement elsewhere.
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        key = b64encode(sha256(secret).digest())

        # hand result off to normal bcrypt algorithm
        return super(bcrypt_sha256, self)._calc_checksum(key)

    # patch set_backend so it modifies bcrypt class, not this one...
    # else the bcrypt.set_backend() tests will call the wrong class.
    # XXX: move this (and a get_backend wrapper) to bcrypt?
    #      also having to set this in django_bcrypt wrappers
    @classmethod
    def set_backend(cls, *args, **kwds):
        return bcrypt.set_backend(*args, **kwds)

    # XXX: have _needs_update() mark the $2a$ ones for upgrading?
    #      so do that after we switch to hex encoding?

#=============================================================================
# eof
#=============================================================================
