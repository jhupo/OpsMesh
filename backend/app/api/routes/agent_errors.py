from fastapi import HTTPException, status


def agent_management_http_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lower_message = message.lower()
    if "not found" in lower_message:
        code = status.HTTP_404_NOT_FOUND
    elif "conflict" in lower_message:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)
