from errno import ENOENT
from os import remove


def silentremove(filename):
    try:
        remove(filename)
    except OSError as e:
        if e.errno != ENOENT:
            raise
