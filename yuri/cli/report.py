"""Best-effort reports of the values the core detected on its own.

The goal is to give the user the numbers they may need to pass to ``compile``
(``--ver``, ``--key``, ``--t-ver``, ``--w-ver``). Everything here is read-only
and never fails the command: if something cannot be read, a warning is printed.
"""
from __future__ import annotations

import struct
import sys
from os import listdir, path


def _warn(msg: str) -> None:
    print(f'warning: {msg}', file=sys.stderr)


def _first_script(iroot: str) -> str | None:
    """Return the first script candidate with a complete YSTB header, or None."""
    try:
        names = sorted(n for n in listdir(iroot)
                       if n.startswith('yst') and n.endswith('.ybn') and n != 'yst_list.ybn')
    except OSError:
        return None
    for name in names:
        try:
            with open(path.join(iroot, name), 'rb') as fp:
                head = fp.read(32)  # YSTB header: <4s7I
        except OSError:
            continue
        if len(head) == 32 and head[:4] == b'YSTB':
            return name
    return None


def header_versions(iroot: str) -> dict[str, int]:
    """Version stored in the header (4-byte magic + u32) of the main YBN files."""
    names = ['ysc.ybn', 'ysv.ybn', 'ysl.ybn', 'yst_list.ybn']
    if script := _first_script(iroot):  # one valid-looking script header is enough
        names.append(script)
    found: dict[str, int] = {}
    for name in names:
        try:
            with open(path.join(iroot, name), 'rb') as fp:
                _, ver = struct.unpack('<4sI', fp.read(8))
        except (OSError, struct.error):
            continue
        found[name] = ver
    return found


def log_headers(iroot: str, kwargs: dict) -> None:
    """Before decompiling: show the versions in the YBN headers and warn about a forced --ver."""
    found = header_versions(iroot)
    if not found:
        _warn(f'could not read the version from the YBN headers in {iroot!r}')
        return
    versions = sorted(set(found.values()))
    print('YBN header version(s): ' + ', '.join(map(str, versions)))
    if len(versions) > 1:
        print('  ' + ', '.join(f'{n}={v}' for n, v in found.items()))
    given = kwargs.get('ver')
    if given is not None and any(v != given for v in versions):
        _warn(f'--ver {given} differs from the header version(s) {versions}. This is expected for '
              f'custom builds; if decompilation fails with an assertion error, try without --ver')


def _probe(iroot: str, kwargs: dict) -> tuple[int, int]:
    """Read one script like yuridec.run does and return the (version, key) actually used."""
    from yuri.fileformat import YSCM, YSTB, Rdr, CP932  # late import: only when used
    ver, key = kwargs.get('ver'), kwargs.get('key')
    with open(path.join(iroot, 'ysc.ybn'), 'rb') as fp:
        yscm = YSCM.read(Rdr.from_bio(fp, CP932), v=ver)
    script = _first_script(iroot)
    if script is None:
        raise FileNotFoundError('no valid yst*.ybn script found (expected YSTB magic and a complete header)')
    try:
        with open(path.join(iroot, script), 'rb') as fp:
            ystb = YSTB.read(
                fp,
                yscm.cmdcodes,
                key=key,
                v=ver,
                enc=kwargs.get('ienc', CP932),
                word_enc=kwargs.get('word_enc'),
            )
    except Exception as e:
        e.add_note(f'while reading {script}')
        raise
    return ystb.ver, ystb.key


def log_values(iroot: str, kwargs: dict, compile_names: set[str]) -> None:
    """After decompiling: show the version and XOR key in use, and suggest options for compile."""
    try:
        ver, key = _probe(iroot, kwargs)
    except Exception as e:
        notes = ''.join(f' [{n}]' for n in getattr(e, '__notes__', []))
        _warn(f'could not determine the YBN version and XOR key in use ({type(e).__name__}: {e}){notes}')
        return
    print('Values for compile:')
    print(f'  YBN version in use: {ver} ' + ('(given with --ver)' if kwargs.get('ver') is not None
                                             else '(read from the file header)'))
    print(f'  XOR key in use:     0x{key:08X} ' + ('(given with --key)' if kwargs.get('key') is not None
                                                   else "(the default key for this version)"))
    opts = {'ver': ver, 'key': f'0x{key:08X}'}
    headers = set(header_versions(iroot).values())
    if len(headers) == 1 and headers != {ver}:  # custom build: headers report another version
        opts.update(t_ver=ver, w_ver=next(iter(headers)))
    shown = [f'--{n.replace("_", "-")} {v}' for n, v in opts.items() if n in compile_names]
    if shown:
        print('  Suggested options: ' + ' '.join(shown))
