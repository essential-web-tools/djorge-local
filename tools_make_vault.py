"""Build step: embeds the model key in the executable (djorge_local/_vault.py, never committed).

    DJORGE_MODEL_KEY=... python tools_make_vault.py

The key is split into three random shares and scrambled, so it is not a plain string inside the binary.
This raises the bar against casual extraction; it cannot stop a determined reverse engineer with a debugger
(no local scheme can: the program itself must be able to open the models)."""
import base64
import os
import secrets
import sys

k = os.environ.get("DJORGE_MODEL_KEY", "").encode()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "djorge_local", "_vault.py")
if len(k) < 10:
    print("DJORGE_MODEL_KEY ausente: o executável vai pedir a chave na aba Modelos")
    if os.path.exists(out):
        os.remove(out)
    sys.exit(0)
a, b = secrets.token_bytes(len(k)), secrets.token_bytes(len(k))
c = bytes(x ^ y ^ z for x, y, z in zip(k, a, b))
perm = list(range(len(k)))
secrets.SystemRandom().shuffle(perm)
enc = lambda v: base64.b85encode(bytes(v[i] for i in perm)).decode()
inv = [0] * len(perm)
for i, p in enumerate(perm):
    inv[p] = i
open(out, "w").write(
    "import base64 as _b\n"
    f"_s=({enc(a)!r},{enc(c)!r},{enc(b)!r})\n_p={inv!r}\n"
    "def k():\n"
    "    x,y,z=[_b.b85decode(s) for s in _s]\n"
    "    r=bytes(p^q^w for p,q,w in zip(x,y,z))\n"
    "    return bytes(r[i] for i in _p).decode()\n")
print("chave embutida em", out)
