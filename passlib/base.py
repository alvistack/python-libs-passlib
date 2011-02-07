"""passlib - implementation of various password hashing functions

Context options

    schemes - list of names or handler instances which should be recognized by context
    deprecated - list names of handlers which should context should only use to validate *old* hashes
    default - optional name of handler to use for encrypting new hashes.

Many schemes support their own options, such as min/max/default rounds.

"""
#=========================================================
#imports
#=========================================================
from __future__ import with_statement
#core
from cStringIO import StringIO
from ConfigParser import ConfigParser
import inspect
import re
import hashlib
import logging; log = logging.getLogger(__name__)
import time
import os
from warnings import warn
#site
from pkg_resources import resource_string
#libs
import passlib.hash as _hmod
from passlib.utils import abstractclassmethod, Undef, is_crypt_handler, splitcomma, rng
#pkg
#local
__all__ = [
    #global registry
    'register_crypt_handler',
    'get_crypt_handler',
    'list_crypt_handlers'

    #contexts
    'CryptContext',
]

#=========================================================
#global registry
#=========================================================

#list of builtin hashes (for list_crypt_handlers, to work around lazy loading)
#XXX: could write some code in setup.py that generates this from package listing.
_builtin_names = set([
    "apr_md5_crypt", "bcrypt", "des_crypt", "ext_des_crypt",
    "md5_crypt", "mysql_323", "mysql_41", "postgres_md5",
    "sha256_crypt", "sha512_crypt", "sun_md5_crypt",
    ])

def register_crypt_handler(obj, force=False):
    "register CryptHandler handler"
    global _hmod

    #validate obj
    if not is_crypt_handler(obj):
        raise TypeError, "object does not appear to be a CryptHandler: %r" % (obj,)
    assert obj, "CryptHandlers must be boolean True: %r" % (obj,)

    #validate name
    name = obj.name
    if not name:
        raise ValueError, "name is null: %r" % (name,)
    if name.lower() != name:
        raise ValueError, "name must be lower-case: %r" % (name,)
    if re.search("[^_a-z0-9]",name):
        raise ValueError, "invalid characters in name (only underscore, a-z, 0-9 allowed): %r" % (name,)

    #check for existing handler
    other = getattr(_hmod, name, None)
    if other:
        if other is obj:
            return #already registered
        if force:
            log.warning("overriding previous handler registered to name %r: %r", name, other)
        else:
            raise ValueError, "handler already registered for name %r: %r" % (name, other)

    #put handler into hash module
    setattr(_hmod, name, obj)
    log.info("registered crypt handler %r: %r", name, obj)

def get_crypt_handler(name, default=Undef):
    "resolve crypt algorithm name"
    global _hmod

    #normalize name
    alt = name.replace("-","_").lower()
    if alt != name:
        warn("handler names be lower-case, and use underscores instead of hyphens: %r => %r" % (name, alt))
        name = alt

    #check if handler loaded
    handler = getattr(_hmod, name, None)
    if handler:
        return handler

    #try to lazy load from passlib.hash.xxx
    try:
        mod = __import__("passlib.hash." + name, None, None, ['dummy'], 0)
    except ImportError, err:
        #make sure we don't hide failure to import dependancy
        if str(err) != "No module named " + name:
            raise
    else:
        #<mod> should now be value store in _hmod.name,
        #we call register_crypt_handler() as sanity check to be sure.
        #(will raise ValueError/TypeError if something went wrong)
        register_crypt_handler(mod)
        return mod

    #fail!
    if default is Undef:
        raise KeyError, "no crypt handler found for algorithm: %r" % (name,)
    else:
        return default

def list_crypt_handlers():
    "return sorted list of all known crypt algorithm names"
    global _hmod, _builtin_names
    return sorted(_builtin_names.union(x for x in dir(_hmod) if not x.startswith("_")))

#=========================================================
#policy
#=========================================================
def parse_policy_key(key):
    "helper to normalize & parse policy keys; returns ``(category, name, option)``"
    ##if isinstance(k, tuple) and len(k) == 3:
    ##    cat, name, opt = k
    ##else:
    orig = key
    if '/' in key: #legacy format
        key = key.replace("/",".")
    elif '.' not in key and '__' in key: #lets user specifiy programmatically (since python doesn't allow '.')
        key = key.replace("__", ".")
    key = key.replace(" ","").replace("\t","") #strip out all whitespace from key
    parts = key.split(".")
    if len(parts) == 1:
        cat = None
        name = "context"
        opt, = parts
    elif len(parts) == 2:
        cat = None
        name, opt = parts
    elif len(parts) == 3:
        cat, name, opt = parts
    else:
        raise KeyError, "keys must have 0..2 separators: %r" % (orig,)
    if cat == "default":
        cat = None
    assert name
    assert opt
    return cat, name, opt

