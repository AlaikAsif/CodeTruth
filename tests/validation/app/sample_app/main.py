"""Entry point (executable script)."""
from sample_app.core import run_pipeline
from sample_app.registry import dispatch


def main():
    print(run_pipeline(["a ", " b"]))
    print(dispatch("email", "hi"))


if __name__ == "__main__":
    main()
