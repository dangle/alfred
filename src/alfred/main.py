import os

import dotenv
import structlog


def configure() -> None:
    dotenv.load_dotenv()

    structlog.configure(
        processors=[
            structlog.processors.JSONRenderer()
        ]
    )


def run() -> None:
    pass


if __name__ == '__main__':
    configure()
    run()
