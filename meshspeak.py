# DEPRECATED NAME: MeshSpeak was renamed to MeshTalk (MT). Compat shim -> meshtalk.
from meshtalk import *  # noqa
import meshtalk as _mt, sys as _sys
_sys.modules[__name__].__dict__.update({k: v for k, v in vars(_mt).items() if not k.startswith("__")})
