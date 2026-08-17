from __future__ import annotations

import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    test_fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_fn):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(test_fn(**kwargs))
    return True
