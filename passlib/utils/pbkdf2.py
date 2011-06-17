"""passlib.pbkdf2 - PBKDF2 support

this module is getting increasingly poorly named.
maybe rename to "kdf" since it's getting more key derivation functions added.
"""
#=================================================================================
#imports
#=================================================================================
#core
from binascii import unhexlify
import hashlib
import hmac
import logging; log = logging.getLogger(__name__)
import re
from struct import pack
from warnings import warn
#site
try:
    from M2Crypto import EVP as _EVP
except ImportError:
    _EVP = None
#pkg
from passlib.utils import xor_bytes, to_bytes, native_str, b, bytes
#local
__all__ = [
    "hmac_sha1",
    "get_prf",
    "pbkdf1",
    "pbkdf2",
]

# Py2k #
from cStringIO import StringIO as BytesIO
# Py3k #
#from io import BytesIO
# end Py3k #

#=================================================================================
#quick hmac_sha1 implementation used various places
#=================================================================================
def hmac_sha1(key, msg):
    "perform raw hmac-sha1 of a message"
    return hmac.new(key, msg, hashlib.sha1).digest()

if _EVP:
    #default *should* be sha1, which saves us a wrapper function, but might as well check.
    try:
        result = _EVP.hmac(b('x'),b('y'))
    except ValueError: #pragma: no cover
        #this is probably not a good sign if it happens.
        warn("PassLib: M2Crypt.EVP.hmac() unexpected threw value error during passlib startup test")
    else:
        if result == b(',\x1cb\xe0H\xa5\x82M\xfb>\xd6\x98\xef\x8e\xf9oQ\x85\xa3i'):
            hmac_sha1 = _EVP.hmac

#=================================================================================
#general prf lookup
#=================================================================================
def _get_hmac_prf(digest):
    "helper to return HMAC prf for specific digest"
    #check if m2crypto is present and supports requested digest
    if _EVP:
        try:
            result = _EVP.hmac(b('x'), b('y'), digest)
        except ValueError:
            pass
        else:
            #it does. so use M2Crypto's hmac & digest code
            hmac_const = _EVP.hmac
            def prf(key, msg):
                "prf(key,msg)->digest; generated by passlib.utils.pbkdf2.get_prf()"
                return hmac_const(key, msg, digest)
            prf.__name__ = "hmac_" + digest
            digest_size = len(result)
            return prf, digest_size

    #fall back to stdlib implementation
    digest_const = getattr(hashlib, digest, None)
    if not digest_const:
        raise ValueError("unknown hash algorithm: %r" % (digest,))
    digest_size = digest_const().digest_size
    hmac_const = hmac.new
    def prf(key, msg):
        "prf(key,msg)->digest; generated by passlib.utils.pbkdf2.get_prf()"
        return hmac_const(key, msg, digest_const).digest()
    prf.__name__ = "hmac_" + digest
    return prf, digest_size

#cache mapping prf name/func -> (func, digest_size)
_prf_cache = {}

def _clear_prf_cache():
    "helper for unit tests"
    _prf_cache.clear()

def get_prf(name):
    """lookup pseudo-random family (prf) by name.
    
    :arg name:
        this must be the name of a recognized prf.
        currently this only recognizes names with the format
        :samp:`hmac-{digest}`, where :samp:`{digest}`
        is the name of a hash function such as
        ``md5``, ``sha256``, etc.
        
        this can also be a callable with the signature
        ``prf(secret, message) -> digest``,
        in which case it will be returned unchanged.
        
    :raises ValueError: if the name is not known
    :raises TypeError: if the name is not a callable or string
    
    :returns:
        a tuple of :samp:`({func}, {digest_size})`.
        
        * :samp:`{func}` is a function implementing
          the specified prf, and has the signature
          ``func(secret, message) -> digest``.
          
        * :samp:`{digest_size}` is an integer indicating
          the number of bytes the function returns.
          
    usage example::
    
        >>> from passlib.utils.pbkdf2 import get_prf
        >>> hmac_sha256, dsize = get_prf("hmac-sha256")
        >>> hmac_sha256
        <function hmac_sha256 at 0x1e37c80>
        >>> dsize
        32
        >>> digest = hmac_sha256('password', 'message')

    this function will attempt to return the fastest implementation
    it can find; if M2Crypto is present, and supports the specified prf, 
    :func:`M2Crypto.EVP.hmac` will be used behind the scenes.
    """
    global _prf_cache
    if name in _prf_cache:
        return _prf_cache[name]
    if isinstance(name, native_str):
        if name.startswith("hmac-") or name.startswith("hmac_"):
            retval = _get_hmac_prf(name[5:])
        else:
            raise ValueError("unknown prf algorithm: %r" % (name,))
    elif callable(name):
        #assume it's a callable, use it directly
        digest_size = len(name(b('x'),b('y')))
        retval = (name, digest_size)
    else:
        raise TypeError("prf must be string or callable")
    _prf_cache[name] = retval
    return retval