def parse_policy_value(cat, name, opt, value):
    "helper to parse policy values"
    #FIXME: kinda primitive :|
    if name == "context":
        if opt == "schemes":
            if isinstance(value, str):
                return splitcomma(value)
        elif opt == "deprecated":
            if isinstance(value, str):
                return set(splitcomma(value))
            elif isinstance(value, (list,tuple)):
                return set(value)
        elif opt == "min_verify_time":
            return float(value)
        return value
    else:
        #try to coerce everything to int
        try:
            return int(value)
        except ValueError:
            return value

class CryptPolicy(object):
    """stores configuration options for a CryptContext object."""

    #=========================================================
    #class methods
    #=========================================================
    @classmethod
    def from_path(cls, path, section="passlib"):
        "create new policy from specified section of an ini file"
        p = ConfigParser()
        if not p.read([path]):
            raise EnvironmentError, "failed to read config file"
        return cls(**dict(p.items(section)))

    @classmethod
    def from_string(cls, source, section="passlib"):
        p = ConfigParser()
        b = StringIO(source)
        p.readfp(b)
        return cls(**dict(p.items(section)))

    @classmethod
    def from_source(cls, source):
        "helper which accepts CryptPolicy, filepath, raw string, and returns policy"
        if isinstance(source, CryptPolicy):
            return source
        if '\n' in source:
            return cls.from_string(source)
        else:
            return cls.from_path(source)

    @classmethod
    def from_sources(cls, sources):
        "create new policy from list of existing policy object"
        if len(sources) == 0:
            raise ValueError, "no sources specified"
        first = sources[0]
        if len(sources) == 1:
            return CryptPolicy.from_source(first)
        if isinstance(first, CryptPolicy):
            target = CryptPolicy()
        else:
            sources = sources[1:]
            target = cls.from_source(first)
        raise NotImplementedError
        ##for source in sources:
        ##    source = cls.from_source(source)
        ##    #TODO: merge handlers.
        ##    target._fallback.update(source._fallback) #FIXME: do we want to override explicitly chosen fallbacks w/ default ones?
        ##    target._deprecated = set(source._deprecated)
        return target

    #=========================================================
    #instance attrs
    #=========================================================
    #NOTE: all category dictionaries below will have a minimum of 'None' as a key

    #:list of all handlers, in order they will be checked when identifying (reverse of order specified)
    _handlers = None #list of password hash handlers instances.

    #:dict mapping category -> fallback handler for that category
    _fallback = None

    #:dict mapping category -> set of handler names which are deprecated for that category
    _deprecated = None

    #:dict mapping category -> min verify time
    _min_verify_time = None

    #:dict mapping category -> dict mapping hash name -> dict of options for that hash
    # if a category is specified, particular hash names will be mapped ONLY if that category
    # has options which differ from the default options.
    _options = None

    #=========================================================
    #init
    #=========================================================
    def __init__(self, **kwds):
        self._from_dict(**kwds)

    #=========================================================
    #internal init helpers
    #=========================================================
    def _from_dict(self, **kwds):
        #
        #normalize & sort keywords
        #
        options = self._options = {None:{"context":{}}}
        for k,v in kwds.iteritems():
            cat,name,opt = parse_policy_key(k)
            if name == "context":
                if cat and opt == "schemes":
                    raise NotImplementedError, "current code does not support per-category schemes"
                    #NOTE: forbidding this because it would really complicate the behavior
                    # of CryptContext.identify & CryptContext.lookup.
                    # most useful behaviors here can be had by overridding deprecated and default, anyways.
            else:
                if opt == "salt":
                    raise KeyError, "'salt' option is not allowed to be set via a policy object"
                    #NOTE: doing this for security purposes, why would you ever want a fixed salt?
            v = parse_policy_value(cat, name, opt, v)
            copts = options.get(cat)
            if copts is None:
                copts = options[cat] = {}
            config = copts.get(name)
            if config is None:
                copts[name] = {opt:v}
            else:
                config[opt] = v

        #
        #parse list of schemes, and resolve to handlers.
        #
        handlers = self._handlers = []
        seen = set()
        schemes = options[None]['context'].get("schemes") or []
        for scheme in reversed(schemes): #NOTE: reversed() just so last entry is used as default, and is checked first.
            #resolve & validate handler
            if is_crypt_handler(scheme):
                handler = scheme
            else:
                handler = get_crypt_handler(scheme)
            name = handler.name
            if not name:
                raise KeyError, "handler lacks name: %r" % (handler,)

            #check name hasn't been re-used
            if name in seen:
                raise KeyError, "multiple handlers with same name: %r" % (name,)
            seen.add(name)

            #add to handler list
            handlers.append(handler)

        #
        #build _deprecated & _fallback maps
        #
        dmap = self._deprecated = {}
        fmap = self._fallback = {}
        mvmap = self._min_verify_time = {}
        for cat, config in options.iteritems():
            kwds = config.pop("context", None)
            if not kwds:
                continue
            deps = kwds.get("deprecated")
            if deps:
                for scheme in deps:
                    if scheme not in seen:
                        raise ValueError, "unspecified scheme in deprecated list: %r" % (scheme,)
                dmap[cat] = deps
            fb = kwds.get("fallback")
            if fb:
                if fb not in seen:
                    raise ValueError, "unspecified scheme set as fallback: %r" % (fb,)
                fmap[cat] = self.lookup(fb, required=True)
            value = kwds.get("min_verify_time")
            if value:
                mvmap[cat] = value
        if None not in dmap:
            dmap[None] = set()
        if None not in fmap and handlers:
            fmap[None] = handlers[0]
        if None not in mvmap:
            mvmap[None] = 0

    #=========================================================
    #public interface (used by CryptContext)
    #=========================================================
    def lookup(self, name=None, category=None, required=False):
        """given an algorithm name, return CryptHandler instance which manages it.
        if no match is found, returns None.

        if name is None, will return handler for default scheme
        """
        if name:
            for handler in self._handlers:
                if handler.name == name:
                    return handler
        else:
            fmap = self._fallback
            if category and category in fmap:
                return fmap[category]
            if None not in fmap:
                raise KeyError, "no crypt algorithms supported"
            return fmap[None]
        if required:
            raise KeyError, "no crypt algorithm by that name: %r" % (name,)
        return None

    def get_options(self, name, category=None):
        "return dict of options attached to specified hash"
        #TODO: pre-calculate or at least cache some of this.
        options = self._options

        #start with default values
        kwds = options[None].get("default") or {}

        #mix in category default values
        if category and category in options:
            tmp = options[category].get("default")
            if tmp:
                kwds.update(tmp)

        #mix in hash-specific options
        tmp = options[None].get(name)
        if tmp:
            kwds.update(tmp)

        #mix in category hash-specific options
        if category and category in options:
            tmp = options[category].get(name)
            if tmp:
                kwds.update(tmp)

        return kwds

    def is_deprecated(self, name, category=None):
        "check if algorithm is deprecated according to policy"
        if hasattr(name, "name"):
            name = name.name
        dmap = self._deprecated
        if category and category in dmap:
            return name in dmap[category]
        return name in dmap[None]

    def get_min_verify_time(self, category=None):
        mvmap = self._min_verify_time
        if category and category in mvmap:
            return mvmap[category]
        return mvmap[None]

    #=========================================================
    #serialization
    #=========================================================
    ##def get(self):
    ##    "get configuration as dictionary"
    ##    out = {}
    ##    out['schemes'] =[
    ##        handler.name if get_crypt_handler(handler.name,None) is handler else handler
    ##        for handler in self._handlers
    ##    ]
    ##    if self._dset:
    ##        out['deprecated'] = [h.name for h in self._dset]
    ##    if self._default is not self._handlers[0]:
    ##        out['default'] = self._default.name
    ##    for name, opts in self._config:
    ##        for k,v in opts.iteritems():
    ##            out["%s__%s" % (name, k)] = v
    ##    return out
    ##
    ##def write_config_to_file(self, path, section="passlib"):
    ##    "save context configuration to section of ConfigParser file"
    ##    p = ConfigParser()
    ##    if os.path.exists(path):
    ##        if not p.read([path]):
    ##            raise EnvironmentError, "failed to read config file"
    ##        p.remove_section(section)
    ##    for k,v in self.get_config().items():
    ##        if k == "schemes":
    ##            if any(hasattr(h,"name") for h in v):
    ##                raise ValueError, "can't write to config file, unregistered handlers in use"
    ##        if k in ["schemes", "deprecated"]:
    ##            v = ", ".join(v)
    ##        k = k.replace("__", "/")
    ##        p.set(k, v)
    ##    fh = file(path, "w")
    ##    p.write(fh)
    ##    fh.close()

    #=========================================================
    #eoc
    #=========================================================

