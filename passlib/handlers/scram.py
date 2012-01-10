"""passlib.handlers.scram - SCRAM hash

Notes
=====
Working on hash to format to support storing SCRAM protocol information
server-side.

passlib issue - https://code.google.com/p/passlib/issues/detail?id=23

scram protocol - http://tools.ietf.org/html/rfc5802
                 http://tools.ietf.org/html/rfc5803

"""
#=========================================================
#imports
#=========================================================
#core
from binascii import hexlify, unhexlify
from base64 import b64encode, b64decode
import hashlib
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
#site
#libs
from passlib.utils import adapted_b64_encode, adapted_b64_decode, xor_bytes, \
        handlers as uh, to_native_str, to_unicode, consteq, saslprep
from passlib.utils.compat import unicode, bytes, u, b, iteritems, itervalues, \
                                 PY2, PY3
from passlib.utils.pbkdf2 import pbkdf2, get_prf
#pkg
#local
__all__ = [
    "scram",
]

def test_reference_scram():
    "quick hack testing scram reference vectors"
    from passlib.utils import xor_bytes
    from passlib.utils.pbkdf2 import pbkdf2, get_prf
    from hashlib import sha1

    # NOTE: "n,," is GS2 header - see https://tools.ietf.org/html/rfc5801

    digest = "sha1"
    salt = 'QSXCR+Q6sek8bf92'.decode("base64")
    rounds = 4096
    username = "user"
    password = "pencil"
    client_nonce = "fyko+d2lbbFgONRv9qkxdawL"
    server_nonce = "3rfcNHYJY1ZVvWVs7j"

    # hash passwd
    hk = pbkdf2(password, salt, rounds, -1, prf="hmac-" + digest)

    # auth msg
    auth_msg = (
        'n={username},r={client_nonce}'
            ','
        'r={client_nonce}{server_nonce},s={salt},i={rounds}'
            ','
        'c=biws,r={client_nonce}{server_nonce}'
        ).format(salt=salt.encode("base64").rstrip(), rounds=rounds,
                 client_nonce=client_nonce, server_nonce=server_nonce,
                 username=username)
    print repr(auth_msg)

    # client proof
    hmac, hmac_size = get_prf("hmac-" + digest)
    ck = hmac(hk, "Client Key")
    cs = hmac(sha1(ck).digest(), auth_msg)
    cp = xor_bytes(ck, cs).encode("base64").rstrip()
    assert cp == "v0X8v3Bz2T0CJGbJQyF0X+HI4Ts=", cp

    # server proof
    sk = hmac(hk, "Server Key")
    ss = hmac(sk, auth_msg).encode("base64").rstrip()
    assert ss == "rmF9pqV8S7suAoZWja4dJRkFsKQ=", ss

