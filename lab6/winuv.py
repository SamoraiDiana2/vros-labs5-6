"""Обхід збірки PyTorch для Windows (win64) без підтримки libuv.

Частина коду `torch.distributed` на Windows створює `TCPStore` із типовим
`use_libuv=True` і завершується з `DistStoreError: use_libuv was requested but
PyTorch was built without libuv support`.

Модуль підміняє `TCPStore` обгорткою з `use_libuv=False` у всіх точках входу
`torch.distributed` та `torch.distributed.elastic` на платформі `win32` (win64).
"""

import importlib
import os
import sys

from torch.distributed import TCPStore as _TCPStore


def _tcp_store(*args, **kwargs):
    kwargs.setdefault("use_libuv", False)
    return _TCPStore(*args, **kwargs)


def active():
    return sys.platform == "win32"


def _apply_tcpstore_patch():
    if not active():
        return False

    os.environ["USE_LIBUV"] = "0"

    import torch.distributed as dist

    dist.TCPStore = _tcp_store

    rdzv = importlib.import_module("torch.distributed.rendezvous")
    rdzv.TCPStore = _tcp_store

    for mod_name in (
        "torch.distributed.elastic.rendezvous.c10d_rendezvous_backend",
        "torch.distributed.elastic.rendezvous.static_tcp_rendezvous",
    ):
        try:
            mod = importlib.import_module(mod_name)
            mod.TCPStore = _tcp_store
        except ImportError:
            pass

    return True


def patch_worker():
    """Патч для процесу-воркера навчання перед dist.init_process_group()."""
    return _apply_tcpstore_patch()


def patch_agent():
    """Патч для обгортки агента elastic (torchrun)."""
    return _apply_tcpstore_patch()