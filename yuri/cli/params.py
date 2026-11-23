"""Builds the CLI options from the SIGNATURES of the core functions.

Core idea: the CLI does not list parameters "by hand". It reads, with
``inspect``, the parameters of ``yuridec.run``, ``yuricom.run`` and
``ypf_read`` and creates a ``--parameter-name`` option for each one. So if the
core code gains, loses or changes the default of a parameter, the CLI follows
without edits.

How the value of each parameter is decided:
  1. value given on the command line;
  2. default from ``main.py`` (yuri/cli/defaults.py);
  3. the core code's own default (the parameter is simply not sent);
  4. if none exists, the parameter is required.
"""
from __future__ import annotations

import argparse
import dataclasses
import inspect
import sys
import types
import typing
from typing import Any, Callable

MISSING = dataclasses.MISSING


class CliError(Exception):
    """CLI usage error (message already meant for the user)."""


# --- text -> value converters ------------------------------------------

def _err(msg: str) -> argparse.ArgumentTypeError:
    return argparse.ArgumentTypeError(msg)


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ('1', 'true', 'yes', 'y', 'on', 'sim', 's'):
        return True
    if s in ('0', 'false', 'no', 'n', 'off', 'nao'):
        return False
    raise _err(f'invalid boolean: {v!r} (use true/false)')


def _to_int(v: Any) -> int:
    if isinstance(v, int):
        return v
    try:
        return int(str(v), 0)  # accepts 480, 0x1E0, 0b1...
    except ValueError:
        raise _err(f'invalid integer: {v!r} (decimal or 0x...)') from None


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except ValueError:
        raise _err(f'invalid number: {v!r}') from None


def _to_str(v: Any) -> str:
    return v if isinstance(v, str) else str(v)


def _load_yscd(v: Any):
    """YSCD (official compiler .ycd): the user passes the file path."""
    if not isinstance(v, str):
        return v
    from yuri.fileformat import YSCD, Rdr  # late import: only when used
    try:
        with open(v, 'rb') as fp:
            return YSCD.read(Rdr.from_bio(fp))
    except OSError as e:
        raise _err(f'could not open {v!r}: {e}') from None


# "Object" types that the user provides as a file path.
# Key = class NAME (string), to avoid coupling to a core import.
FILE_LOADERS: dict[str, Callable[[Any], Any]] = {'YSCD': _load_yscd}


def _all_subclasses(base: type) -> list[type]:
    out: list[type] = []
    for sub in base.__subclasses__():
        out.append(sub)
        out.extend(_all_subclasses(sub))
    return sorted(set(out), key=lambda c: c.__name__) or [base]


@dataclasses.dataclass
class Conv:
    fn: Callable[[Any], Any]
    kind: str                       # bool | int | float | str | class | file
    choices: list[str] | None = None


def _candidates(ann: Any) -> list[Any]:
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        return [a for a in typing.get_args(ann) if a is not type(None)]
    return [ann]


def _single(a: Any) -> Conv | None:
    if a is bool:
        return Conv(_to_bool, 'bool')
    if a is int:
        return Conv(_to_int, 'int')
    if a is float:
        return Conv(_to_float, 'float')
    if a is str:
        return Conv(_to_str, 'str')
    if typing.get_origin(a) is type and typing.get_args(a):   # type[Base]
        classes = {c.__name__.lower(): c for c in _all_subclasses(typing.get_args(a)[0])}

        def to_class(v: Any):
            if isinstance(v, type):
                return v
            try:
                return classes[str(v).lower()]
            except KeyError:
                raise _err(f'invalid option: {v!r} (use: {", ".join(c.__name__ for c in classes.values())})') from None
        return Conv(to_class, 'class', [c.__name__ for c in classes.values()])
    if isinstance(a, type) and a.__name__ in FILE_LOADERS:
        return Conv(FILE_LOADERS[a.__name__], 'file')
    return None


def converter_for(ann: Any) -> Conv | None:
    for a in _candidates(ann):
        conv = _single(a)
        if conv:
            return conv
    return None


# --- parameters --------------------------------------------------------------

@dataclasses.dataclass
class Param:
    name: str
    conv: Conv | None               # None = type the CLI cannot receive
    default: Any = MISSING          # core default
    group: str | None = None        # source dataclass parameter (e.g. 'opts')

    @property
    def flag(self) -> str:
        return '--' + self.name.replace('_', '-')

    @property
    def required(self) -> bool:
        return self.default is MISSING


def _fmt(v: Any) -> str:
    if isinstance(v, type):
        return v.__name__
    if isinstance(v, int) and not isinstance(v, bool) and v > 0xFFFF:
        return hex(v)
    return repr(v)


def _signature(fn: Callable) -> inspect.Signature:
    try:
        return inspect.signature(fn, eval_str=True)
    except Exception:
        return inspect.signature(fn)