class scram_record(tuple):
    #=========================================================
    # init
    #=========================================================

    @classmethod
    def from_string(cls, hash, alg):
        "create record from scram hash, for given alg"
        return cls(alg, *scram.extract_digest_info(hash, alg))

    def __new__(cls, *args):
        return tuple.__new__(cls, args)

    def __init__(self, salt, rounds, alg, digest):
        self.alg = norm_digest_name(alg)
        self.salt = salt
        self.rounds = rounds
        self.digest = digest

    #=========================================================
    # frontend methods
    #=========================================================
    def get_hash(self, data):
        "return hash of raw data"
        return hashlib.new(iana_to_hashlib(self.alg), data).digest()

    def get_client_proof(self, msg):
        "return client proof of specified auth msg text"
        return xor_bytes(self.client_key, self.get_client_sig(msg))

    def get_client_sig(self, msg):
        "return client signature of specified auth msg text"
        return self.get_hmac(self.stored_key, msg)

    def get_server_sig(self, msg):
        "return server signature of specified auth msg text"
        return self.get_hmac(self.server_key, msg)

    def format_server_response(self, client_nonce, server_nonce):
        return 'r={client_nonce}{server_nonce},s={salt},i={rounds}'.format(
            client_nonce=client_nonce,
            server_nonce=server_nonce,
            rounds=self.rounds,
            salt=self.encoded_salt,
            )

    def format_auth_msg(self, username, client_nonce, server_nonce,
                        header='c=biws'):
        return (
            'n={username},r={client_nonce}'
                ','
            'r={client_nonce}{server_nonce},s={salt},i={rounds}'
                ','
            '{header},r={client_nonce}{server_nonce}'
            ).format(
                username=username,
                client_nonce=client_nonce,
                server_nonce=server_nonce,
                salt=self.encoded_salt,
                rounds=rounds,
                header=header,
                )

    #=========================================================
    # helpers to calculate & cache constant data
    #=========================================================
    def _calc_get_hmac(self):
        return get_prf("hmac-" + iana_to_hashlib(self.alg))[0]

    def _calc_client_key(self):
        return self.get_hmac(self.digest, b("Client Key"))

    def _calc_stored_key(self):
        return self.get_hash(self.client_key)

    def _calc_server_key(self):
        return self.get_hmac(self.digest, b("Server Key"))

    def _calc_encoded_salt(self):
        return self.salt.encode("base64").rstrip()

    #=========================================================
    # hacks for calculated attributes
    #=========================================================
    def __getattr__(self, attr):
        if not attr.startswith("_"):
            f = getattr(self, "_calc_" + attr, None)
            if f:
                value = f()
                setattr(self, attr, value)
                return value
        raise AttributeError("attribute not found")

    def __dir__(self):
        cdir = dir(self.__class__)
        attrs = set(cdir)
        attrs.update(self.__dict__)
        attrs.update(attr[6:] for attr in cdir
                     if attr.startswith("_calc_"))
        return sorted(attrs)
    #=========================================================
    # eoc
    #=========================================================

#=========================================================
# helpers
#=========================================================
# set of known iana names --
# http://www.iana.org/assignments/hash-function-text-names
iana_digests = frozenset(["md2", "md5", "sha-1", "sha-224", "sha-256",
                          "sha-384", "sha-512"])

# cache for norm_digest_name()
_ndn_cache = {}

def norm_digest_name(name):
    """normalize digest names to IANA hash function name.

    :arg name:
        name can be a Python :mod:`~hashlib` digest name,
        a SCRAM mechanism name, etc; case insensitive.

        input can be either unicode or bytes.

    :returns:
        native string containing lower-case IANA hash function name.
        if IANA has not assigned one, this will make a guess as to
        what the IANA-style representation should be.
    """
    # check cache
    try:
        return _ndn_cache[name]
    except KeyError:
        pass
    key = name

    # normalize case
    name = name.strip().lower().replace("_","-")

    # extract digest from scram mechanism name
    if name.startswith("scram-"):
        name = name[6:]
        if name.endswith("-plus"):
            name = name[:-5]

    # handle some known aliases
    if name not in iana_digests:
        if name == "sha1":
            name = "sha-1"
        else:
            m = re.match("^sha2-(\d{3})$", name)
            if m:
                name = "sha-" + m.group(1)

    # run heuristics if not an official name
    if name not in iana_digests:

        # add hyphen between hash name and digest size;
        # e.g. "ripemd160" -> "ripemd-160"
        m = re.match("^([a-z]+)(\d{3,4})$", name)
        if m:
            name = m.group(1) + "-" + m.group(2)

        # remove hyphen between hash name & version (e.g. MD-5 -> MD5)
        # note that SHA-1 is an exception to this, but taken care of above.
        m = re.match("^([a-z]+)-(\d)$", name)
        if m:
            name = m.group(1) + m.group(2)

        # check for invalid chars
        if re.search("[^a-z0-9-]", name):
            raise ValueError("invalid characters in digest name: %r" % (name,))

        # issue warning if not in the expected format,
        # this might be a sign of some strange input
        # (and digest probably won't be found)
        m = re.match("^([a-z]{2,}\d?)(-\d{3,4})?$", name)
        if not m:
            warn("encountered oddly named digest: %r" % (name,))

    # store in cache
    _ndn_cache[key] = name
    return name

