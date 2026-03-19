import os


def log(filename, text):
    """Append a line to <filename>.design.log alongside the board file."""
    log_path = filename + ".design.log"
    with open(log_path, "a") as f:
        f.write(text + "\n")
