"""CLI default values, taken from the example setup in ``main.py``.

These are just a convenient set of generic defaults (folders, encodings,
syntax). Game-specific values (YBN versions and the YSTB XOR key) are
intentionally NOT defaulted here: when the core can detect them (e.g. reading
the version from the file headers) the CLI lets it do so; otherwise the option
is reported as required.

The dictionaries use the PARAMETER NAMES of the core functions
(``yuridec.run`` and ``yuricom.run``). If the core gains new parameters, the
CLI exposes them on its own (with the core's own default); if the core
renames/removes a parameter listed here, the CLI warns on stderr and ignores
the entry.
"""
from os import path

# --- Paths (same as main.py) ---------------------------------------
YPF_IN = path.join('ypf', 'ysbin-input.ypf')             # original YPF of the game
YPF_EX = path.join('ybn', 'original')                    # extraction destination
YBN_IN = path.join(YPF_EX, 'ysbin')                      # extracted original .ybn files
YSB_OUT_OFFICIAL = path.join('ybn', 'decoded', 'official')      # (reference only; see README)
YSB_OUT_UNOFFICIAL = path.join('ybn', 'decoded', 'unofficial')  # decompiler output
YSB_OUT_UNOFFICIAL_TEMP = path.join('ybn', 'encoded', 'temp')   # temporary files
YPF_OUT = path.join('ypf', 'ysbin-output.ypf')           # recompiled YPF

# Folder inside the YPF that holds the .ybn files (path.join(YPF_EX, 'ysbin') == YBN_IN)
YPF_ENTRY_DIR = 'ysbin'

# --- Defaults per command: {core_parameter_name: value} ---------------------
EXTRACT: dict = {}   # main.py does not customize anything in ypf_read

DECOMPILE: dict = {
    'iroot': YBN_IN,
    'oroot': YSB_OUT_UNOFFICIAL,
    'ienc': 'cp932',
    'oenc': 'utf-8',
    'dcls': 'YDecYuri',        # custom YURI syntax (the official one would be 'YDecYuris')
    'mp_parallel': False,
    'also_dump': False,
}

COMPILE: dict = {
    'iroot': YSB_OUT_UNOFFICIAL,
    'wroot': YSB_OUT_UNOFFICIAL_TEMP,
    'troot': YBN_IN,
    'o_ypf': YPF_OUT,
    'i_enc': 'utf-8',
    'o_enc': 'cp932',
    'mp_parallel': False,
    'opt_custom_npar': False,  # ComOpts field (custom GOSUB_NPAR)
}

# --- Rules derived from main.py ---------------------------------------------
# In main.py, the compiler's ``ypf_ver`` is the version read from the input YPF.
# {command: parameter filled with the version detected from --ypf-in}
FROM_INPUT_YPF_VERSION = {'compile': 'ypf_ver'}

# In the ``all`` command, changing --extract-dir changes these defaults (like changing YPF_EX).
# {command: parameter that points to <extract-dir>/ysbin}
UNDER_EXTRACT_DIR = {'decompile': 'iroot', 'compile': 'troot'}

# Parameters the core detects on its own when they are not given. Only used to
# add a hint to errors raised by the core, since auto-detection can be wrong
# for custom builds. {command: parameter names}
AUTO_DETECTED = {'extract': ('v',), 'decompile': ('ver', 'key'), 'compile': ('t_ver',)}

COMMAND_DEFAULTS = {'extract': EXTRACT, 'decompile': DECOMPILE, 'compile': COMPILE}
