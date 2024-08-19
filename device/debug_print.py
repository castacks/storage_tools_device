#!/usr/bin/env python3

import os 
import sys 
import datetime 
import inspect
from typing import TextIO 

def debug_print(string, file:TextIO=sys.stderr):
    """ Print a debug message to the console (or file object)

    Prints the string as "DEBUG :: {time} :: {calling_script} :: {calling_fun} :: string

    Args:
        string (str): Any string
        file (TextIO, optional): Destination of output. Defaults to sys.stderr.
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    calling_script = os.path.basename(inspect.stack()[1].filename)
    calling_fn = inspect.stack()[1].function
    line = inspect.stack()[1].lineno
    print(f"DEBUG :: {now} :: {calling_script}:{line} :: {calling_fn} :: {string} ", file=file)   
    file.flush()



def debug_prefix():
    calling_script = os.path.basename(inspect.stack()[1].filename)
    calling_fn = inspect.stack()[1].function
    line = inspect.stack()[1].lineno

    return f"{calling_script}:{line} :: {calling_fn} :: "
