class UserError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(UserError):
    pass


class StaleRevision(UserError):
    pass


class NeedsConfirmation(UserError):
    def __init__(self, message: str, affected: int):
        super().__init__(message)
        self.affected = affected