#=================================================================================
#pbkdf1 support
#=================================================================================
def pbkdf1(secret, salt, rounds, keylen, hash="sha1", encoding="utf8"):
    """pkcs#5 password-based key derivation v1.5

    :arg secret: passphrase to use to generate key
    :arg salt: salt string to use when generating key
    :param rounds: number of rounds to use to generate key
    :arg keylen: number of bytes to generate
    :param hash:
        hash function to use.
        if specified, it must be one of the following:

        * a callable with the prototype ``hash(message) -> raw digest``
        * a string matching one of the hashes recognized by hashlib

    :param encoding:
        encoding to use when converting unicode secret and salt to bytes.
        defaults to ``utf8``.
        
    :returns:
        raw bytes of generated key
    
    This algorithm is deprecated, new code should use PBKDF2.
    Among other reasons, ``keylen`` cannot be larger
    than the digest size of the specified hash.
    
    """
    #prepare secret & salt
    secret = to_bytes(secret, encoding, errname="secret")
    salt = to_bytes(salt, encoding, errname="salt")

    #prepare rounds
    if not isinstance(rounds, (int, long)):
        raise TypeError("rounds must be an integer")
    if rounds < 1:
        raise ValueError("rounds must be at least 1")

    #prep keylen
    if keylen < 0:
        raise ValueError("keylen must be at least 0")

    #resolve hash
    if isinstance(hash, native_str):
        #check for builtin hash
        hf = getattr(hashlib, hash, None)
        if hf is None:
            #check for ssl hash
            #NOTE: if hash unknown, will throw ValueError, which we'd just
            # reraise anyways; so instead of checking, we just let it get
            # thrown during first use, below
            def hf(msg):
                return hashlib.new(hash, msg)

    #run pbkdf1
    block = hf(secret + salt).digest()
    if keylen > len(block):
        raise ValueError, "keylength too large for digest: %r > %r" % (keylen, len(block))
    r = 1
    while r < rounds:
        block = hf(block).digest()
        r += 1
    return block[:keylen]
    
#=================================================================================
#pbkdf2
#=================================================================================
MAX_BLOCKS = 0xffffffff #2**32-1
MAX_HMAC_SHA1_KEYLEN = MAX_BLOCKS*20

def pbkdf2(secret, salt, rounds, keylen, prf="hmac-sha1", encoding="utf8"):
    """pkcs#5 password-based key derivation v2.0

    :arg secret: passphrase to use to generate key
    :arg salt: salt string to use when generating key
    :param rounds: number of rounds to use to generate key
    :arg keylen: number of bytes to generate
    :param prf:
        psuedo-random family to use for key strengthening.
        this can be any string or callable accepted by :func:`get_prf`.
        this defaults to ``hmac-sha1`` (the only prf explicitly listed in
        the PBKDF2 specification)
    :param encoding:
        encoding to use when converting unicode secret and salt to bytes.
        defaults to ``utf8``.
        
    :returns:
        raw bytes of generated key
    """
    #prepare secret & salt
    secret = to_bytes(secret, encoding, errname="secret")
    salt = to_bytes(salt, encoding, errname="salt")

    #prepare rounds
    if not isinstance(rounds, (int, long)):
        raise TypeError("rounds must be an integer")
    if rounds < 1:
        raise ValueError("rounds must be at least 1")

    #special case for m2crypto + hmac-sha1
    if prf == "hmac-sha1" and _EVP:
        #NOTE: doing check here, because M2crypto won't take longs (which this is, under 32bit)
        if keylen > MAX_HMAC_SHA1_KEYLEN:
            raise ValueError("key length too long")
        
        #NOTE: M2crypto reliably segfaults for me if given keylengths
        # larger than 40 (crashes at 41 on one system, 61 on another).
        # so just avoiding it for longer calls.
        if keylen < 41:
            return _EVP.pbkdf2(secret, salt, rounds, keylen)

    #resolve prf
    encode_block, digest_size = get_prf(prf)

    #figure out how many blocks we'll need
    bcount = (keylen+digest_size-1)//digest_size
    if bcount >= MAX_BLOCKS:
        raise ValueError("key length to long")

    #build up key from blocks
    out = BytesIO()
    write = out.write
    for i in xrange(1,bcount+1):
        block = tmp = encode_block(secret, salt + pack(">L", i))
        #NOTE: could potentially unroll this loop somewhat for speed,
        # or find some faster way to accumulate & xor tmp values together
        j = 1
        while j < rounds:
            tmp = encode_block(secret, tmp)
            block = xor_bytes(block, tmp)
            j += 1
        write(block)

    #and done
    return out.getvalue()[:keylen]

#=================================================================================
#eof
#=================================================================================
