import gc
import os
import psutil
import threading

from functools import wraps
from typing import Callable


class _PeakRSSTracker:
    """Background daemon thread that samples process RSS to capture peak memory.
    It is used to track CPU memory usage when DEVICE_ENV is set to "cpu".
    """

    def __init__(self, interval: float = 0.1):
        self._process = psutil.Process(os.getpid())
        self._interval = interval
        self._peak_mb: float = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)

    def _poll(self) -> None:
        # samples RSS every `interval` seconds
        while not self._stop.is_set():
            rss_mb = self._process.memory_info().rss / 1024**2
            if rss_mb > self._peak_mb:
                self._peak_mb = rss_mb
            self._stop.wait(self._interval)

    def start(self) -> float:
        """Start tracking and return initial RSS in MB."""
        initial = self._process.memory_info().rss / 1024**2
        self._peak_mb = initial
        self._thread.start()
        return initial

    def stop(self) -> tuple:
        """Stop tracking and return (final_mb, peak_mb)."""
        self._stop.set()
        self._thread.join()
        final = self._process.memory_info().rss / 1024**2
        if final > self._peak_mb:
            self._peak_mb = final
        return final, self._peak_mb


def track_cpu_memory(func: Callable) -> Callable:
    """Track CPU memory usage (RSS) around a function call."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        gc.collect()
        # record internal 50ms
        tracker = _PeakRSSTracker(interval=0.05)
        initial_memory = tracker.start()
        print(f"[{func.__name__}] Initial CPU memory (RSS): {initial_memory:.2f} MB")

        result = func(*args, **kwargs)

        gc.collect()
        final_memory, peak_memory = tracker.stop()
        memory_used = peak_memory - initial_memory

        print(f"[{func.__name__}] Peak CPU memory (RSS): {peak_memory:.2f} MB")
        print(f"[{func.__name__}] Final CPU memory (RSS): {final_memory:.2f} MB")
        print(f"[{func.__name__}] CPU Memory used: {memory_used:.2f} MB")

        return result

    return wrapper


def track_gpu_memory(func: Callable) -> Callable:
    """Track GPU memory usage around a function call."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        # lazy import
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            initial_memory = torch.cuda.memory_allocated() / 1024**2  # MB
            max_memory_before = torch.cuda.max_memory_allocated() / 1024**2

            print(f"[{func.__name__}] Initial GPU memory: {initial_memory:.2f} MB")

            result = func(*args, **kwargs)

            peak_memory = torch.cuda.max_memory_allocated() / 1024**2  # MB
            final_memory = torch.cuda.memory_allocated() / 1024**2
            memory_used = peak_memory - max_memory_before

            print(f"[{func.__name__}] Peak GPU memory: {peak_memory:.2f} MB")
            print(f"[{func.__name__}] Final GPU memory: {final_memory:.2f} MB")
            print(f"[{func.__name__}] GPU Memory used: {memory_used:.2f} MB")

            # Reset peak memory tracking
            torch.cuda.reset_peak_memory_stats()
        else:
            result = func(*args, **kwargs)

        return result

    return wrapper
