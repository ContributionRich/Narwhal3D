"""Required native alias-grammar ABI. Host admission, framing and presentation only.

The native core owns the ``parse_macro`` GRAMMAR (the regex + ``str.split`` +
``str.lstrip`` retired to ``native/pure_core/src/alias_owned.rs``). This module
does no grammar work of its own: it admits the input, frames the ABI call,
validates the reply, and assembles the spans into ``(prefix, command, options)``.

Missing/stale libraries and invalid replies are installation/protocol errors —
they are never replayed in Python. The distinct ``AliasTable`` state, casefold,
defaults, status mapping and CRUD remain in ``alias_table.py`` (a separate task);
this module owns the parse grammar alone.
"""
import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys
import threading
import unicodedata

__all__ = ['parse_macro']


@lru_cache(maxsize=16)
def _array_type(length):
    # Cache only immutable ctypes types, never caller data or native results.
    return ctypes.c_uint32 * length


class AliasReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('prefix_start', ctypes.c_uint32), ('prefix_end', ctypes.c_uint32),
                ('command_start', ctypes.c_uint32), ('command_end', ctypes.c_uint32),
                ('option_count', ctypes.c_uint32)]


# Reply codes (alias_owned.rs).
_CODE_OK = 0
_CODE_INVALID = 1
_CODE_BOUND = 2

_bound = None
_signature = None
_library = None
_cache = None
_lock = threading.Lock()
_native_alias_calls = 0


def _required_binding():
    global _bound, _signature, _library, _cache
    explicit = os.environ.get('NARWHAL_PURE_CORE')
    signature = explicit or None  # None distinguishes packaged from every path.
    cached = _cache
    if (cached is not None and cached[0] == signature
            and _bound is cached[1] and _signature == signature):
        return cached[1]
    with _lock:
        cached = _cache
        if (cached is not None and cached[0] == signature
                and _bound is cached[1] and _signature == signature):
            return cached[1]
        suffix = ('narwhal_pure_core.dll' if sys.platform == 'win32' else
                  'libnarwhal_pure_core.dylib' if sys.platform == 'darwin' else
                  'libnarwhal_pure_core.so')
        path = (Path(explicit).expanduser() if explicit else
                Path(__file__).resolve().parent / 'native' / suffix)
        _bound = _library = _signature = None
        try:
            library = ctypes.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes = []
            core.restype = ctypes.c_uint32
            if core() != 1:
                raise RuntimeError('Stale Rust alias core ABI; not replayed')
            identity = library.narwhal_pure_core_alias_identity
            identity.argtypes = []
            identity.restype = ctypes.c_uint32
            if identity() != 1:
                raise RuntimeError('Stale Rust alias ABI; not replayed')
            # The primary dependency is `alias_parse` alone; it returns the
            # option count AND fills the option span array, so the companion
            # `alias_options` function is not bound here (it remains a Rust
            # compatibility export, not a host requirement).
            binding = library.narwhal_pure_core_alias_parse
            point = ctypes.POINTER(ctypes.c_uint32)
            binding.argtypes = [point, ctypes.c_size_t, ctypes.c_uint32,
                                ctypes.POINTER(AliasReply), ctypes.c_size_t,
                                point, ctypes.c_size_t]
            binding.restype = ctypes.c_uint32
        except (OSError, AttributeError) as error:
            raise RuntimeError('Required Rust alias ABI unavailable; not replayed') from error
        _signature, _library, _bound = signature, library, binding
        _cache = (signature, binding, library)
        return binding


def _buffer(value):
    # Admission only: the native bound check precedes reading the span.
    encoding = 'utf-32-le' if sys.byteorder == 'little' else 'utf-32-be'
    return _array_type(len(value)).from_buffer_copy(value.encode(encoding, 'surrogatepass'))


