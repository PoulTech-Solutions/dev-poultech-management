from app.core.exceptions import AppError


class InvalidPincodeError(AppError):
    status_code = 422
    code = 'invalid_pincode'
    message = 'Select a valid India Post pincode from the list'
