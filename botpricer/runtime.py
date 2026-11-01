import os


class InstanceLock:
    def __init__(self, path):
        self.path, self.file = path, None

    def acquire(self):
        self.file = open(self.path, 'a+b')
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('BotPricer уже запущен с этой папкой данных. Откройте http://127.0.0.1:8000') from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