def iana_to_hashlib(name):
    "adapt iana hash name -> hashlib hash name"
    # NOTE: assumes this has been run through norm_digest_name()
    # XXX: this works for all known cases for now, might change in future.
    return name.replace("-","")

_gds_cache = {}

def _get_digest_size(name):
    "get size of digest"
    try:
        return _gds_cache[name]
    except KeyError:
        pass
    key = name
    name = iana_to_hashlib(norm_digest_name(name))
    value = hashlib.new(name).digest_size
    _gds_cache[key] = value
    return value

#=========================================================
#
#=========================================================
class scram(uh.HasRounds, uh.HasRawSalt, uh.HasRawChecksum, uh.GenericHandler):
    """This class provides a format for storing SCRAM passwords, and follows
    the :ref:`password-hash-api`.

    It supports a variable-length salt, and a variable number of rounds.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional salt bytes.
        If specified, the length must be between 0-1024 bytes.
        If not specified, a 12 byte salt will be autogenerated
        (this is recommended).

    :param salt_size:
        Optional number of bytes to use when autogenerating new salts.
        Defaults to 12 bytes, but can be any value between 0 and 1024.

    :param rounds:
        Optional number of rounds to use.
        Defaults to 6400, but must be within ``range(1,1<<32)``.

    :param algs:
        Specify list of digest algorithms to use.

        By default each scram hash will contain digests for SHA-1,
        SHA-256, and SHA-512. This may either be a list such as
        ``["sha-1", "sha-256"]``, or a comma-separated string such as
        ``"sha-1,sha-256"``. Names are case insensitive, and may
        use hashlib or IANA compatible hash names.

    This class also provides the following additional class methods
    for manipulating Passlib scram hashes in ways useful for pluging
    into a SCRAM protocol stack:

    .. automethod:: extract_digest_info
    .. automethod:: extract_digest_algs
    .. automethod:: derive_digest
    """
    #=========================================================
    #class attrs
    #=========================================================

    # NOTE: unlike most GenericHandler classes, the 'checksum' attr of
    # ScramHandler is actually a map from digest_name -> digest, so
    # many of the standard methods have been overridden.

    # NOTE: max_salt_size and max_rounds are arbitrarily chosen to provide
    # a sanity check; the underlying pbkdf2 specifies no bounds for either.

    #--GenericHandler--
    name = "scram"
    setting_kwds = ("salt", "salt_size", "rounds", "algs")
    ident = u("$scram$")

    #--HasSalt--
    default_salt_size = 12
    min_salt_size = 0
    max_salt_size = 1024

    #--HasRounds--
    default_rounds = 6400
    min_rounds = 1
    max_rounds = 2**32-1
    rounds_cost = "linear"

    #--custom--

    # default algorithms when creating new hashes.
    default_algs = ["sha-1", "sha-256", "sha-512"]

    # list of algs verify prefers to use, in order.
    _verify_algs = ["sha-256", "sha-512", "sha-384", "sha-224", "sha-1"]

    #=========================================================
    # instance attrs
    #=========================================================

    # 'checksum' is different from most GenericHandler subclasses,
    # in that it contains a dict mapping from alg -> digest,
    # or None if no checksum present.

    #: list of algorithms to create/compare digests for.
    algs = None

    #=========================================================
    # scram frontend helpers
    #=========================================================
    @classmethod
    def extract_digest_info(cls, hash, alg):
        """given scram hash & hash alg, extracts salt, rounds and digest.

        :arg hash:
            Scram hash stored for desired user

        :arg alg:
            Name of digest algorithm (e.g. ``"sha-1"``) requested by client.

            This value is run through :func:`norm_digest_name`,
            so it is case-insensitive, and can be the raw SCRAM
            mechanism name (e.g. ``"SCRAM-SHA-1"``), the IANA name,
            or the hashlib name.

        :raises KeyError:
            If the hash does not contain an entry for the requested digest
            algorithm.

        :returns:
            A tuple containing ``(salt, rounds, digest)``,
            where *digest* matches the raw bytes return by
            SCRAM's :func:`Hi` function for the stored password,
            the provided *salt*, and the iteration count (*rounds*).
            *salt* and *digest* are both raw (unencoded) bytes.
        """
        alg = norm_digest_name(alg)
        self = cls.from_string(hash)
        chkmap = self.checksum
        if not chkmap:
            raise ValueError("scram hash contains no digests")
        return self.salt, self.rounds, chkmap[alg]

    @classmethod
    def extract_digest_algs(cls, hash, hashlib=False):
        """Return names of all algorithms stored in a given hash.

        :arg hash:
            The scram hash to parse

        :param hashlib:
            By default this returns a list of IANA compatible names.
            if this is set to `True`, hashlib-compatible names will
            be returned instead.

        :returns:
            Returns a list of digest algorithms; e.g. ``["sha-1"]``,
            or ``["sha1"]`` if ``hashlib=True``.
        """
        algs = cls.from_string(hash).algs
        if hashlib:
            return [iana_to_hashlib(alg) for alg in algs]
        else:
            return algs

    @classmethod
    def derive_digest(cls, password, salt, rounds, alg):
        """helper to create SaltedPassword digest for SCRAM.

        This performs the step in the SCRAM protocol described as::

            SaltedPassword  := Hi(Normalize(password), salt, i)

        :arg password: password as unicode or utf-8 encoded bytes.
        :arg salt: raw salt as bytes.
        :arg rounds: number of iterations.
        :arg alg: SCRAM-compatible name of digest (e.g. ``"SHA-1"``).

        :returns:
            raw bytes of SaltedPassword
        """
        if isinstance(password, bytes):
            password = password.decode("utf-8")
        password = saslprep(password).encode("utf-8")
        if not isinstance(salt, bytes):
            raise TypeError("salt must be bytes")
        alg = iana_to_hashlib(norm_digest_name(alg))
        return pbkdf2(password, salt, rounds, -1, "hmac-" + alg)

    #=========================================================
    # serialization
    #=========================================================

    @classmethod
    def from_string(cls, hash):
        # parse hash
        if not hash:
            raise ValueError("no hash specified")
        hash = to_native_str(hash, "ascii")
        if not hash.startswith("$scram$"):
            raise ValueError("invalid scram hash")
        parts = hash[7:].split("$")
        if len(parts) != 3:
            raise ValueError("invalid scram hash")
        rounds_str, salt_str, chk_str = parts

        # decode rounds
        rounds = int(rounds_str)
        if rounds_str != str(rounds): #forbid zero padding, etc.
            raise ValueError("invalid scram hash")

        # decode salt
        salt = adapted_b64_decode(salt_str.encode("ascii"))

        # decode algs/digest list
        if not chk_str:
            # scram hashes MUST have something here.
            raise ValueError("invalid scram hash")
        elif "=" in chk_str:
            # comma-separated list of 'alg=digest' pairs
            algs = None
            chkmap = {}
            for pair in chk_str.split(","):
                alg, digest = pair.split("=")
                chkmap[alg] = adapted_b64_decode(digest.encode("ascii"))
        else:
            # comma-separated list of alg names, no digests
            algs = chk_str
            chkmap = None

        # return new object
        return cls(
            rounds=rounds,
            salt=salt,
            checksum=chkmap,
            algs=algs,
            strict=chkmap is not None,
        )

    def to_string(self, withchk=True):
        salt = adapted_b64_encode(self.salt)
        if PY3:
            salt = salt.decode("ascii")
        chkmap = self.checksum
        if withchk and chkmap:
            chk_str = ",".join(
                "%s=%s" % (alg, to_native_str(adapted_b64_encode(chkmap[alg])))
                for alg in self.algs
            )
        else:
            chk_str = ",".join(self.algs)
        return '$scram$%d$%s$%s' % (self.rounds, salt, chk_str)

    #=========================================================
    # init
    #=========================================================
    def __init__(self, algs=None, **kwds):
        super(scram, self).__init__(**kwds)
        self.algs = self.norm_algs(algs)

    @classmethod
    def norm_checksum(cls, checksum, strict=False):
        if checksum is None:
            return None
        for alg, digest in iteritems(checksum):
            if alg != norm_digest_name(alg):
                raise ValueError("malformed algorithm name in scram hash: %r" %
                                 (alg,))
            if len(alg) > 9:
                raise ValueError("SCRAM limits algorithm names to "
                                 "9 characters: %r" % (alg,))
            if not isinstance(digest, bytes):
                raise TypeError("digests must be raw bytes")
        if 'sha-1' not in checksum:
            # NOTE: required because of SCRAM spec.
            raise ValueError("sha-1 must be in algorithm list of scram hash")
        return checksum

    def norm_algs(self, algs):
        "normalize algs parameter"
        # determine default algs value
        if algs is None:
            chk = self.checksum
            if chk is None:
                return list(self.default_algs)
            else:
                return sorted(chk)
        elif self.checksum is not None:
            raise RuntimeError("checksum & algs kwds are mutually exclusive")
        # parse args value
        if isinstance(algs, str):
            algs = algs.split(",")
        algs = sorted(norm_digest_name(alg) for alg in algs)
        if any(len(alg)>9 for alg in algs):
            raise ValueError("SCRAM limits alg names to max of 9 characters")
        if 'sha-1' not in algs:
            # NOTE: required because of SCRAM spec.
            raise ValueError("sha-1 must be in algorithm list of scram hash")
        return algs

    #=========================================================
    # digest methods
    #=========================================================

    @classmethod
    def _deprecation_detector(cls, **settings):
        "generate a deprecation detector for CryptContext to use"
        # generate deprecation hook which marks hashes as deprecated
        # if they don't support a superset of current algs.
        algs = frozenset(cls(**settings).algs)
        def detector(hash):
            return not algs.issubset(cls.from_string(hash).algs)
        return detector

    def calc_checksum(self, secret, alg=None):
        rounds = self.rounds
        salt = self.salt
        hash = self.derive_digest
        if alg:
            # if requested, generate digest for specific alg
            return hash(secret, salt, rounds, alg)
        else:
            # by default, return dict containing digests for all algs
            return dict(
                (alg, hash(secret, salt, rounds, alg))
                for alg in self.algs
            )

    @classmethod
    def verify(cls, secret, hash, full_verify=False):
        self = cls.from_string(hash)
        chkmap = self.checksum
        if not chkmap:
            return False

        # NOTE: to make the verify method efficient, we just calculate hash
        # of shortest digest by default. apps can pass in "full_verify=True" to
        # check entire hash for consistency.
        if full_verify:
            correct = failed = False
            for alg, digest in iteritems(chkmap):
                other = self.calc_checksum(secret, alg)
                # NOTE: could do this length check in norm_algs(),
                # but don't need to be that strict, and want to be able
                # to parse hashes containing algs not supported by platform.
                # it's fine if we fail here though.
                if len(digest) != len(other):
                    raise ValueError("mis-sized %s digest in scram hash: %r != %r"
                                     % (alg, len(digest), len(other)))
                if consteq(other, digest):
                    correct = True
                else:
                    failed = True
            if correct and failed:
                warning("scram hash verified inconsistently, may be corrupted")
                return False
            else:
                return correct
        else:
            # otherwise only verify against one hash, pick one w/ best security.
            for alg in self._verify_algs:
                if alg in chkmap:
                    other = self.calc_checksum(secret, alg)
                    return consteq(other, chkmap[alg])
            # there should *always* be at least sha-1.
            raise AssertionError("sha-1 digest not found!")

    #=========================================================
    #
    #=========================================================

#=========================================================
#eof
#=========================================================
