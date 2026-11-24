"""CLI commands: extract, decompile, compile and all (what main.py does)."""
from __future__ import annotations

import argparse
import importlib
import sys
from os import makedirs, path

from . import defaults as D
from . import report
from .params import CliError, Step, format_call

# Where each core function lives. If the core moves things, only this table changes.
CORE = {
    'extract': ('yuri.fileformat', 'ypf_read'),
    'decompile': ('yuri.yuridec', 'run'),
    'compile': ('yuri.yuricom', 'run'),
}
RESERVED = {'-h', '--help', '--ypf-in', '--out-dir', '--dry-run'}


def load_steps() -> dict[str, Step]:
    steps = {}
    for cmd, (module, attr) in CORE.items():
        try:
            fn = getattr(importlib.import_module(module), attr)
        except ImportError as e:
            raise CliError(f'could not import {module} ({e}). Run from the repo root and '
                           f'install: pip install murmurhash2 xor_cipher deflate') from None
        steps[cmd] = Step(fn, skip_first=(cmd == 'extract'))  # the first ypf_read argument is the opened file
    return steps


def add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument('--dry-run', action='store_true',
                    help='print the call that would be made to the core, without running anything')


# --- actions -------------------------------------------------------------------

def detect_ypf_version(read: Step, ypf_in: str) -> int | None:
    kw = {'do_decompress': False} if 'do_decompress' in read.names else {}
    try:
        with open(ypf_in, 'rb') as fp:
            return read.fn(fp, **kw)[1]
    except Exception as e:
        print(f'warning: could not read the YPF version from {ypf_in!r}: {e}', file=sys.stderr)
        return None


def call_core(cmd: str, step: Step, args: tuple, kwargs: dict):
    """Call the core. On failure, add a note about values the core detected on its own."""
    try:
        return step.fn(*args, **kwargs)
    except Exception as e:
        if isinstance(e, OSError):
            raise  # missing files etc. have nothing to do with auto-detection
        if isinstance(e, UnicodeError):
            opts = [f'--{n.replace("_", "-")}' for n in ('ienc', 'word_enc') if n in step.names]
            e.add_note(f'note (cli): a text field could not be decoded. '
                       f'(see {", ".join(opts)}).')
            raise
        auto = [f'--{n.replace("_", "-")}' for n in D.AUTO_DETECTED.get(cmd, ())
                if n in step.names and n not in kwargs]
        if auto:
            e.add_note(f'note (cli): {", ".join(auto)} not given, so the core detected the value '
                       f'automatically. Auto-detection can be wrong for custom builds; '
                       f'try passing it explicitly.')
        raise


def do_extract(read: Step, ypf_in: str, out_dir: str, kwargs: dict, dry: bool) -> int | None:
    if dry:
        print(format_call('ypf_read', kwargs, first=f'f=open({ypf_in!r}, "rb")'))
        print(f'-> would write the files to {out_dir!r}')
        return detect_ypf_version(read, ypf_in)
    with open(ypf_in, 'rb') as fp:
        entries, version = call_core('extract', read, (fp,), kwargs)
    print('The actual version of the YPF file is:', version)
    flag = '--' + D.FROM_INPUT_YPF_VERSION['compile'].replace('_', '-')
    print(f'-> for compile: {flag} {version} (compile reads it from --ypf-in by default)')
    for ent in entries:  # (name, type, compression, data, uncompressed_size)
        name, data = ent[0], ent[3]
        print('Extracted file: ' + name)
        full = path.join(out_dir, *name.split('\\'))
        makedirs(path.dirname(full), exist_ok=True)
        with open(full, 'wb') as out:
            out.write(data)
    return version


def do_run(cmd: str, step: Step, label: str, kwargs: dict, dry: bool) -> None:
    if dry:
        print(format_call(label, kwargs))
    else:
        call_core(cmd, step, (), kwargs)


def do_decompile(steps: dict[str, Step], kwargs: dict, dry: bool) -> None:
    iroot = kwargs.get('iroot')
    if dry or not iroot:
        return do_run('decompile', steps['decompile'], 'yuridec.run', kwargs, dry)
    report.log_headers(iroot, kwargs)
    do_run('decompile', steps['decompile'], 'yuridec.run', kwargs, dry)
    report.log_values(iroot, kwargs, steps['compile'].names)


def with_ypf_version(cmd: str, step: Step, kwargs: dict, version: int | None) -> None:
    """As in main.py: the compiler's ypf_ver = version of the input YPF."""
    name = D.FROM_INPUT_YPF_VERSION.get(cmd)
    if name and name in step.names and name not in kwargs:
        if version is None:
            print(f'warning: {name} not detected; using the core default. '
                  f'Pass --{name.replace("_", "-")} if needed.', file=sys.stderr)
        else:
            kwargs[name] = version


# --- commands ----------------------------------------------------------------

