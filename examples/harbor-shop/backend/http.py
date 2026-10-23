"""Tiny decorator that marks a function as an HTTP route."""


def route(method: str, path: str):
    def decorator(fn):
        fn.http_method = method
        fn.http_path = path
        return fn

    return decorator
