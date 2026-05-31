import argparse


def greet(name: str) -> str:
    return f"Hello, {name}!"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    greet_parser = subparsers.add_parser("greet")
    greet_parser.add_argument("name")
    args = parser.parse_args(argv)

    if args.command == "greet":
        print(greet(args.name))


if __name__ == "__main__":
    main()
