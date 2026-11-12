import time


def wait_for(pred, timeout: float = 2.0, step: float = 0.002) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())