def cmd_extract(ns, steps):
    read = steps['extract']
    kwargs = read.build_kwargs(Step.provided(ns), D.EXTRACT)
    do_extract(read, ns.ypf_in, ns.out_dir, kwargs, ns.dry_run)


def cmd_decompile(ns, steps):
    kwargs = steps['decompile'].build_kwargs(Step.provided(ns), D.DECOMPILE)
    do_decompile(steps, kwargs, ns.dry_run)


def cmd_compile(ns, steps):
    step = steps['compile']
    kwargs = step.build_kwargs(Step.provided(ns), D.COMPILE)
    if D.FROM_INPUT_YPF_VERSION.get('compile') not in kwargs:
        with_ypf_version('compile', step, kwargs, detect_ypf_version(steps['extract'], ns.ypf_in))
    do_run('compile', step, 'yuricom.run', kwargs, ns.dry_run)


def _split(raw: str) -> tuple[str, str]:
    name, sep, value = raw.partition('=')
    if not sep or not name:
        raise CliError(f'use NAME=VALUE (got {raw!r})')
    return name.strip(), value


def cmd_all(ns, steps):
    overrides: dict[str, dict] = {c: {} for c in CORE}
    for raw in ns.set:  # applies to every step that has this parameter
        name, value = _split(raw)
        hit = [c for c, s in steps.items() if s.find(name)]
        if not hit:
            raise CliError(f'--set {name}: no step has this parameter')
        for c in hit:
            k, v = steps[c].parse_override(name, value)
            overrides[c][k] = v
    for c, items in (('extract', ns.ext), ('decompile', ns.dec), ('compile', ns.com)):
        for raw in items:  # step-specific: wins over --set
            k, v = steps[c].parse_override(*_split(raw))
            overrides[c][k] = v

    defaults = {c: dict(D.COMMAND_DEFAULTS[c]) for c in CORE}
    root = path.join(ns.extract_dir, D.YPF_ENTRY_DIR)
    for c, name in D.UNDER_EXTRACT_DIR.items():
        defaults[c][name] = root

    # Resolve every step first, so missing required parameters fail before any slow step runs.
    kws = {c: steps[c].build_kwargs(overrides[c], defaults[c]) for c in CORE}

    print('==> [1/3] extract')
    version = do_extract(steps['extract'], ns.ypf_in, ns.extract_dir, kws['extract'], ns.dry_run)
    print('==> [2/3] decompile')
    do_decompile(steps, kws['decompile'], ns.dry_run)
    print('==> [3/3] compile')
    with_ypf_version('compile', steps['compile'], kws['compile'], version)
    do_run('compile', steps['compile'], 'yuricom.run', kws['compile'], ns.dry_run)


# --- parser ------------------------------------------------------------------

def build_parser(steps: dict[str, Step]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m yuri.cli', allow_abbrev=False,
        description='CLI for yuri (decompiler/compiler for the Yu-Ris engine). '
                    'Use "COMMAND -h" to see all the options of each step.')
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    def new(cmd: str, help: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(cmd, help=help, description=help, allow_abbrev=False)
        sp.set_defaults(func=globals()['cmd_' + cmd])
        add_common(sp)
        return sp

    sp = new('extract', 'extract the files of a .ypf')
    sp.add_argument('--ypf-in', default=D.YPF_IN, help=f'input .ypf (main.py default: {D.YPF_IN})')
    sp.add_argument('--out-dir', default=D.YPF_EX, help=f'output folder (main.py default: {D.YPF_EX})')
    steps['extract'].add_arguments(sp, D.EXTRACT, RESERVED)

    sp = new('decompile', 'convert .ybn files into .yuri scripts (or into the official syntax)')
    steps['decompile'].add_arguments(sp, D.DECOMPILE, RESERVED)

    sp = new('compile', 'compile the .yuri scripts and build a new .ypf')
    sp.add_argument('--ypf-in', default=D.YPF_IN,
                    help=f'original .ypf, only used to detect the YPF version (main.py default: {D.YPF_IN})')
    notes = {n: 'if omitted, uses the version read from --ypf-in (as in main.py)'
             for n in D.FROM_INPUT_YPF_VERSION.values()}
    steps['compile'].add_arguments(sp, D.COMPILE, RESERVED, notes)

    sp = new('all', 'extract + decompile + compile in sequence (equivalent to running main.py)')
    sp.add_argument('--ypf-in', default=D.YPF_IN, help=f'input .ypf (main.py default: {D.YPF_IN})')
    sp.add_argument('--extract-dir', default=D.YPF_EX, help=f'extraction folder (main.py default: {D.YPF_EX})')
    for flag, what in (('--set', 'every step that has the parameter'), ('--ext', 'extract'),
                       ('--dec', 'decompile'), ('--com', 'compile')):
        sp.add_argument(flag, action='append', default=[], metavar='NAME=VALUE',
                        help=f'override a parameter of {what} (repeatable)')
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        steps = load_steps()
        ns = build_parser(steps).parse_args(argv)
        ns.func(ns, steps)
    except CliError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    return 0