def _validate_reply(reply, n, source):
    """Pure ABI structure admission — NO grammar/regex logic in Python.

    Raises ``RuntimeError`` on any malformed metadata; the counter is
    incremented only by the caller AFTER this check passes.

    A prefix span of (0, 1) is only legal when the actual input starts
    with one of the legal prefix characters (the set the native grammar
    uses); a shape check alone would let a hostile native claim a prefix
    for any input, so the first codepoint is validated against the span.
    """
    if reply.reserved != 0:
        raise RuntimeError('Invalid Rust alias reply (reserved=%d); not replayed' % (reply.reserved,))
    if reply.code == _CODE_OK:
        # Prefix: absent (0, 0), or exactly span (0, 1) AND the actual
        # first codepoint is a legal prefix character.
        if reply.prefix_start != 0:
            raise RuntimeError('Invalid Rust alias reply (prefix start); not replayed')
        if reply.prefix_end == 0:
            return
        if reply.prefix_end != 1:
            raise RuntimeError('Invalid Rust alias reply (prefix end); not replayed')
        first = source[0]
        if first not in (0x27, 0x21, 0x5F):  # ' ! _
            raise RuntimeError(
                'Invalid Rust alias reply (prefix not first codepoint); not replayed')
        return
    if reply.code == _CODE_INVALID:
        # An invalid reply carries NO metadata at all.
        for field in ('prefix_start', 'prefix_end', 'command_start',
                      'command_end', 'option_count'):
            if getattr(reply, field) != 0:
                raise RuntimeError('Invalid Rust alias reply (invalid code with %s); not replayed' % (field,))
        return
    raise RuntimeError('Invalid Rust alias reply (code=%d); not replayed' % (reply.code,))


def _validate_option_spans(options, count, n, command_end):
    """Bounded, ordered, non-overlapping option spans after the command."""
    previous_end = command_end
    for i in range(count):
        start = options[i * 2]
        end = options[i * 2 + 1]
        if start > end or end > n:
            raise RuntimeError('Invalid Rust alias reply (option span %d); not replayed' % (i,))
        if start < previous_end:
            raise RuntimeError('Invalid Rust alias reply (option span %d overlaps); not replayed' % (i,))
        previous_end = end


def parse_macro(macro):
    """Parse a macro into ``(prefix, command, options)`` via the native grammar.

    Return ``(None, None, None)`` when the value does not follow the supported
    form. Falsy input (``None``, ``0``, ``False``, ``[]``, ``''``) returns the
    invalid tuple, matching the reference ``macro or ''``. A truthy non-string
    raises ``TypeError``, matching the reference ``re.match`` admission.
    A string beyond the 4096-codepoint bound raises ``RuntimeError`` BEFORE
    any buffer allocation or binding (OURS safety limit, never replayed).
    """
    if not macro:
        return None, None, None
    if not isinstance(macro, str):
        raise TypeError('macro must be a string, got %r' % (type(macro).__name__,))
    if unicodedata.unidata_version != '15.1.0':
        raise RuntimeError('Unsupported Unicode database for Rust alias grammar (requires 15.1)')
    n = len(macro)
    if n > 4096:
        raise RuntimeError('alias input exceeds 4096 codepoints; not replayed')
    binding = _required_binding()
    source = _buffer(macro)
    reply = AliasReply(code=0xffffffff)
    # Each option is at least one codepoint, so the option count is at most n;
    # allocate n pairs (2n u32s) to be safe.
    opt_len = n * 2
    opts_buf = _array_type(opt_len)()
    global _native_alias_calls
    try:
        rc = binding(source, n, 151, ctypes.byref(reply), ctypes.sizeof(reply), opts_buf, opt_len)
    except Exception as error:
        raise RuntimeError('Rust alias call failed; not replayed') from error
    if rc != 0:
        if rc == 2:
            raise RuntimeError('alias input exceeds 4096 codepoints; not replayed')
        raise RuntimeError('Invalid Rust alias reply (rc=%d); not replayed' % (rc,))
    _validate_reply(reply, n, source)
    if reply.code == _CODE_INVALID:
        # Validated (zero metadata): one native operation, no fallback.
        _native_alias_calls += 1
        return None, None, None
    # Command must be a non-empty span inside the input, after the prefix.
    if not (0 <= reply.command_start <= reply.command_end <= n
            and reply.command_start < reply.command_end
            and reply.command_start >= reply.prefix_end):
        raise RuntimeError('Invalid Rust alias reply (command span); not replayed')
    if reply.option_count * 2 > opt_len:
        raise RuntimeError('Invalid Rust alias reply (option count); not replayed')
    _validate_option_spans(opts_buf, reply.option_count, n, reply.command_end)
    # Fully validated (OK): one native operation, no Python fallback ran.
    _native_alias_calls += 1
    prefix = (macro[reply.prefix_start:reply.prefix_end]
              if reply.prefix_start != reply.prefix_end else None)
    command = macro[reply.command_start:reply.command_end]
    options = tuple(
        macro[opts_buf[i * 2]:opts_buf[i * 2 + 1]]
        for i in range(reply.option_count)
    )
    return prefix, command, options
