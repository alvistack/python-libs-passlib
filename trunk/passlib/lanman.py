"""

the LMHASH and NTHASH algorithms are used in various windows related contexts,
but generally not in a manner compatible with how passlib operates.
in particular, they have no identifying marks, both being
32 bytes of binary data. thus, they can't be easily identified
in a context with other hashes, so a CryptHandler hasn't been defined for them.

this module only defines the lmhash() and nthash() functions for easy use.
"""
from binascii import hexlify
from passlib.utils.des import des_encrypt_block
from passlib.utils.md4 import md4

LM_MAGIC = "KGS!@#$%"

def lmhash(secret):
    #XXX: encoding should be oem ascii
    ns = secret.upper()[:14] + "\x00" * (14-len(secret))
    return hexlify(des_encrypt_block(ns[:7], LM_MAGIC) + des_encrypt_block(ns[7:], LM_MAGIC))

def nthash(secret):
    return md4(secret.encode("utf-16le")).hexdigest()

###hashes from http://msdn.microsoft.com/en-us/library/cc245828(v=prot.10).aspx
### among other places
##for secret, hash in [
##    ("OLDPASSWORD", "c9b81d939d6fd80cd408e6b105741864"),
##    ("NEWPASSWORD", '09eeab5aa415d6e4d408e6b105741864'),
##    ("welcome", "c23413a8a1e7665faad3b435b51404ee"),
##    ]:
##
##    print secret, lmhash(secret), hash == lmhash(secret)

##for secret, hash in [
##    ("OLDPASSWORD", "6677b2c394311355b54f25eec5bfacf5"),
##    ("NEWPASSWORD", "256781a62031289d3c2c98c14f1efc8c"),
##    ]:
##    print secret, lmhash(secret), hash == nthash(secret)