##default_policy = CryptPolicy.from_string(resource_string("passlib", "default.cfg"))
default_policy = None

#=========================================================
#
#=========================================================
class CryptContext(object):
    """Helper for encrypting passwords using different algorithms.

    Different storage contexts (eg: linux shadow files vs openbsd shadow files)
    may use different sets and subsets of the available algorithms.
    This class encapsulates such distinctions: it represents an ordered
    list of algorithms, each with a unique name. It contains methods
    to verify against existing algorithms in the context,
    and still encrypt using new algorithms as they are added.

    Because of all of this, it's basically just a list object.
    However, it contains some dictionary-like features
    such as looking up algorithms by name, and it's restriction
    that no two algorithms in a list share the same name
    causes it to act more like an "ordered set" than a list.

    In general use, none of this matters.
    The typical use case is as follows::

        >>> from passlib import hash
        >>> #create a new context that only understands Md5Crypt & BCrypt
        >>> myctx = hash.CryptContext([ hash.Md5Crypt, hash.BCrypt ])

        >>> #the last one in the list will be used as the default for encrypting...
        >>> hash1 = myctx.encrypt("too many secrets")
        >>> hash1
        '$2a$11$RvViwGZL./LkWfdGKTrgeO4khL/PDXKe0TayeVObQdoew7TFwhNFy'

        >>> #choose algorithm explicitly
        >>> hash2 = myctx.encrypt("too many secrets", alg="md5-crypt")
        >>> hash2
        '$1$E1g0/BY.$gS9XZ4W2Ea.U7jMueBRVA.'

        >>> #verification will autodetect the right hash
        >>> myctx.verify("too many secrets", hash1)
        True
        >>> myctx.verify("too many secrets", hash2)
        True
        >>> myctx.verify("too many socks", hash2)
        False

        >>> #you can also have it identify the algorithm in use
        >>> myctx.identify(hash1)
        'bcrypt'
        >>> #or just return the CryptHandler instance directly
        >>> myctx.identify(hash1, resolve=True)
        <passlib.BCrypt object, name="bcrypt">

        >>> #you can get a list of algs...
        >>> myctx.keys()
        [ 'md5-crypt', 'bcrypt' ]

        >>> #and get the CryptHandler object by name
        >>> bc = myctx['bcrypt']
        >>> bc
        <passlib.BCrypt object, name="bcrypt">
    """
    #===================================================================
    #instance attrs
    #===================================================================
    policy = None #policy object governing context

    #===================================================================
    #init
    #===================================================================
    def __init__(self, schemes=None, policy=default_policy, **kwds):
        if schemes:
            kwds['schemes'] = schemes
        if not policy:
            policy = CryptPolicy(**kwds)
        elif kwds:
            tmp = CryptPolicy(**kwds)
            if isinstance(policy, list):
                policy = CryptPolicy.from_sources(policy + [tmp])
            else:
                policy = CryptPolicy.from_sources([policy, tmp])
        if not policy._handlers:
            raise ValueError, "at least one scheme must be specified"
        self.policy = policy

    def __repr__(self):
        names = [ handler.name for handler in self.policy._handlers ]
        return "CryptContext(%r)" % (names,)

    #===================================================================
    #policy adaptation
    #===================================================================
    def lookup(self, name=None, category=None, required=False):
        """given an algorithm name, return CryptHandler instance which manages it.
        if no match is found, returns None.

        if name is None, will return default algorithm
        """
        return self.policy.lookup(name, category, required)

    def norm_handler_settings(self, handler, category=None, **settings):
        "normalize settings for handler according to context configuration"
        opts = self.policy.get_options(handler, category)
        if not opts:
            return settings

        #load in default values
        for k in handler.setting_kwds:
            if k not in settings and k in opts:
                settings[k] = opts[k]

        #handle rounds
        if 'rounds' in handler.setting_kwds:
            #TODO: prep-parse & validate this w/in get_options() ?
            mn = opts.get("min_rounds")
            mx = opts.get("max_rounds")
            rounds = settings.get("rounds")
            if rounds is None:
                df = opts.get("default_rounds") or mx or mn
                if df is not None:
                    vr = opts.get("vary_default_rounds")
                    if vr:
                        if isinstance(vr, str) and vr.endswith("%"):
                            ##TODO: detect log rounds, and adjust scale
                            ##vr = int(log(vr*.01*(2**df),2))
                            vr = int(df * vr / 100)
                        rounds = rng.randint(df-vr,df+vr)
                    else:
                        rounds = df
            if rounds is not None:
                if mx and rounds > mx:
                    rounds = mx
                if mn and rounds < mn: #give mn predence if mn > mx
                    rounds = mn
                settings['rounds'] = rounds

        return settings

    def is_compliant(self, hash, category=None):
        """check if hash is allowed by current policy, or if secret should be re-encrypted"""
        handler = self.identify(hash, required=True)
        policy = self.policy

        #check if handler has been deprecated
        if policy.is_deprecated(handler, category):
            return True

        #TODO: check specific policy for hash.
        #need to work up protocol here.
        #probably want to hand off settings to handler.
        #or at least check for parse() support, and read 'rounds' parameter.
        #options = policy.get_options(handler, category)
        return False

    #===================================================================
    #password hash api proxy methods
    #===================================================================
    def genconfig(self, scheme=None, category=None, **settings):
        """Call genconfig() for specified handler"""
        handler = self.lookup(scheme, category, required=True)
        settings = self.norm_handler_settings(handler, category, **settings)
        return handler.genconfig(**settings)

    def genhash(self, config, scheme=None, category=None, **context):
        """Call genhash() for specified handler"""
        #NOTE: this doesn't use category in any way, but accepts it for consistency
        if scheme:
            handler = self.lookup(scheme, required=True)
        else:
            handler = self.identify(config, required=True)
        return handler.genhash(config, **context)

    def identify(self, hash, category=None, name=False, required=False):
        """Attempt to identify which algorithm hash belongs to w/in this context.

        :arg hash:
            The hash string to test.

        :param name:
            If true, returns the name of the handler
            instead of the handler itself.

        All registered algorithms will be checked in from last to first,
        and whichever one claims the hash first will be returned.

        :returns:
            The handler which first identifies the hash,
            or ``None`` if none of the algorithms identify the hash.
        """
        #NOTE: this doesn't use category in any way, but accepts it for consistency
        if hash is None:
            if required:
                raise ValueError, "no hash specified"
            return None
        for handler in self._handlers:
            if handler.identify(hash):
                if name:
                    return handler.name
                else:
                    return handler
        if required:
            raise ValueError, "hash could not be identified"
        return None

    def encrypt(self, secret, scheme=None, category=None, **kwds):
        """encrypt secret, returning resulting hash.

        :arg secret:
            String containing the secret to encrypt

        :param scheme:
            Optionally specify the name of the algorithm to use.
            If no algorithm is specified, an attempt is made
            to guess from the hash string. If no hash string
            is specified, the last algorithm in the list is used.

        :param **kwds:
            All other keyword options are passed to the algorithm's encrypt method.
            The two most common ones are "keep_salt" and "rounds".

        :returns:
            The secret as encoded by the specified algorithm and options.
        """
        if not self:
            raise ValueError, "no algorithms registered"
        handler = self.lookup(scheme, category, required=True)
        kwds = self.norm_handler_settings(handler, category, **kwds)
        return handler.encrypt(secret, **kwds)

    def verify(self, secret, hash, scheme=None, category=None, **context):
        """verify secret against specified hash

        :arg secret:
            the secret to encrypt
        :arg hash:
            hash string to compare to
        :param scheme:
            optional force context to use specfic scheme (must be allowed by context)
        """
        #quick checks
        if not self:
            raise ValueError, "no crypt schemes registered"
        if hash is None:
            return False

        mvt = self.policy.get_min_verify_time(category)
        if mvt:
            start = time.time()

        #locate handler
        if scheme:
            handler = self.lookup(scheme, required=True)
        else:
            handler = self.identify(hash, required=True)

        #strip context kwds if scheme doesn't use them
        ##for k in context.keys():
        ##    if k not in handler.context_kwds:
        ##        del context[k]

        #use handler to verify secret
        result = handler.verify(secret, hash, **context)

        if mvt:
            #delta some amount of time if verify took less than mvt seconds
            delta = time.time() - start - mvt
            if delta > 0:
                time.sleep(delta)

        return result

    #=========================================================
    #eoc
    #=========================================================

#=========================================================
# eof
#=========================================================
