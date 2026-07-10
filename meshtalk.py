# DEPRECATED NAME: MeshTalk was renamed (back) to MeshSpeak. Compat shim -> meshspeak.
from meshspeak import *  # noqa
import meshspeak as _ms, sys as _sys
_sys.modules[__name__].__dict__.update(
    {k: v for k, v in vars(_ms).items() if not k.startswith("__")})
MeshTalkSession = MeshSpeakSession        # deprecated class aliases
MeshTalkSTS = MeshSpeakSTS
