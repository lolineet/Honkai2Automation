"""Retry a current observation or navigation without replaying a task."""
from functools import wraps

from .cancellation import Cancelled
from .inventory import EquipmentFull


class RetryExhausted(RuntimeError):
    pass


def retry_call(operation, *, sleep, log, label):
    for attempt in range(1, 4):
        try:
            return operation()
        except (Cancelled, EquipmentFull, RetryExhausted):
            raise
        except RuntimeError as exc:
            log(f"{label}：第 {attempt}/3 次尝试失败：{exc}")
            if attempt == 3:
                raise RetryExhausted(f"{label}尝试 3 次仍失败，停止本轮运行：{exc}") from exc
            sleep(1.5)


def retry_lag(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        return retry_call(lambda: method(self, *args, **kwargs),
                          sleep=self.sleep, log=self.log, label=method.__name__)
    return wrapped