class Step:
    """A core function exposed as a command (decompile, compile, ...)."""

    def __init__(self, fn: Callable, skip_first: bool = False):
        self.fn = fn
        self.params: list[Param] = []
        self.groups: dict[str, type] = {}   # param name -> dataclass
        items = list(_signature(fn).parameters.items())
        for i, (name, p) in enumerate(items):
            if (skip_first and i == 0) or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            default = MISSING if p.default is p.empty else p.default
            self._add(name, p.annotation, default, p.empty)

    def _add(self, name: str, ann: Any, default: Any, empty: Any) -> None:
        if ann is empty:  # no annotation: try to infer it from the default value
            ann = type(default) if default not in (MISSING, None) else None
        conv = converter_for(ann)
        if conv:
            self.params.append(Param(name, conv, default))
            return
        for a in _candidates(ann):  # a dataclass (e.g. ComOpts) becomes one option per field
            if isinstance(a, type) and dataclasses.is_dataclass(a):
                hints = typing.get_type_hints(a)
                self.groups[name] = a
                for f in dataclasses.fields(a):
                    if not f.init:
                        continue
                    if f.default is not MISSING:
                        fd = f.default
                    elif f.default_factory is not MISSING:
                        fd = f.default_factory()
                    else:
                        fd = MISSING
                    self.params.append(Param(f.name, converter_for(hints.get(f.name, f.type)), fd, group=name))
                return
        self.params.append(Param(name, None, default))

    # -- lookup --
    @property
    def names(self) -> set[str]:
        return {p.name for p in self.params}

    def find(self, name: str) -> Param | None:
        name = name.replace('-', '_')
        return next((p for p in self.params if p.name == name), None)

    def parse_override(self, name: str, raw: str) -> tuple[str, Any]:
        p = self.find(name)
        if p is None or p.conv is None:
            valid = ', '.join(sorted(q.name for q in self.params if q.conv))
            raise CliError(f'unknown parameter: {name!r}. Valid: {valid}')
        try:
            return p.name, p.conv.fn(raw)
        except argparse.ArgumentTypeError as e:
            raise CliError(f'{p.name}: {e}') from None

    # -- argparse --
    def add_arguments(self, parser: argparse.ArgumentParser, defaults: dict, reserved: set[str],
                      notes: dict[str, str] | None = None) -> None:
        group = parser.add_argument_group(
            f'parameters of {self.fn.__module__}.{self.fn.__name__} (generated from the core code)')
        for p in self.params:
            if p.conv is None:
                continue
            flag = p.flag if p.flag not in reserved else '--core-' + p.flag[2:]
            if notes and p.name in notes:
                info = notes[p.name]
            elif p.name in defaults:
                info = f'main.py default: {_fmt(p.conv.fn(defaults[p.name]))}'
            elif p.default is not MISSING:
                info = f'core default: {_fmt(p.default)}'
            else:
                info = 'REQUIRED'
            extra = f' [{", ".join(p.conv.choices)}]' if p.conv.choices else ''
            kw: dict[str, Any] = dict(dest='p_' + p.name, default=argparse.SUPPRESS,
                                      help=f'{p.conv.kind}{extra}; {info}'.replace('%', '%%'))
            if p.conv.kind == 'bool':
                group.add_argument(flag, action=argparse.BooleanOptionalAction, **kw)
            else:
                group.add_argument(flag, type=p.conv.fn, metavar=p.name.upper(), **kw)

    @staticmethod
    def provided(ns: argparse.Namespace) -> dict[str, Any]:
        return {k[2:]: v for k, v in vars(ns).items() if k.startswith('p_')}

    # -- final resolution --
    def build_kwargs(self, provided: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        fields: dict[str, dict[str, Any]] = {g: {} for g in self.groups}
        missing: list[str] = []
        for p in self.params:
            if p.name in provided:
                val = provided[p.name]
            elif p.name in defaults and p.conv:
                try:
                    val = p.conv.fn(defaults[p.name])
                except argparse.ArgumentTypeError as e:
                    raise CliError(f'default of {p.name} in defaults.py: {e}') from None
            elif p.required:
                missing.append(p.flag if p.conv else f'{p.name} (type not supported by the CLI)')
                continue
            else:
                continue  # let the core code use its own default
            (fields[p.group] if p.group else kwargs)[p.name] = val
        if missing:
            raise CliError('missing required parameters (there is no default for them): '
                           + ', '.join(missing))
        for g, cls in self.groups.items():
            if fields[g]:
                kwargs[g] = cls(**fields[g])
        for stale in sorted(set(defaults) - self.names):
            print(f'warning: the default for {stale!r} in yuri/cli/defaults.py was ignored '
                  f'({self.fn.__name__} no longer has that parameter)', file=sys.stderr)
        return kwargs


def format_call(label: str, kwargs: dict[str, Any], first: str | None = None) -> str:
    lines = [f'{label}(']
    if first:
        lines.append(f'    {first},')
    lines += [f'    {k}={_fmt(v)},' for k, v in kwargs.items()]
    lines.append(')')
    return '\n'.join(lines)
