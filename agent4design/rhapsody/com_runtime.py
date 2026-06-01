"""Run every Rhapsody COM operation on one dedicated STA thread."""

from __future__ import annotations

import atexit
import concurrent.futures
from collections.abc import Callable
from queue import Queue
import threading
from typing import Any, TypeVar

import pythoncom


T = TypeVar("T")
Task = tuple[Callable[[], Any] | None, concurrent.futures.Future[Any]]


class COMDispatcher:
    """Serialize COM work and keep Rhapsody objects inside one STA apartment."""

    def __init__(self, startup_timeout: float = 10.0) -> None:
        self._startup_timeout = startup_timeout
        self._lifecycle_lock = threading.RLock()
        self._queue: Queue[Task] = Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._stopping = False

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stopping

    def start(self) -> None:
        """Start the STA worker lazily. Repeated calls are safe."""
        with self._lifecycle_lock:
            if self.is_running:
                return
            if self._stopping:
                raise RuntimeError("COM dispatcher is stopping")

            self._queue = Queue()
            self._ready = threading.Event()
            self._startup_error = None
            self._thread = threading.Thread(target=self._loop, name="RhpCOM", daemon=True)
            self._thread.start()

        if not self._ready.wait(timeout=self._startup_timeout):
            raise RuntimeError("COM dispatcher failed to start before timeout")
        if self._startup_error is not None:
            raise RuntimeError("COM dispatcher failed to initialize") from self._startup_error

    def _loop(self) -> None:
        initialized = False
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                fn, future = self._queue.get()
                if fn is None:
                    future.set_result(None)
                    break
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    future.set_result(fn())
                except BaseException as exc:
                    future.set_exception(exc)
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def call(self, fn: Callable[[], T]) -> T:
        """Submit work to the STA thread and wait for its result."""
        if threading.current_thread() is self._thread:
            return fn()

        self.start()
        with self._lifecycle_lock:
            if not self.is_running:
                raise RuntimeError("COM dispatcher is not running")
            future: concurrent.futures.Future[T] = concurrent.futures.Future()
            self._queue.put((fn, future))
        return future.result()

    def stop(self) -> None:
        """Stop the worker after queued tasks finish. Repeated calls are safe."""
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                return
            if threading.current_thread() is thread:
                raise RuntimeError("COM dispatcher cannot stop itself")
            if self._stopping:
                return

            self._stopping = True
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            self._queue.put((None, future))

        future.result()
        thread.join()
        with self._lifecycle_lock:
            if self._thread is thread:
                self._thread = None
            self._stopping = False


com = COMDispatcher()


def run_on_com(fn: Callable[[], T]) -> T:
    """Run a callable on the shared Rhapsody COM thread."""
    return com.call(fn)


def stop_com_runtime() -> None:
    """Stop the shared runtime when the process exits."""
    com.stop()


atexit.register(stop_com_runtime)
