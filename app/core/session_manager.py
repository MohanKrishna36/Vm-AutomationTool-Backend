class SessionRuntime:
    def __init__(self, ssh, channel):
        self.ssh = ssh
        self.channel = channel


SESSION_STORE: dict[str, SessionRuntime] = {}
