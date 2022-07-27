import os
import re
import random
import string
import tempfile

WINDOWS = (os.name == 'nt')

CODE_DIR = os.path.dirname(os.path.realpath(__file__))

# BEWARE !!!!
def inf_gen(start: int = 0, step: int = 1):
    i = start
    while True:
        yield i
        i += step

def get_tempfile(prefix: str = None, suffix: str = None, random_len: int = 32) -> str:
    charset = string.ascii_letters + string.digits
    while True:
        random_str = ''.join(random.choice(charset) for _ in range(random_len))
        path = os.path.join(tempfile.gettempdir(), f"{prefix if prefix else ''}{random_str}{suffix if suffix else ''}")
        if not os.path.exists(path):
            return path

def fix_fn(filename: str) -> str:
    return re.sub(r"[\\\/\*\<\?\>\|\<\>\:\"]", "-", filename)

def unique_path(path: str, ext: bool = True) -> str:
    if not os.path.exists(path):
        return path

    if ext:
        bn, _, e = os.path.basename(path).rpartition('.')
        d = os.path.dirname(path)
        template = f"{d}/{bn} ({{}}).{e}"
    else:
        template = f"{path} ({{}})"

    for i in inf_gen(1):
        p = template.format(i)
        if not os.path.exists(p):
            return p
