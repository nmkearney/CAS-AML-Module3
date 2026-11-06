"""ChatCDE: a local research assistant over CDE's publications (prototype)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("chatcde")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0+unknown"
