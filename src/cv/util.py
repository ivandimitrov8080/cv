from os import remove
from errno import ENOENT

def silentremove(filename):
    try:
        remove(filename)
    except OSError as e:
        if e.errno != ENOENT:
            raise
