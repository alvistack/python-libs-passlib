"""passlib.handlers.fshp
"""

#=========================================================
#imports
#=========================================================
#core
from base64 import b64encode, b64decode
import re
import logging; log = logging.getLogger(__name__)
from warnings import warn
#site
#libs
import passlib.utils.handlers as uh
from passlib.utils.compat import b, bytes, bascii_to_str, iteritems, u,\
                                 unicode
from passlib.utils.pbkdf2 import pbkdf1
#pkg
#local
__all__ = [
    'fshp',
]
#=========================================================
#sha1-crypt
#=========================================================
class fshp(uh.HasStubChecksum, uh.HasRounds, uh.HasRawSalt, uh.HasRawChecksum, uh.GenericHandler):
    """This class implements the FSHP password hash, and follows the :ref:`password-hash-api`.

    It supports a variable-length salt, and a variable number of rounds.

    The :meth:`encrypt()` and :meth:`genconfig` methods accept the following optional keywords:

    :param salt:
        Optional raw salt string.
        If not specified, one will be autogenerated (this is recommended).

    :param salt_size:
        Optional number of bytes to use when autogenerating new salts.
        Defaults to 16 bytes, but can be any non-negative value.

    :param rounds:
        Optional number of rounds to use.
        Defaults to 40000, must be between 1 and 4294967295, inclusive.

    :param variant:
        Optionally specifies variant of FSHP to use.

        * ``0`` - uses SHA-1 digest (deprecated).
        * ``1`` - uses SHA-2/256 digest (default).
        * ``2`` - uses SHA-2/384 digest.
        * ``3`` - uses SHA-2/512 digest.
    """

    #=========================================================
    #class attrs
    #=========================================================
    #--GenericHandler--
    name = "fshp"
    setting_kwds = ("salt", "salt_size", "rounds", "variant")
    checksum_chars = uh.PADDED_BASE64_CHARS

    #--HasRawSalt--
    default_salt_size = 16 #current passlib default, FSHP uses 8
    min_salt_size = 0
    max_salt_size = None

    #--HasRounds--
    default_rounds = 16384 #current passlib default, FSHP uses 4096
    min_rounds = 1 #set by FSHP
    max_rounds = 4294967295 # 32-bit integer limit - not set by FSHP
    rounds_cost = "linear"

    #--variants--
    default_variant = 1
    _variant_info = {
        #variant: (hash, digest size)
        0: ("sha1",     20),
        1: ("sha256",   32),
        2: ("sha384",   48),
        3: ("sha512",   64),
        }
    _variant_aliases = dict(
        [(unicode(k),k) for k in _variant_info] +
        [(v[0],k) for k,v in iteritems(_variant_info)]
        )

    #=========================================================
    #instance attrs
    #=========================================================
    variant = None

    #=========================================================
    #init
    #=========================================================
    def __init__(self, variant=None, strict=False, **kwds):
        self.variant = self.norm_variant(variant, strict=strict)
        super(fshp, self).__init__(strict=strict, **kwds)

    @classmethod
    def norm_variant(cls, variant, strict=False):
        if variant is None:
            if strict:
                raise ValueError("no variant specified")
            variant = cls.default_variant
        if isinstance(variant, bytes):
            variant = variant.decode("ascii")
        if isinstance(variant, unicode):
            try:
                variant = cls._variant_aliases[variant]
            except KeyError:
                raise ValueError("invalid fshp variant")
        if not isinstance(variant, int):
            raise TypeError("fshp variant must be int or known alias")
        if variant not in cls._variant_info:
            raise TypeError("unknown fshp variant")
        return variant

    def norm_checksum(self, checksum, strict=False):
        checksum = super(fshp, self).norm_checksum(checksum, strict)
        if checksum is not None and len(checksum) != self._variant_info[self.variant][1]:
            raise ValueError("invalid checksum length for FSHP variant")
        return checksum

    @property
    def _info(self):
        return self._variant_info[self.variant]

    #=========================================================
    #formatting
    #=========================================================

    @classmethod
    def identify(cls, hash):
        return uh.identify_prefix(hash, u("{FSHP"))

    _fshp_re = re.compile(u(r"^\{FSHP(\d+)\|(\d+)\|(\d+)\}([a-zA-Z0-9+/]+={0,3})$"))

    @classmethod
    def from_string(cls, hash):
        if not hash:
            raise ValueError("no hash specified")
        if isinstance(hash, bytes):
            hash = hash.decode("ascii")
        m = cls._fshp_re.match(hash)
        if not m:
            raise ValueError("not a valid FSHP hash")
        variant, salt_size, rounds, data = m.group(1,2,3,4)
        variant = int(variant)
        salt_size = int(salt_size)
        rounds = int(rounds)
        try:
            data = b64decode(data.encode("ascii"))
        except ValueError:
            raise ValueError("malformed FSHP hash")
        salt = data[:salt_size]
        chk = data[salt_size:]
        return cls(checksum=chk, salt=salt, rounds=rounds,
                   variant=variant, strict=True)

    @property
    def _stub_checksum(self):
        return b('\x00') * self._info[1]

    def to_string(self):
        chk = self.checksum or self._stub_checksum
        salt = self.salt
        data = bascii_to_str(b64encode(salt+chk))
        return "{FSHP%d|%d|%d}%s" % (self.variant, len(salt), self.rounds, data)

    #=========================================================
    #backend
    #=========================================================

    def calc_checksum(self, secret):
        hash, klen = self._info
        if isinstance(secret, unicode):
            secret = secret.encode("utf-8")
        #NOTE: for some reason, FSHP uses pbkdf1 with password & salt reversed.
        #      this has only a minimal impact on security,
        #      but it is worth noting this deviation.
        return pbkdf1(
            secret=self.salt,
            salt=secret,
            rounds=self.rounds,
            keylen=klen,
            hash=hash,
            )

    #=========================================================
    #eoc
    #=========================================================

#=========================================================
#eof
#=========================================================
